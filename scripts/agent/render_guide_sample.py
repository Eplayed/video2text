# -*- coding: utf-8 -*-
"""render_guide_sample.py — 把一版**改过的攻略文案**出成样稿，交付给人看，不碰线上成品。

为什么要单独一条脚本（2026-10-21 Codex 复核退回来的第四关）：83 的最终样稿要交
「三页以内海报 + 每页实际用图 + 判帧记录 + 文案对照」。而 `output/poster/83/` 是
**已经发出去的旧成品**，直接走工作台会把带缺陷的那版覆盖掉——旧成品必须原样留着当对照。
所以这里把 `ai_poster.OUTPUT_DIR` 指到样稿目录，并且：
· **目标目录落在 `output/poster/` 里就拒绝运行**（这条是硬闸，不靠"我记得没写"）；
· 出图前后各算一次线上 `output/poster/<id>/manifest.json` 的 sha256，跑完必须一样；
· 事实闸有硬伤（待核事实进卖点）**默认拒绝出图**——样稿的正确解法是改掉卖点那句话，
  不是拿 `force` 把它放行成海报（放行要逐条凭据，那是另一条路径，这里不代做决定）；
· `--judge` 会把这一版真用到的那几张帧**重新判一遍**（真看图模型、花一点 token），
  帧文件先复制进样稿目录再判，生产的 `frames.json` 与原帧/clean 帧都不动。
  旧缓存里的 `clean=true` 不能替这次的目视——判帧那条链 10-21 刚修过一个
  "空回答被当成干净帧"的 P0（`src/frame_pool.py:apply_probe`）。

用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 \\
      scripts/agent/render_guide_sample.py --summary 83 \\
      --plan temp/plan83_revised.json --out output/样稿/2026-10-21-83 --judge
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))


def sha(path):
    if not os.path.isfile(str(path)):
        return ""
    with open(str(path), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def asr_for(summary, db_path):
    """这篇整合稿的全部原转写（多源按 source_video_ids 串起来）。

    为什么要脚本自己拼：`generate_poster` 里的行动依据闸退而用 `summary["transcript"]`，
    那是**单条**视频的字幕；`toutiao_mix` 是多源稿，只拿一条去定位等于把其它源的每条建议
    都判成「转写里查无」（2026-10-22 工作台那条同一口径改成拼全部源）。
    """
    import sqlite3
    ids = [x for x in (summary.get("source_video_ids") or []) if x] if isinstance(
        summary.get("source_video_ids"), list) else []
    if not ids:
        try:
            ids = json.loads(summary.get("source_video_ids") or "[]")
        except Exception:
            ids = []
    out = []
    if ids:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        for vid in ids:
            row = con.execute("SELECT transcript FROM videos WHERE id=?", (vid,)).fetchone()
            if row and row[0]:
                out.append(str(row[0]))
        con.close()
    return "\n".join(out) or str(summary.get("transcript") or "")


def load_ai_config():
    import app
    return app._fast_config()


def judge_used(pages, cfg, work_dir):
    """对这一版真用到的帧重新判一遍：复制帧 → 真调用 → 返回逐帧记录（不写生产库）。

    帧先复制进样稿目录再判：`_probe_item` 认为能裁时会在**文件旁边**写成品帧，
    指向生产池就会往 `output/assets/frames/` 里落新文件——那条目录是线上素材账，不动。
    """
    from src import frame_pool as fp
    recs = []
    for n, pg in enumerate(pages or [], 1):
        for ph in (pg.get("photos") or []):
            src = ph.get("path") or ""
            if not src or not os.path.exists(src):
                recs.append({"page": n, "frame": ph.get("frame"), "error": "帧文件不存在"})
                continue
            dst = work_dir / ("judge_%s" % os.path.basename(src))
            shutil.copyfile(src, str(dst))
            item = {"i": ph.get("frame"), "ts": ph.get("ts") or "", "file": str(dst),
                    "raw": str(dst), "band": None, "checked": False, "clean": None,
                    "subject": "", "why": ""}
            spent = fp._probe_item(item, cfg)
            recs.append({"page": n, "frame": ph.get("frame"), "ts": ph.get("ts") or "",
                         "src_file": src, "judged_file": item.get("file"),
                         "clean": item.get("clean"), "checked": item.get("checked"),
                         "judge_failed": bool(item.get("judge_failed")),
                         "subject": item.get("subject") or "", "why": item.get("why") or "",
                         "words": item.get("words") or [], "tokens": spent})
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", type=int, required=True)
    ap.add_argument("--plan", required=True, help="含 plan 与 publish 两份字段的 JSON")
    ap.add_argument("--out", required=True, help="样稿目录（不许落在 output/poster 里）")
    ap.add_argument("--judge", action="store_true", help="对用到的帧重新判一遍（真调用，花少量钱）")
    ap.add_argument("--force", action="store_true",
                    help="硬伤也出图（只用于人已经逐条核过并另留凭据的情况）")
    ap.add_argument("--prov", default="",
                    help="编辑证据链 JSON（question/contribution/contribution_evidence/claims/materials）；"
                         "不传就如实按「证据链没建」出，publishable 会是 false——**不替号主编凭据**")
    ap.add_argument("--allow-overflow", action="store_true",
                    help="行动依据闸判越界也照样出图（默认不出：样稿要证明的就是这条拦得住）")
    ap.add_argument("--check", action="store_true",
                    help="只跑闸口与字数体检，不出图（改文案时先这条，几十毫秒）")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    prod_poster = (ROOT / "output" / "poster").resolve()
    if prod_poster in out.parents or out == prod_poster:
        print("REFUSED: 样稿目录不能落在 %s 里（那是已发成品，必须原样留着）" % prod_poster)
        return 2
    body = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    plan = body.get("plan") or {}
    publish = body.get("publish") or {}
    if not plan.get("pages") and not plan.get("title"):
        print("REFUSED: plan 里没有文案")
        return 2

    from src import ai_poster
    from src import content_store
    from src import fact_gate
    import app

    prod_mf = prod_poster / str(a.summary) / "manifest.json"
    before = sha(prod_mf)
    summary = content_store.get_summary(app.DB_PATH, int(a.summary))
    if not summary:
        print("REFUSED: 整合稿 %d 不存在" % a.summary)
        return 2
    hard, soft = fact_gate.check(ai_poster._publish_view(plan, publish), summary.get("content") or "")
    print("事实闸：硬伤 %d 条 · 要人看一眼 %d 条" % (len(hard), len(soft)))
    for h in hard:
        print("   [硬] " + h[:120])
    for s in soft:
        print("   [看] " + s[:120])
    gaps = fact_gate.guide_gaps(plan) if ai_poster.plan_is_guide(plan) else []
    print("攻略五段结构缺：%s" % ("、".join(gaps) or "无"))
    cut = fact_gate.copy_is_cut(publish.get("copy_text") or "")
    print("配文截断：%s" % (cut or "无"))
    bad = ai_poster.lint_plan(plan, ai_poster.limits_for("typeset"))
    print("上图字数体检：%s" % ("；".join(bad) or "过线"))

    # ── 行动依据闸（2026-10-22 那一单）：先离线跑一遍，越界就不出图 ──────────
    # 这层判的是"玩家照着这条做，做法在不在原转写里"。样稿要交付的正是
    # 「含混的项名与没核的档位停在行动之前」，所以默认**拦下就不渲染**：
    # 拿 --allow-overflow 把越界的样稿画出来，等于把验收对象变成待验对象。
    from src import action_gate
    asr = asr_for(summary, app.DB_PATH)
    rep = action_gate.check(summary.get("content") or "", asr, material_id=summary.get("id"))
    over = action_gate.poster_block(rep["rows"], plan)
    print("行动依据闸：建议 %d 条 · 可用 %d · 待核 %d · 这篇拿到的转写 %d 字" % (
        rep["stats"]["rows"], rep["stats"]["ok"], rep["stats"]["pending"], len(asr)))
    for line in over:
        print("   [越界] " + line[:130])
    if a.check:
        print("（--check：只看闸口与字数，没出图）")
        return 3 if (hard or over) else 0
    if over and not a.allow_overflow:
        print("\n有待核行动建议印到了卖点位置，不出样稿：把那句从标题/大字位挪走，"
              "或先回听/进游戏核对并把出处与结论记进待核清单。")
        return 3
    if a.prov:
        prov = json.loads(Path(a.prov).read_text(encoding="utf-8"))
    else:
        prov = None
        print("（没传 --prov：编辑证据链按「没建」如实走，publishable 预期是 false——"
              "样稿不替号主编凭据）")
    if hard and not a.force:
        print("\n有硬伤就不出样稿：把那句话从卖点位置改掉。")
        print("   样稿要证明的是「不拿待核数当卖点」，不是把它放行——放行要逐条凭据，"
              "那是人在界面上做的决定，这条脚本不代人做。")
        return 3

    cfg = load_ai_config()
    if not cfg.get("api_key"):
        print("REFUSED: 没读到 AI 配置（generate_poster 入口要过 _require_ai）")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    ai_poster.OUTPUT_DIR = out
    manifest = ai_poster.generate_poster(summary, cfg, plan=plan, publish=publish,
                                         text_mode="typeset", copies=1, do_review=False,
                                         do_verify=False, publish_channel="toutiao",
                                         form="newspic", provenance=prov, asr_text=asr,
                                         progress_cb=lambda m: print("   · " + m[:90]))
    pack_dir = out / str(a.summary)        # generate_poster 仍然按素材 id 分子目录
    pngs = sorted(p.name for p in pack_dir.glob("*.png"))
    print("\n样稿落盘：%s" % pack_dir)
    print("   PNG：%s" % "、".join(pngs))
    fg = manifest.get("fact_gate") or {}
    print("   publishable=%s  硬伤=%d  待看一眼=%d  未判帧的图=%s" % (
        manifest.get("publishable"), len(fg.get("hard") or []), len(fg.get("soft") or []),
        manifest.get("photos_unjudged") or []))
    print("   费用估算：%s 元（攻略页画面用素材真帧，不叫出图模型）" % manifest.get("cost_yuan_estimate"))

    # ── 质量报告落盘：交付物不能只有 PNG，"当前能不能发、为什么不能"要跟着图走 ──
    acts = manifest.get("action_gate") or {}
    (out / "action_gate.json").write_text(json.dumps(
        {"stats": acts.get("stats"), "asr_chars": acts.get("asr_chars"),
         "ready_for_poster": acts.get("ready_for_poster"),
         "overflow": acts.get("overflow"), "pending": acts.get("pending"),
         "rows": rep["rows"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    errs = [i for i in (manifest.get("lint") or []) if i.startswith("error")]
    lines = ["# 83 号样稿第二版 · 质量报告（%s）" % a.out, "",
             "- 模型调用次数：**0**（文案人手抄、画面用素材真帧，不出底图）",
             "- publishable：`%s`" % manifest.get("publishable"),
             "- 事实闸：硬 %d · 软 %d；攻略五段结构缺：%s；配文截断：%s" % (
                 len(fg.get("hard") or []), len(fg.get("soft") or []),
                 "、".join(gaps) or "无", cut or "无"),
             "- 行动依据闸：建议 %s 条 / 可用 %s / 待核 %s；越界进成品 %d 条" % (
                 (acts.get("stats") or {}).get("rows"), (acts.get("stats") or {}).get("ok"),
                 (acts.get("stats") or {}).get("pending"), len(acts.get("overflow") or [])),
             "", "## 阻断原因（逐条原样，不省略）", ""]
    lines += ["%d. %s" % (n, e) for n, e in enumerate(errs, 1)] or ["（无 error 行）"]
    lines += ["", "## 待核清单（印到成品之前停在这里的那些）", ""]
    lines += ["- 【%s】%s ｜ %s" % (r["where"], r["text"], "；".join(r["problems"])[:110])
              for r in rep["rows"] if r["status"] != "可用"][:40]
    (out / "质量报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("   质量报告：%s（阻断 %d 条 · 待核 %d 条）" % (
        out / "质量报告.md", len(errs), rep["stats"]["pending"]))

    if a.judge:
        pages = ai_poster.plan_pages(plan)
        used = []
        aid = ""
        for pg in pages:
            for ph in (pg.get("photos") or []):
                p = ph.get("path") or ""
                used.append((ph.get("frame"), p))
                if p and "/frames/" in p and not aid:
                    aid = p.split("/frames/")[1].split("/")[0]
        recs = judge_used(pages, cfg, out)
        (out / "frame_judge.json").write_text(json.dumps(
            {"aweme_id": aid, "records": recs}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n重新判帧（%d 张，真调用）：" % len(recs))
        for r in recs:
            print("   页%s %s clean=%s 主体=%r 原因=%r tokens=%s" % (
                r.get("page"), r.get("frame"), r.get("clean"), r.get("subject"),
                r.get("why"), r.get("tokens")))
        print("   记录：%s" % (out / "frame_judge.json"))

    after = sha(prod_mf)
    if before and after != before:
        print("!! 线上成品的 manifest 变了（%s → %s）——这条脚本不该写它" % (before, after))
        return 1
    print("线上 output/poster/%d/manifest.json 未被动：sha %s == %s" % (a.summary, before, after))
    print("RENDER_SAMPLE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
