# -*- coding: utf-8 -*-
"""risk_ledger.py — 未核事实按**素材**记账，换一版稿不许把它洗掉。

为什么要这一层（2026-10-29 真实事故）：同一份素材 309874，10-26 那版整理稿列了 11 条待核，
其中两条正是读者后来抓出来的「愈合祷言弹跳递增 40%、吃满 200% 法强」和「切目标不掉连击点」；
10-29 重跑的那版只写了 2 条，这两条**没了**，成品照旧把两个数当事实印出去发掉。
解析器（`fact_gate.risk_items`）只能读"已经写出来的内容"——**模型漏写，风险就静默消失**。
所以待核事实必须存在稿子外面：按素材 id 累积，新稿只能补充或凭证据解除，不能靠"这次没写"过关。

四条口径：
1. **只能凭可定位证据解除**（`evidence_ok`）：官方原句/公告/补丁链接，或写明版本与日期的本人实测；
   一句话"我看过了"不算。规矩与 `fact_gate.check_override` 一致——列了核实点不等于完成核实。
2. **继承是并进稿子那一节**，不是另起一节写在文末：`fact_gate` 只读它找到的第一处清单，
   写在后面等于没写（这是本轮踩过的形状，见 `risk_section` 的注释）。
3. **只记上游已经标过的条目**，不拿关键词去全文扫着造风险——那会变成全局封杀，
   号主要的是"标过的别丢"，不是"机器替我决定哪句可疑"。
4. 旧成品（已发出去的笔记）一律不改，这里只影响**下一次出包**。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import fact_gate

_TABLE = """
CREATE TABLE IF NOT EXISTS risk_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  material_id INTEGER NOT NULL,
  claim TEXT NOT NULL,
  raw TEXT NOT NULL,
  tokens TEXT NOT NULL DEFAULT '[]',
  kind TEXT NOT NULL DEFAULT '其他',
  status TEXT NOT NULL DEFAULT 'open',
  first_summary_id INTEGER,
  last_summary_id INTEGER,
  created_at TEXT,
  updated_at TEXT,
  source TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  resolved_by TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT '',
  UNIQUE(material_id, claim)
)
"""


def ensure(db_path: str) -> None:
    with sqlite3.connect(str(db_path)) as con:
        con.execute(_TABLE)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def norm(text: str) -> str:
    """归一化成"同一条说法"的键：去空白、去标点、去 markdown、小写。

    模型每版措辞会漂（"的具体数值。" vs "数值待核"），完全等值比对会把它当成两条新风险；
    但也不能太松——太松会把两条不同的技能改动并成一条。取中间：只留汉字/字母/数字，
    并截断到 40 字，够稳也够区分。
    """
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "")).lower()
    return s[:40]


# 分级判据只有一份，在 `fact_gate.claim_kind`（机制数值／二元机制／其他）：
# 台账分级和发布闸必须同一口径，两边各写一份迟早对不上（这仓库反复踩过）。
classify = fact_gate.claim_kind

# 继承条目的标记。判重靠它认出自己上次写的行（见 `inherit_into`）。
INHERIT_MARK = "【继承·未核】"


def record_draft(db_path: str, material_ids: Iterable[int], summary_id: Optional[int],
                 items: List[Dict[str, Any]]) -> int:
    """把这一版稿写出来的待核条目入账（已解除过的不复活，除非换了新措辞）。"""
    rows = [i for i in (items or []) if str(i.get("raw") or "").strip()]
    if not rows:
        return 0
    ensure(db_path)
    mats = [int(m) for m in material_ids if m]
    n = 0
    with sqlite3.connect(str(db_path)) as con:
        for mid in mats:
            for it in rows:
                claim = norm(it["raw"])
                if not claim:
                    continue
                now = _now()
                con.execute(
                    "INSERT INTO risk_ledger(material_id, claim, raw, tokens, kind, status,"
                    " first_summary_id, last_summary_id, created_at, updated_at)"
                    " VALUES(?,?,?,?,?, 'open', ?,?,?,?)"
                    " ON CONFLICT(material_id, claim) DO UPDATE SET"
                    "  raw=excluded.raw, tokens=excluded.tokens, kind=excluded.kind,"
                    "  last_summary_id=excluded.last_summary_id, updated_at=excluded.updated_at",
                    (mid, claim, str(it["raw"])[:400],
                     json.dumps(list(it.get("tokens") or []), ensure_ascii=False),
                     fact_gate.claim_kind(it["raw"]), summary_id, summary_id, now, now))
                n += 1
    return n


def open_items(db_path: str, material_ids: Iterable[int]) -> List[Dict[str, Any]]:
    """这些素材上还挂着哪些没解除的待核事实。"""
    mats = [int(m) for m in material_ids if m]
    if not mats:
        return []
    ensure(db_path)
    ph = ",".join("?" for _ in mats)
    out = []
    with sqlite3.connect(str(db_path)) as con:
        con.row_factory = sqlite3.Row
        for r in con.execute(
                "SELECT * FROM risk_ledger WHERE status='open' AND material_id IN (%s)"
                " ORDER BY material_id, id" % ph, mats):
            d = dict(r)
            d["tokens"] = json.loads(d.get("tokens") or "[]")
            out.append(d)
    return out


def all_items(db_path: str, material_ids: Iterable[int]) -> List[Dict[str, Any]]:
    """含已解除的（界面要说清"这条是什么时候、凭什么解除的"）。"""
    mats = [int(m) for m in material_ids if m]
    if not mats:
        return []
    ensure(db_path)
    ph = ",".join("?" for _ in mats)
    with sqlite3.connect(str(db_path)) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM risk_ledger WHERE material_id IN (%s) ORDER BY status, material_id, id" % ph,
            mats)]


def evidence_ok(source: str, result: str) -> Tuple[bool, List[str]]:
    """解除一条待核事实要的证据长什么样。返回 (行不行, 缺哪几样)。

    两类算数：
      · 一手文本：链接 / 公告 / 蓝贴 / 官网 / 补丁 / 热修 / 版本号；
      · 本人实测：必须同时写明**版本或日期**，否则别人无法判断是不是同一版（读者那句
        "你这啥时候的信息"就是这么来的）。
    """
    s = str(source or "").strip()
    r = str(result or "").strip()
    missing: List[str] = []
    locatable = re.search(r"https?://|公告|蓝贴|蓝帖|官网|补丁|热修|版本号|v\d+\.\d+", s, re.I)
    own = re.search(r"实测|自己试|上手|进游戏核|游戏内核对|游戏内看", s)
    dated = re.search(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}日|\d+\.\d+\.\d+|版本\s*\S+",
                      s + " " + r)
    if len(s) < 6:
        missing.append("出处太短——要能定位：链接／哪篇公告／哪一版游戏内实测")
    if not (locatable or own):
        missing.append("出处不可定位：要么给一手文本（链接/公告/蓝贴/补丁/版本号），要么写明本人实测")
    if own and not locatable and not dated:
        missing.append("本人实测要带版本或日期，否则换一版就没法核对")
    if len(r) < 4:
        missing.append("没写核出来的结论（是、不是、还是数值改成多少）")
    return (not missing), missing


def resolve(db_path: str, refs: Iterable[Any], source: str, result: str,
            by: str = "") -> Dict[str, Any]:
    """按 id 或 (material_id, claim) 解除。证据不合格就一条都不解除（不部分生效）。"""
    refs = list(refs or [])
    if not refs:
        return {"ok": False, "resolved": 0, "missing": ["没点名要解除哪一条"]}
    ok, missing = evidence_ok(source, result)
    if not ok:
        return {"ok": False, "resolved": 0, "missing": missing}
    ensure(db_path)
    ids: List[int] = []
    with sqlite3.connect(str(db_path)) as con:
        for ref in refs:
            if isinstance(ref, (tuple, list)) and len(ref) == 2:
                row = con.execute("SELECT id FROM risk_ledger WHERE material_id=? AND claim=?",
                                  (int(ref[0]), norm(ref[1]))).fetchone()
            else:
                row = con.execute("SELECT id FROM risk_ledger WHERE id=?", (int(ref),)).fetchone()
            if row:
                ids.append(int(row[0]))
        if not ids:
            return {"ok": False, "resolved": 0, "missing": ["台账里找不到这些条目"]}
        ph = ",".join("?" for _ in ids)
        con.execute(
            "UPDATE risk_ledger SET status='resolved', source=?, result=?, resolved_by=?, resolved_at=?,"
            " updated_at=? WHERE id IN (%s)" % ph,
            [source.strip(), result.strip(), by.strip() or "人工", _now(), _now()] + ids)
    return {"ok": True, "resolved": len(ids), "missing": []}


def inherit_into(md: str, items: List[Dict[str, Any]]) -> Tuple[str, int]:
    """把台账里仍 open、而这一版**没写出来**的条目并回稿子那一节核查清单。

    必须并进同一节：`fact_gate.risk_section` 只认它找到的第一处清单，
    另起一节写在文末（配图素材之后）解析器读不到，继承就等于没发生。
    """
    if not items:
        return md, 0
    sec = fact_gate.risk_section(md)
    lines = str(md or "").splitlines()
    have = set()
    if sec:
        for ln in lines[sec["start"] + 1:sec["end"]]:
            body = fact_gate._ITEM_PREFIX.sub("", ln.strip()).replace("**", "")
            if INHERIT_MARK in body:
                # 上一次继承写进去的那一行，去掉标记与尾注之后就是原句——
                # 不这么认，每重跑一次生成就多一屏重复条目
                body = body.split(INHERIT_MARK, 1)[1].split("（上一版", 1)[0]
            have.add(norm(body))
    add = []
    for it in items:
        raw = str(it.get("raw") or "").strip()
        if not raw or norm(raw) in have:
            continue
        add.append("- [ ] %s%s（上一版整理稿标过，至今没有可定位证据）" % (INHERIT_MARK, raw))
    if not add:
        return md, 0
    if sec:
        at = sec["end"]
        block = add
    else:
        at = len(lines)
        block = ["", "## 事实核查清单（台账继承）", ""] + add
    lines[at:at] = block
    out = "\n".join(lines)
    return (out + "\n") if str(md or "").endswith("\n") else out, len(add)
