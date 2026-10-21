# -*- coding: utf-8 -*-
"""guide_provenance.py — 「这篇我们到底新增了什么」的编辑证据链（只管记录与算缺项，不判原创）。

为什么要有这一层（2026-10-21 PRD `PRD-video2text-原创增量改造-2026-10-21.md`）：
现在这条线能从"别人的视频"一步走到"三页图文卡"，中间**没有任何地方记下**：这篇解决玩家哪个问题、
我们独立补了什么、结论凭什么核过、借来的文字和画面按什么依据用。结果就是——一份口播转写换个排版
就能长得像"我们的攻略"。事实闸（`fact_gate`）管的是"数没核过别当卖点"，
判帧（`frame_pool`）管的是"画面上有没有别人压的字"，**两件都不等于权利与原创判断**。

三条口径写死在这里，接手别改回去：
1. **工具不证明原创**。这里只算"缺什么"并把它摆到成品预览上，永远不输出"100% 原创""已获原创资格"
   这类标记；勾不勾原创、能不能首发，是人按当时平台规则逐篇判的（PRD §范围与边界）。
2. **缺字段一律"未审"，不默认通过**。历史成品、旧记录没有这些字段时按最保守的一档显示，
   免得一次迁移就把几十篇变成"看着已审"。
3. **站内查询 ≠ 游戏内实测**。结论状态只有 `已核 / 待核 / 参考转述` 三种，
   从素材口播转写来又没独立核过的，默认就是"参考转述"；写"我测过"但拿不出可回查记录，仍按待核算。

版本语义沿用排产任务那边（`plan_tasks` 的 `UNIQUE(slot_key, channel, form, version)`）：
证据链**跟着版本走**，已开工那一版不被后来改的选题卡悄悄覆盖。
本模块**不 import `plan_tasks`**（那份文件此刻还在别人未提交的改动里，import 进来
我这个提交在干净检出上就是 ImportError），要读就 SQL 读表、表不在就当没有。

Python 3.9 兼容：不用 match / X|Y 标注。
"""
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

_LOCK_PATHS = set()
CLAIM_STATUS = ("已核", "待核", "参考转述")
EVIDENCE_KINDS = ("游戏内实测", "官方一手页面", "本站页面查询", "本地操作记录", "无")
USAGE_KINDS = ("选题线索", "事实来源", "被引用的表达", "被引用的画面", "我们自己做的")
REVIEW_STATES = ("未审", "可发布", "不可用")
# 编辑状态是**算出来的**，不给人手填一个乐观值
STATE_UNSET = "independent_unset"
STATE_DIGEST = "digest"
STATE_CONTRIB = "contrib_pending"
STATE_THIN = "evidence_thin"
STATE_LABELS = {
    STATE_UNSET: "独立贡献未说明",
    STATE_DIGEST: "整理转述（如实标为整理）",
    STATE_CONTRIB: "有独立贡献，待人工评估",
    STATE_THIN: "证据不足（缺项见下）",
}
# 人在"本篇新增了什么"这一栏里明写"没有，就是整理"时用的哨兵（不许留空当默认）
DIGEST_MARKS = ("无", "没有", "整理")
_MINE_WORDS = re.compile(r"我(?:自己)?(?:测|试|打|跑|量|查)过|亲测|实测过|我这边试")
_IN_GAME = re.compile(r"实测|打过|跑过|测过|帧数记录|跑分")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _j(text, default):
    try:
        val = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return val if isinstance(val, type(default)) else default


def ensure_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS guide_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                form TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                task_id INTEGER,
                summary_id INTEGER,
                question TEXT DEFAULT '',
                contribution TEXT DEFAULT '',
                contribution_evidence TEXT DEFAULT '',
                claims TEXT DEFAULT '[]',
                materials TEXT DEFAULT '[]',
                updated_at TEXT,
                UNIQUE(slot_key, channel, form, version)
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_prov_summary ON guide_provenance(summary_id)")
        con.commit()


def _norm_claim(raw: Dict[str, Any]) -> Dict[str, Any]:
    status = str(raw.get("status") or "").strip()
    if status not in CLAIM_STATUS:
        status = "参考转述"          # 没填状态 = 默认从素材转述来，不算已核
    kind = str(raw.get("evidence_kind") or "").strip()
    if kind not in EVIDENCE_KINDS:
        kind = "无"
    return {"text": str(raw.get("text") or "").strip()[:200],
            "evidence": str(raw.get("evidence") or "").strip()[:300],
            "evidence_kind": kind, "verifier": str(raw.get("verifier") or "").strip()[:24],
            "vdate": str(raw.get("vdate") or "").strip()[:10], "status": status}


