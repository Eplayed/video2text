# -*- coding: utf-8 -*-
"""humanize_routes.py — 整理稿「去 AI 味」的两步接口：先预览，人点应用才写库

为什么单独一个文件（而不是长在 web/app.py 里）：app.py 常被多个会话同时改，
上一轮我的端点与别人的改动相邻、git 合成同一个 hunk，hunk 级拆分失败过一次。
抽成 blueprint 后 app.py 只需要 2 行，撞面从 140 行降到 2 行。

为什么是两步：定点改写的全部价值在「其余一字不动」，让模型整篇重发就变回重写
（用户自己那份公众号审计已实证：多版重写＝AI 味放大器）。业界同构做法是 textlint 的 --fix：
默认只报告，改文件是一个显式动作。预览只存内存，工作台重启即失效（接口回 410，界面提示重跑）。
"""
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from src import content_store, draft_audit, humanize, review_store

_lock = threading.Lock()
_status = {"running": False, "stage": "", "error": "", "summary_id": None, "started_at": ""}
_t0 = [0.0]
_preview: dict = {}


def _brief(a):
    return {"verdict": a.get("verdict"), "label": a.get("label"),
            "fails": a.get("fails"), "warns": a.get("warns"), "score": a.get("score"),
            "prose_chars": a.get("prose_chars"),
            "rows": [[lv, nm, dt] for lv, nm, dt in (a.get("rows") or [])
                     if lv in ("FAIL", "WARN")]}


def make_blueprint(db_path, output_dir, ai_config_fn, db_lock, logger):
    """依赖注入建 blueprint：app.py 那边只负责传自己的 DB_PATH / OUTPUT_DIR / _ai_config / 锁 / logger。"""
    bp = Blueprint("humanize", __name__)

    def audit_of(md, sources):
        return draft_audit.audit(md_text=md, sources_text=sources or None, use_verify=False)

    def backup_summary(summary_id, old_md):
        """应用前把原稿存一份：改写可以回退，不靠"我记得改了什么"。"""
        d = Path(output_dir) / "整理稿备份"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s-%s.md" % (summary_id, datetime.now().strftime("%Y%m%d-%H%M%S")))
        path.write_text(old_md, encoding="utf-8")
        return str(path)

    @bp.route("/api/content/humanize", methods=["POST"])
    def api_humanize_preview():
        """跑一次定点改写预览（不写库）。retry 传上一轮被拒条目，只让它们重做。"""
        global _preview
        data = request.get_json(force=True)
        sid = int(data.get("summary_id") or 0)
        retry = data.get("retry") or []
        with _lock:
            if _status["running"]:
                return jsonify({"error": "已有一次改写在跑，等它结束"}), 409
            _status.update({"running": True, "stage": "读整理稿…", "error": "", "summary_id": sid,
                            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        _t0[0] = time.time()

        def worker():
            global _preview
            try:
                summary = content_store.get_summary(db_path, sid)
                md = (summary or {}).get("content") or ""
                if not md.strip():
                    raise ValueError("整理稿是空的，没什么可改")
                sources = content_store.material_source_text(db_path, summary)
                before = audit_of(md, sources)
                _status["stage"] = "模型定点改写中（主模型思考，通常 2-4 分钟）…"
                res = humanize.humanize(md, before["rows"], ai_config_fn(), retry=retry)
                if not res["ok"]:
                    _status["error"] = res["error"]
                    return
                _status["stage"] = "复检预览结果…"
                after = audit_of(res["new_md"], sources)
                _preview = {
                    "summary_id": sid, "fingerprint": review_store.fingerprint(md),
                    "edits": res["applied"], "rejected": res["rejected"],
                    "drift": res["drift"], "rules_from": res["rules_from"],
                    "note": "模型认为这篇没有可改的句子（体检清单里那些点在它看来不需要动）"
                            if res.get("nothing") else "",
                    "before": _brief(before), "after": _brief(after),
                    "preview_md": res["new_md"],
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception as e:
                logger.error("去 AI 味预览失败：%s", e.__class__.__name__)
                logger.exception("去 AI 味预览失败")
                _status["error"] = str(e) or e.__class__.__name__
            finally:
                # 状态机不许出现"跑完了但既没预览也没报错"——那是最难查的一种静默失效
                if not _status["error"] and not _preview:
                    _status["error"] = ("改写线程结束但没交出结果，也没抛异常（多半是模型返回空）。"
                                        "整理稿未改动。")
                    logger.error("去 AI 味线程空手退出：summary_id=%s", sid)
                _status["running"] = False
                _status["stage"] = ""

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"started": True})

    @bp.route("/api/content/humanize/status")
    def api_humanize_status():
        out = dict(_status)
        out["elapsed"] = int(time.time() - _t0[0]) if _t0[0] and _status["running"] else 0
        out["preview"] = _preview or None
        return jsonify(out)

    @bp.route("/api/content/humanize/discard", methods=["POST"])
    def api_humanize_discard():
        global _preview
        _preview = {}
        return jsonify({"ok": True})

    @bp.route("/api/content/humanize/apply", methods=["POST"])
    def api_humanize_apply():
        """把界面上勾选的替换落到整理稿：先校验指纹，再备份，再写库，最后复检。"""
        global _preview
        data = request.get_json(force=True)
        sid = int(data.get("summary_id") or 0)
        keep = data.get("keep")
        if not _preview or _preview.get("summary_id") != sid:
            return jsonify({"error": "没有可应用的预览（已过期或工作台重启过），请重新点「去 AI 味」"}), 410
        summary = content_store.get_summary(db_path, sid)
        md = (summary or {}).get("content") or ""
        if review_store.fingerprint(md) != _preview.get("fingerprint"):
            return jsonify({"error": "预览之后这篇整理稿又被改过（多半是重新生成过）。"
                                     "为防覆盖，本次应用已拒绝——请重新点一次「去 AI 味」。"}), 409
        idx = set(keep) if isinstance(keep, list) else None
        chosen = [e for i, e in enumerate(_preview.get("edits") or []) if idx is None or i in idx]
        if not chosen:
            return jsonify({"error": "一条都没勾，没有要应用的内容"}), 400
        new_md, applied, rejected = humanize.apply_edits(md, chosen)
        if rejected:
            return jsonify({"error": "有 %d 条在应用时定位不到原文（稿子被改过？），本次整批不落盘"
                                      % len(rejected)}), 409
        d = humanize.drift(md, new_md)
        if abs(d) > humanize.MAX_LEN_DRIFT:
            return jsonify({"error": "勾选后字数变化 %+.0f%%，超过 ±%d%% 红线，不落盘"
                                      % (d * 100, int(humanize.MAX_LEN_DRIFT * 100))}), 409
        bak = backup_summary(sid, md)
        with db_lock:
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE ai_summaries SET content=?, updated_at=? WHERE id=?",
                             (new_md, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid))
                conn.commit()
            finally:
                conn.close()
        sources = content_store.material_source_text(db_path, content_store.get_summary(db_path, sid))
        after = _brief(audit_of(new_md, sources))
        _preview = {}
        logger.info("整理稿 %s 去 AI 味：应用 %d 处，字数 %+.1f%%，备份 %s", sid, len(applied), d * 100, bak)
        return jsonify({"ok": True, "applied": len(applied), "drift": d, "bak": bak, "after": after})

    return bp
