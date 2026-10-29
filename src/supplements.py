# -*- coding: utf-8 -*-
"""supplements.py — 给已采用的视频素材提名**强相关**的非视频补充料（号主 2026-10-28 提的方向）。

要解决的问题：微信文章／暴雪公告／NGA 帖这些非视频素材，走图文卡不出"跟内容有关的图"、
走图文攻略又没有帧可绑，单独成稿又撑不起一篇。号主定的用法是**当视频素材的补充资料**，
挂在排产格里视频下面，让主素材那份证据更完整。

三条写死的规矩（都是他点过名的）：
1. **只提名，不自动塞**。判据再硬也可能是错的，采用与否是人勾；勾了的才进提示词。
2. **强相关要有可解释的判据**，不是"看起来像"：标题有共同片段（能打印出来给人看）
   ＋ 发布时间在窗口内。两个条件缺一个就不提名，宁缺毋滥。
3. **补充料永远是配角**：不进素材台账的编号（素材1/素材2 仍是主素材），
   带出处与"一手/转述/论坛"分级，论坛那批沿用库里的 remark——
   只当选题参考，不得成段引用、不得冒充本站或官方观点。

判据阈值是 2026-10-28 在真库上量出来定的（见 `scripts/e2e_supplements.py` 的分布断言），
调它们要连那份分布一起看，别只挑一条命中的样例。
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

# 非视频素材表。B 站/抖音是视频侧（主素材），不进这里。
NON_VIDEO_SHEETS = ("微信文章", "暴雪官方", "NGA无限板块")
# 能当"父级"的表：只有视频素材下面才挂子集（排产表那一侧的展开就是按这个判的）。
# 抖音图文两边都不算——它是订阅账号的成品图文，不当父级、也不进补充料池。
VIDEO_SHEETS = ("抖音视频数据", "B站视频")


def is_video(sheet: str) -> bool:
    """这张源表是不是"有视频、能抽帧"那一侧。判据只有这一份，别再抄一遍名单。"""
    return (sheet or "").strip() in VIDEO_SHEETS
# 分级按"这张表整体是什么来源"判，别按作者猜：库里那 5 个微信订阅全是第三方转述号，
# 暴雪官方是官方一手，NGA 是玩家发帖。
TIER_BY_SHEET = {
    "暴雪官方": "一手·官方口径",
    "微信文章": "转述·第三方号",
    "NGA无限板块": "论坛·只当选题参考",
}
TIER_RANK = {"一手·官方口径": 0, "转述·第三方号": 1, "论坛·只当选题参考": 2}

# 判据（2026-10-28 真库校准，命中率与假相关都看过）：
# 共同片段要先过 `informative`（剔掉游戏名还剩 ≥2 字），
# 然后要么有一个 ≥4 字的，要么有两个且合计 ≥6 字；覆盖主素材标题 ≥20%；发布日相差 ≤10 天。
MIN_FRAGMENT = 3
STRONG_FRAGMENT = 4
MIN_COVERAGE = 0.20
DEFAULT_WINDOW_DAYS = 10
MAX_CANDIDATES = 8

_PUNCT_RE = re.compile(r"[^\w一-鿿]+")
# 光有"共同片段"还不够——真库抽样抓到两类假相关：
#   ① 只撞上游戏名：「魔兽世界u型护颈枕特价」撞上暴雪官方《魔兽世界》职业解析，
#      「魔兽无限服」「魔兽世界无限」这类片段在魔兽题材里几乎每条标题都有，撞上等于没撞上；
#   ② 只撞上时长/数量口语：「10分钟带你解析AI应用三层概念」撞上论坛帖「10分钟一掉线」。
# 所以判据只认**把游戏名家族字符剔掉之后还剩 ≥2 个字**的片段（剔完还剩"职业推荐"、"团本难度"
# 这种才是真信号；剔完什么都不剩就是撞名）。
# 注意：比对发生在 normalize（去标点、转小写）之后的串上，所以这里只写小写、不带标点。
GAME_TOKENS = ("魔兽世界", "魔兽无限服", "魔兽无限", "无限服", "正式服",
               "怀旧服", "时光服", "测试服", "国服", "暴雪", "魔兽", "无限", "玩家", "的",
               "forever")
_NOISE_RE = re.compile(r"^\d+\s*(分钟|小时|天|秒|周|月|年|个|条|张|级|层|成|点|kb|ms|g|帧)$", re.I)
MIN_RESIDUE = 2      # 剔掉游戏名后至少剩两个字


def informative(frag: str) -> bool:
    """这个片段有没有信息量：剔掉游戏名家族与"10分钟"这类时长词后，还剩不剩内容。"""
    f = (frag or "").strip()
    if not f or _NOISE_RE.match(f):
        return False
    residue = f
    for tok in GAME_TOKENS:
        residue = residue.replace(tok, "")
    return len(residue) >= MIN_RESIDUE


def strong_fragments(a: str, b: str) -> List[str]:
    """两段标题里"能当证据"的共同片段：≥3 字、且过 `informative`。"""
    return [f for f in fragments(a, b) if informative(f)]


def normalize(text: str) -> str:
    """标题归一：去标点空格、小写——共同片段要在"干净串"上找，否则全角逗号能把词切开。"""
    return _PUNCT_RE.sub("", (text or "").strip()).lower()


def fragments(a: str, b: str) -> List[str]:
    """两段标题里连续相同、且长度 ≥MIN_FRAGMENT 的片段（这就是给人看的"为什么算相关"）。"""
    x, y = normalize(a), normalize(b)
    if not x or not y:
        return []
    out = []
    for blk in difflib.SequenceMatcher(None, x, y).get_matching_blocks():
        if blk.size >= MIN_FRAGMENT:
            frag = x[blk.a:blk.a + blk.size]
            if frag not in out:
                out.append(frag)
    # 丢掉被更长片段包住的短片段（"种族平衡" 与 "种族" 同时命中时只留前者）
    return [f for f in out if not any(f != g and f in g for g in out)]


def _days_between(d1: str, d2: str):
    try:
        a = datetime.strptime((d1 or "")[:10], "%Y-%m-%d")
        b = datetime.strptime((d2 or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return abs((a - b).days)


def score(main_title: str, cand_title: str, main_date: str, cand_date: str,
          window_days: int = DEFAULT_WINDOW_DAYS):
    """返回 (score, 理由) 或 None（不够强就不提名，别硬凑）。

    只数**有信息量**的片段：覆盖率先扣掉游戏名/时长词那批，再跟主素材标题长度比。
    """
    frags = strong_fragments(main_title, cand_title)
    if not frags:
        return None
    strong = [f for f in frags if len(f) >= STRONG_FRAGMENT]
    if not strong and (len(frags) < 2 or sum(len(f) for f in frags) < 6):
        return None
    base = normalize(main_title) or " "
    coverage = sum(len(f) for f in frags) / float(len(base))
    if coverage < MIN_COVERAGE:
        return None
    gap = _days_between(main_date, cand_date)
    if gap is None or gap > window_days:
        return None
    return round(coverage, 3), "标题共同片段：%s；发布日相差 %d 天" % ("、".join(frags[:4]), gap)


def _pool_rows(conn):
    conn.row_factory = sqlite3.Row
    marks = ",".join("?" for _ in NON_VIDEO_SHEETS)
    sql = ("SELECT id, source_sheet, source_row, author, title, published_at, source_url, remark,"
           " LENGTH(COALESCE(transcript,'')) AS body_len FROM videos"
           " WHERE source_sheet IN (%s)" % marks)
    return [dict(r) for r in conn.execute(sql, list(NON_VIDEO_SHEETS))]


def candidates(db_path, mains, window_days: int = DEFAULT_WINDOW_DAYS,
               limit: int = MAX_CANDIDATES) -> Dict[str, List[Dict[str, Any]]]:
    """给一批主素材（已采用的视频行）提名补充料。

    `mains`：主素材 dict 列表（要 id/title/published_at/source_sheet）。
    返回 {"list": [提名…], "scanned": 扫了多少条非视频料}，每条提名带
    id/title/author/sheet/date/url/tier/why/score/main_id（对哪条主素材相关）。
    同一张补充料对多条主素材都相关时，只保留分最高的那一次（不重复占位）。
    """
    wanted = [m for m in (mains or []) if (m.get("title") or "").strip()]
    out = []
    if not wanted:
        return {"list": [], "scanned": 0}
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        pool = _pool_rows(con)
    finally:
        con.close()
    main_ids = {int(m["id"]) for m in wanted if m.get("id")}
    pool = [r for r in pool if r["id"] not in main_ids]
    for cand in pool:
        best = None
        for m in wanted:
            hit = score(m.get("title") or "", cand.get("title") or "",
                        m.get("published_at") or "", cand.get("published_at") or "",
                        window_days)
            if hit and (best is None or hit[0] > best[0]):
                best = (hit[0], hit[1], m)
        if not best:
            continue
        cov, why, m = best
        tier = TIER_BY_SHEET.get(cand["source_sheet"], "素材")
        out.append({"id": cand["id"], "title": cand.get("title") or "",
                    "author": cand.get("author") or "", "sheet": cand["source_sheet"],
                    "row": cand.get("source_row"),
                    "date": (cand.get("published_at") or "")[:10],
                    "url": cand.get("source_url") or "", "tier": tier,
                    "why": why, "score": cov, "main_id": int(m["id"]),
                    "main_title": m.get("title") or "",
                    "body_len": cand.get("body_len") or 0,
                    "remark": cand.get("remark") or ""})
    out.sort(key=lambda r: (-r["score"], TIER_RANK.get(r["tier"], 9), r["date"]))
    return {"list": out[:limit], "count_all": len(out), "scanned": len(pool)}


def brief_lines(items) -> List[str]:
    """把采用的补充料渲染成提示词里的那几行（带出处与分级，写死配角身份）。"""
    lines = []
    for i, r in enumerate(items or [], 1):
        lines.append("- 补充%d｜%s｜%s｜作者：%s｜发布日：%s｜%s｜链接：%s"
                     % (i, r.get("tier") or "素材", r.get("title") or "（没标题）",
                        r.get("author") or "未记录", r.get("date") or "未记录",
                        r.get("why") or "", r.get("url") or "未记录"))
    return lines


# 提示词预算：补充料是配角，给多了会挤掉主素材、也多花免费额度里的 token。
MAX_ITEMS = 3
PER_ITEM_CHARS = 1200


def rows_by_ids(db_path, ids, limit: int = MAX_ITEMS, snaps=None) -> List[Dict[str, Any]]:
    """按 id 现读补充料（含正文节选）。id 顺序保留，取不到的跳过不报错。

    `snaps`：任务里存的证据快照，用来把"当初凭什么算相关"那行带回提示词——
    理由不重新现算，判据以后改了也不该改写历史稿的依据。
    """
    want = [int(i) for i in (ids or [])][:limit]
    if not want:
        return []
    why_by = {}
    for s in (snaps or []):
        try:
            why_by[int(s.get("db_id"))] = s.get("why") or ""
        except (TypeError, ValueError):
            continue
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" for _ in want)
        got = {r["id"]: dict(r) for r in con.execute(
            "SELECT id, source_sheet, author, title, published_at, source_url, remark,"
            " transcript FROM videos WHERE id IN (%s)" % marks, want)}
    finally:
        con.close()
    out = []
    for i in want:
        r = got.get(i)
        if not r:
            continue
        body = (r.get("transcript") or "").strip()
        out.append({"id": i, "sheet": r.get("source_sheet") or "",
                    "title": r.get("title") or "", "author": r.get("author") or "",
                    "date": (r.get("published_at") or "")[:10],
                    "url": r.get("source_url") or "", "remark": r.get("remark") or "",
                    "why": why_by.get(i, ""),
                    "tier": TIER_BY_SHEET.get(r.get("source_sheet"), "素材"),
                    "body": body[:PER_ITEM_CHARS] + ("…" if len(body) > PER_ITEM_CHARS else "")})
    return out


def why_map(mains, rows, window_days: int = DEFAULT_WINDOW_DAYS) -> Dict[int, str]:
    """给勾选的这几条补充料现算"凭什么算相关"，冻进任务的证据快照。

    理由由服务端算、不由前端回传：前端那次提名用的窗口/阈值可能已经不是现在这套，
    存下来的理由就会和实际判据对不上。
    """
    out = {}
    for r in rows or []:
        best = None
        cand_date = r.get("published_at") or r.get("date") or ""
        for m in mains or []:
            hit = score(m.get("title") or "", r.get("title") or "",
                        m.get("published_at") or m.get("date") or "", cand_date, window_days)
            if hit and (best is None or hit[0] > best[0]):
                best = hit
        out[int(r["id"])] = best[1] if best else "人工勾的（未命中当前判据，按人的判断保留）"
    return out


def brief_block(items) -> str:
    """渲染进整理稿提示词的那一段「补充资料」。

    身份必须在这儿写死一次，不能只靠界面文案：补充料和主素材在提示词里长得很像，
    模型默认会一视同仁地引用——而论坛帖里一半是猜测。
    """
    lines = brief_lines(items)
    if not lines:
        return ""
    bodies = []
    for i, r in enumerate(items or [], 1):
        if r.get("body"):
            extra = "（这条素材自己的要求：%s）" % r["remark"] if r.get("remark") else ""
            bodies.append("补充%d 原文节选：%s%s" % (i, r["body"], extra))
    return ("\n本篇的补充资料（**不是主素材**，只用来核对细节、补全口径、发现反例；"
            "「素材1/素材2」那套编号仍然只指主素材，不许把补充料写成主素材）：\n"
            + "\n".join(lines) + "\n"
            + "纪律：① 补充料里有、主素材没有的事实，可以用，但【来源】必须写清是上面哪一条补充料"
              "（带作者与链接），不许含糊成「本站看过」；② 标注为「论坛」「转述」的内容**不得成段引用、"
              "不得冒充官方口径**，与主素材冲突时以主素材为准并把分歧写进【风险核查】；"
              "③ 补充料里没有的，不许因为这里列了就编。\n"
            + ("\n".join(bodies) + "\n" if bodies else ""))