def _norm_material(raw: Dict[str, Any]) -> Dict[str, Any]:
    usage = str(raw.get("usage") or "").strip()
    if usage not in USAGE_KINDS:
        usage = "选题线索"            # 没说用途就只当线索，不算事实来源也不算引用表达
    review = str(raw.get("review") or "").strip()
    if review not in REVIEW_STATES:
        review = "未审"                # 缺字段的旧记录一律未审，不默认通过
    return {"ref": str(raw.get("ref") or "").strip()[:40],
            "url": str(raw.get("url") or "").strip()[:300],
            "author": str(raw.get("author") or "").strip()[:40],
            "usage": usage, "rights_basis": str(raw.get("rights_basis") or "").strip()[:200],
            "review": review}


def save(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """存一份编辑证据链（草稿允许空着，缺项由 `gaps()` 算，不在这一步报错）。"""
    ensure_table(db_path)
    slot = str(payload.get("slot_key") or "").strip()[:40]
    channel = str(payload.get("channel") or "toutiao").strip()[:10]
    form = str(payload.get("form") or "guide").strip()[:10]
    if not slot:
        raise ValueError("证据链要挂在哪个排产槽上：slot_key 不能空")
    try:
        version = int(payload.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    claims = [_norm_claim(c) for c in (payload.get("claims") or []) if isinstance(c, dict)][:12]
    mats = [_norm_material(m) for m in (payload.get("materials") or []) if isinstance(m, dict)][:20]
    row = {"slot_key": slot, "channel": channel, "form": form, "version": version,
           "task_id": payload.get("task_id"), "summary_id": payload.get("summary_id"),
           "question": str(payload.get("question") or "").strip()[:200],
           "contribution": str(payload.get("contribution") or "").strip()[:300],
           "contribution_evidence": str(payload.get("contribution_evidence") or "").strip()[:400],
           "claims": claims, "materials": mats, "updated_at": _now()}
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO guide_provenance (slot_key, channel, form, version, task_id, summary_id,"
            " question, contribution, contribution_evidence, claims, materials, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(slot_key, channel, form, version) DO UPDATE SET"
            " task_id=excluded.task_id, summary_id=excluded.summary_id,"
            " question=excluded.question, contribution=excluded.contribution,"
            " contribution_evidence=excluded.contribution_evidence,"
            " claims=excluded.claims, materials=excluded.materials,"
            " updated_at=excluded.updated_at",
            (slot, channel, form, version, row["task_id"], row["summary_id"], row["question"],
             row["contribution"], row["contribution_evidence"], json.dumps(claims, ensure_ascii=False),
             json.dumps(mats, ensure_ascii=False), row["updated_at"]))
        con.commit()
    return row


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["claims"] = _j(d.get("claims"), [])
    d["materials"] = _j(d.get("materials"), [])
    return d


def load(db_path: str, slot_key: str, channel: str = "toutiao", form: str = "guide",
         version: int = 1) -> Dict[str, Any]:
    ensure_table(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM guide_provenance WHERE slot_key=? AND channel=?"
                        " AND form=? AND version=?",
                        (slot_key, channel, form, int(version or 1))).fetchone()
    return _row_to_dict(r) if r else {}


def load_for_summary(db_path: str, summary_id: int) -> Dict[str, Any]:
    """从整理稿倒查证据链（成品预览那一头只有 summary_id）。

    先按 `summary_id` 直接命中；命中不了再借 `plan_tasks` 那一行找到 slot+版本
    ——**SQL 读表，不 import 那份未提交的文件**，表不存在就当没有。
    """
    ensure_table(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        # 先认排产任务那一行：它才是"这篇稿是哪一版生成的"的权威。
        # 直接按 summary_id 捞会串篇——同一篇稿可能先挂 v1 又存了 v2，谁 updated_at 新就命中谁。
        if con.execute("SELECT count(*) FROM sqlite_master WHERE name='plan_tasks'").fetchone()[0]:
            t = con.execute("SELECT slot_key, channel, form, version FROM plan_tasks "
                            "WHERE summary_id=? ORDER BY version DESC LIMIT 1",
                            (int(summary_id),)).fetchone()
            if t:
                r = con.execute("SELECT * FROM guide_provenance WHERE slot_key=? AND channel=?"
                                " AND form=? AND version=?",
                                (t["slot_key"], t["channel"], t["form"], t["version"])).fetchone()
                if r:
                    return _row_to_dict(r)
        r = con.execute("SELECT * FROM guide_provenance WHERE summary_id=? "
                        "ORDER BY version DESC, updated_at DESC LIMIT 1",
                        (int(summary_id),)).fetchone()
    return _row_to_dict(r) if r else {}


def is_digest(row: Dict[str, Any]) -> bool:
    """人在"新增了什么"那一栏明写"没有，就是整理"——这是**如实标注**，不是缺项。"""
    c = str((row or {}).get("contribution") or "").strip()
    return bool(c) and len(c) <= 12 and any(m in c for m in DIGEST_MARKS)


def state(row: Dict[str, Any]) -> Tuple[str, str]:
    """编辑状态（算出来的）。返回 (key, 中文标签)。"""
    row = row or {}
    if not str(row.get("contribution") or "").strip():
        return STATE_UNSET, STATE_LABELS[STATE_UNSET]
    if is_digest(row):
        return STATE_DIGEST, STATE_LABELS[STATE_DIGEST]
    gaps = [g for g in compute_gaps(row) if g["blocks"] == "state"]
    return (STATE_THIN, STATE_LABELS[STATE_THIN]) if gaps else (STATE_CONTRIB, STATE_LABELS[STATE_CONTRIB])


def compute_gaps(row: Dict[str, Any]) -> List[Dict[str, str]]:
    """缺什么，逐项点名到**具体字段/结论/图片**，并标它挡什么。

    `blocks`：`state` 只影响编辑状态那一档；`publish` 影响"待发/可发布"（合并进 publishable）。
    缺项一律说"未说明/未审"，不写成"不合格"——判断权在人。
    """
    row = row or {}
    out: List[Dict[str, str]] = []
    if not str(row.get("question") or "").strip():
        out.append({"field": "question", "blocks": "publish",
                    "what": "没写这篇要解决玩家哪个问题",
                    "hint": "先有一个可核的问题，再决定用不用这条材料"})
    contrib = str(row.get("contribution") or "").strip()
    if not contrib:
        out.append({"field": "contribution", "blocks": "state",
                    "what": "独立贡献未说明（本篇新增了什么可指认的内容）",
                    "hint": "没有新增就如实标整理，别靠换排版冒充"})
    elif not is_digest(row) and not str(row.get("contribution_evidence") or "").strip():
        out.append({"field": "contribution_evidence", "blocks": "publish",
                    "what": "写了新增内容，但没有对应的证据或操作记录",
                    "hint": "填可回查的那一样：链接、本地截图路径、操作记录编号"})
    for i, c in enumerate(row.get("claims") or [], 1):
        text = (c.get("text") or "")[:24]
        if c.get("status") == "已核":
            if not (c.get("evidence") or "").strip():
                out.append({"field": "claims[%d].evidence" % i, "blocks": "publish",
                            "what": "结论「%s」标了已核，但没有可回查的证据" % text,
                            "hint": "补链接或本地操作记录；补不出来就改回待核"})
            if not (c.get("verifier") or "").strip() or not (c.get("vdate") or "").strip():
                out.append({"field": "claims[%d].verifier" % i, "blocks": "publish",
                            "what": "结论「%s」没写谁核的、哪天核的" % text,
                            "hint": "人手填「我测过」而没有记录，仍按待核"})
        if c.get("evidence_kind") == "本站页面查询" and _IN_GAME.search(c.get("text") or ""):
            out.append({"field": "claims[%d].evidence_kind" % i, "blocks": "publish",
                        "what": "结论「%s」的证据是本站页面查询，文字却写成游戏内实测" % text,
                        "hint": "查询验证与游戏内测试分开说，不混"})
    if not (row.get("claims") or []):
        out.append({"field": "claims", "blocks": "state",
                    "what": "没有逐条结论-证据对应",
                    "hint": "至少把稿子里那几个关键结论对上出处；全篇都是转述就如实标整理"})
    for i, m in enumerate(row.get("materials") or [], 1):
        who = (m.get("url") or m.get("author") or m.get("ref") or "第%d条" % i)[:28]
        if m.get("usage") in ("被引用的表达", "被引用的画面") and not (m.get("rights_basis") or "").strip():
            out.append({"field": "materials[%d].rights_basis" % i, "blocks": "publish",
                        "what": "用了外部表达/画面（%s），没写授权或使用依据" % who,
                        "hint": "标出处 ≠ 取得许可；拿不到依据就换掉这一项"})
        if m.get("review") != "可发布" and m.get("usage") != "选题线索":
            out.append({"field": "materials[%d].review" % i, "blocks": "publish",
                        "what": "%s 的使用依据还没人审（当前：%s）" % (who, m.get("review") or "未审"),
                        "hint": "自绘图与本站截图也要标来源，不凭一句「原创」清疑点"})
    return out


def blocking_gaps(row: Dict[str, Any]) -> List[Dict[str, str]]:
    """挡"待发/可发布"的那些缺项。**整条证据链没建过**也算一项——
    旧记录、没填过的稿子一律按最保守的一档处理，不能默认通过（PRD §范围与边界）。
    """
    if not row:
        return [{"field": "provenance", "blocks": "publish",
                 "what": "这篇没建编辑证据链：解决什么问题、我们新增了什么、依据在哪，都没记",
                 "hint": "如实整理也可以发，但那三行要写下来；不填不等于通过"}]
    return [g for g in compute_gaps(row) if g["blocks"] == "publish"]


def summary_line(row: Dict[str, Any]) -> str:
    """给成品预览那一行用的中文摘要（明确不是平台认证）。"""
    key, label = state(row)
    gaps = blocking_gaps(row)
    bits = ["编辑状态：%s" % label]
    if gaps:
        bits.append("待发状态受阻 %d 项" % len(gaps))
    bits.append("（内部提示，不是平台原创资格认证）")
    return "｜".join(bits)
