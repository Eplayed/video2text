# -*- coding: utf-8 -*-
"""plan_brief.py — 「今天该发哪篇」的唯一判据（日报、排产表颜色、接口三处共用这一份）。

为什么单独成模块而不是留在脚本里（2026-10-21 号主要求排产表按颜色区分"一定要发/可以暂缓"）：
颜色只是判据的显示层。判据要是**在页面里再写一套**，过两周页面和日报就会互相说反话——
这类"同一件事两份账"本项目反复踩过（usable=17 与界面 0 张、界面写 0 元后端照样扣钱）。
所以：`scripts/daily_brief.py`（命令行）、`/api/plan/brief`（接口）、`web/static/schedule-brief.js`
（卡片上色）三处都调这一个 `build()`，谁都不自己算。

先说清这份判据**不管**什么：颜色只回答"今天先动哪一题"，是**排期与素材优先级**，
不等于"这篇已经能发"。文字、事实、关键帧、使用依据那四道审核在下游（`fact_gate`、`frame_pool`、
`guide_provenance`），本模块不看、也不冒充看过。2026-10-21 复核点名的就是这件事：
红色原来叫"今天就发"，读起来像攻略已经过了发布审核。

四条判据（顺序就是优先级）：
1. **发过没有** —— 台账 `publish_stats.slot_key` 精确命中算已确认；只有标题对上的算"疑似已发"，
   不装确定（2026-10-18 从 5180 导入的那批老行没有槽号）。
2. **窗口到没到** —— 排产页 `DATA.items[].date` 是计划发布日。
3. **素材新不新** —— 每格按它自己的 `search` 词现查 `videos` 表，过滤条件跟页面 `loadLive()` 一条不差
   （命中标题或作者、不早于 `since`、白名单 must 中、黑名单 block 不中、转写 ≥MIN_TL）。
   窗口是**今天和前两天这三个日历日**（`FRESH_DAYS=2` 往前推），不是精确 48 小时——
   库里 1258 条素材里 231 条只有日期没有时刻、120 条是空的、带时刻的那批也**没一条带时区**，
   拿它算"整 48 小时"是假精度。所以文案与算法一起说"今天和前两天"，别说 48h。
   窗口里没有新料的到期格子，判"缺新料"，不判"能发"。
4. **别扎堆** —— 台账里今天已经有行，那所有"原本会红"的格子一律改判**顺延**（`deferred`，
   独立一档，不并入橙色"缺新料"——那是两件事：一个有料今天不做，一个现在没料）。
   这条是**限流红线**：均匀排产那次把八成稿发在池子关闭期，而 72 小时内外发会掉首发补贴、
   连点几家平台还会踩"AI 批量发布"的判定。

Python 3.9 兼容：不用 match / X|Y 标注。
"""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "web" / "static" / "schedule.html"
DB = ROOT / "output" / "video2text.db"
NEWS_SHEET = "暴雪官方"          # 与 web/app.py 的 DB_ONLY_SHEETS["暴雪官方"]="blizzard" 同口径
ISO72_DAYS = 3                  # 头条首发 72 小时独占，按天算取 3 天窗口
FRESH_DAYS = 2                  # "新料"窗口：今天往前推 2 天 = 三个日历日（不是精确 48 小时）
FRESH_LABEL = "今天和前两天"     # 界面/日报/接口都念这一句，别各处自己写一个"48h"
MIN_TL = 200                    # 正文/转写短于这个数只算线索不占位（与排产页 MIN_TL 同值）

# 六档：key → (页面上的类名后缀, 一句话状态)。颜色含义必须写死在这里，页面只负责上色。
# 措辞是 2026-10-21 复核 R1 点名的：不许出现"今天就发/一定要发"这种读起来像"已过发布审核"的说法。
VERDICTS = {
    "red":       "今日优先处理",
    "deferred":  "今日名额已满，顺延",
    "orange":    "缺新料，先核或补",
    "yellow":    "疑似已发，请确认",
    "green":     "已发过，别再发",
    "grey":      "还没到",
}
# 每一档"为什么是这个颜色"的一句话（tooltip 与日报都用这份，前端不另写一套）
VERDICT_WHY = {
    "red": "到期、没发过、%s有可核的新素材 —— 今天先动这一题（只说排期，不说这篇已经能发）" % FRESH_LABEL,
    "deferred": "这一题本身到期也有料，但今天台账里已经登记发过一篇 —— 一天一篇，缓到明天",
    "orange": "到期了，可%s查不到新素材 —— 先补素材或按旧口径核一遍，别当今天的消息发" % FRESH_LABEL,
    "yellow": "台账里有标题对得上的行，但没填排产槽号 —— 你自己确认发过没有",
    "green": "已登记发过。72 小时内别外发（含小红书），否则掉首发补贴、还容易被判批量搬运",
    "grey": "还没到计划日，现在挂的素材只是选题依据",
}


def _ro(db: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_data(path: Optional[str] = None) -> Dict[str, Any]:
    """从排产页里把 DATA 常量抠出来（那份才是权威源，这里不抄第二份名单）。

    要说清的依赖：名单住在 `web/static/schedule.html` 里，而那份页面此刻**还没进版本库**
    （另一条会话的活）。所以干净检出里这个函数会抛——不是判据坏了，是没有名单可读。
    报错必须说人话，别让调用方（接口/CLI）只剩一句 FileNotFoundError。
    """
    p = Path(path or SCHEDULE)
    if not p.is_file():
        raise FileNotFoundError(
            "排产名单读不到：%s 不在（这份页面还没提交/没部署）——"
            "颜色与日报都没有名单可算，先补这份文件或改指一个存在的排产页，别拿空名单当"
            "「今天没题可发」" % str(p.relative_to(ROOT)))
    s = p.read_text(encoding="utf-8")
    i = s.index("var DATA = ") + len("var DATA = ")
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                break
    return json.loads(s[i:j + 1])


def _norm(t: str) -> str:
    return re.sub(r"[\s《》“”\"'’‘（）()：:，,。.\-—、/]+", "", str(t or ""))


def fresh_material(con: sqlite3.Connection, item: Dict[str, Any], meta: Dict[str, Any],
                   today: dt.date, limit: int = 4) -> Dict[str, Any]:
    """这一格现在能用的素材，逐条带新鲜度标记（页面按 status 上色，不再自己算日子）。"""
    try:
        must = re.compile(meta.get("must") or "(?!)", re.I)
    except re.error:
        must = re.compile("(?!)")
    try:
        block = re.compile(meta.get("block") or "(?!)")
    except re.error:
        block = re.compile("(?!)")
    since = item.get("since") or "2026-01-01"
    line = (today - dt.timedelta(days=FRESH_DAYS)).isoformat()
    found: Dict[str, Dict[str, Any]] = {}
    thin = 0
    for term in (item.get("search") or []):
        like = "%%%s%%" % term
        rows = con.execute(
            "SELECT source_sheet s, source_row r, title, author, published_at, "
            "length(transcript) AS tl FROM videos "
            "WHERE (title LIKE ? OR author LIKE ?) AND substr(published_at,1,10) >= ? "
            "ORDER BY published_at DESC LIMIT 80", (like, like, since)).fetchall()
        for x in rows:
            hay = "%s %s" % (x["title"] or "", x["author"] or "")
            if not must.search(hay) or block.search(hay):
                continue
            key = "%s:%s" % (x["s"], x["r"])
            if key in found:
                continue
            date = (x["published_at"] or "")[:10]
            if (x["tl"] or 0) < MIN_TL:
                thin += 1
                continue
            found[key] = {
                "sheet": x["s"], "row": str(x["r"]), "title": (x["title"] or "")[:40],
                "author": x["author"] or "", "date": date, "tl": x["tl"] or 0,
                # fresh=窗口内（今天和前两天这三个日历日）的新料
                # window=窗口外的旧料（能写但别当新闻）——别把这两个字读成"整 48 小时"
                "status": "fresh" if date >= line else "window",
            }
    out = sorted(found.values(), key=lambda v: (v["date"], v["tl"]), reverse=True)[:limit]
    newest = out[0]["date"] if out else ""
    # 窗口内一条都没有 = 这格现在没料（多半是搜索词太窄或素材超期，不是"库里没东西"）
    return {"list": out, "thin": thin, "newest": newest,
            "fresh": len([v for v in out if v["status"] == "fresh"]),
            "empty": not out}


def published_state(con: sqlite3.Connection, slot: str, title: str) -> Dict[str, Any]:
    """发过没有：槽号命中算确认，只有标题对上算疑似（两档分开说，不含混）。"""
    cols = {r[1] for r in con.execute("pragma table_info(publish_stats)")}
    out = {"state": "no", "note": ""}
    if "slot_key" in cols:
        r = con.execute("SELECT title, pub_date, channel FROM publish_stats "
                        "WHERE slot_key=? ORDER BY pub_date DESC LIMIT 1", (slot,)).fetchone()
        if r:
            out = {"state": "confirmed",
                   "note": "已登记 %s（%s《%s》）" % (r["pub_date"], r["channel"] or "-",
                                                     (r["title"] or "")[:22]),
                   "pub_date": r["pub_date"]}
    if out["state"] == "no":
        key = _norm(title)[:12]
        if key:
            r = con.execute("SELECT title, pub_date FROM publish_stats WHERE title LIKE ? "
                            "ORDER BY pub_date DESC LIMIT 1", ("%%%s%%" % key,)).fetchone()
            if r:
                out = {"state": "suspect",
                       "note": "标题对上 %s《%s》，没槽号 → 你自己确认" % (
                           r["pub_date"], (r["title"] or "")[:20])}
    if con.execute("SELECT count(*) FROM sqlite_master WHERE name='plan_tasks'").fetchone()[0]:
        r = con.execute("SELECT status, summary_id, channel, form FROM plan_tasks "
                        "WHERE slot_key=? ORDER BY version DESC LIMIT 1", (slot,)).fetchone()
        if r:
            out["task"] = "%s/%s %s%s" % (r["channel"], r["form"], r["status"],
                                          ("，稿 id=%s" % r["summary_id"]) if r["summary_id"] else "，还没稿")
    return out


def pipeline(con: sqlite3.Connection, today: dt.date) -> Dict[str, Any]:
    """这两天素材库进了多少条（分来源）——先回答"同步有没有在跑"，再谈该发什么。"""
    since = (today - dt.timedelta(days=FRESH_DAYS)).isoformat()
    rows = con.execute(
        "SELECT source_sheet, COUNT(*) n, MAX(substr(published_at,1,10)) mx FROM videos "
        "WHERE substr(published_at,1,10) >= ? GROUP BY source_sheet ORDER BY n DESC", (since,)).fetchall()
    return {"since": since, "total": sum(r["n"] for r in rows),
            "by_sheet": [{"sheet": r["source_sheet"] or "（没标来源）", "n": r["n"],
                          "newest": r["mx"]} for r in rows]}


def news_state(con: sqlite3.Connection, today: dt.date) -> Dict[str, Any]:
    row = con.execute("SELECT MAX(published_at) mx, COUNT(*) n FROM videos WHERE source_sheet=?",
                      (NEWS_SHEET,)).fetchone()
    latest = (row[0] or "")[:10]
    age = (today - dt.date.fromisoformat(latest)).days if latest else 999
    titles = [r[0] for r in con.execute(
        "SELECT title FROM videos WHERE source_sheet=? ORDER BY published_at DESC LIMIT 3",
        (NEWS_SHEET,))]
    return {"count": row[1] or 0, "latest": latest, "age_days": age, "titles": titles}


def build(db_path: str = "", today: Optional[dt.date] = None,
          data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """算出每一格的 verdict。三处调用方（CLI / 接口 / 页面）都拿这一份。"""
    db = str(db_path or DB)
    today = today or dt.date.today()
    data = data or load_data()
    meta = data.get("meta") or {}
    con = _ro(db)
    slots, news, pl = {}, news_state(con, today), pipeline(con, today)
    # 老库可能还没有 slot_key 列（那列是新代码第一次 ensure() 时才补的），
    # 判据不能因为列没补上就整个报错——少这一列只是"已发"那一档退化成按标题猜。
    has_slot = "slot_key" in {r[1] for r in con.execute("pragma table_info(publish_stats)")}
    published_today = [dict(r) for r in con.execute(
        "SELECT channel, title, pub_date%s FROM publish_stats WHERE pub_date=?"
        % (", slot_key" if has_slot else ""), (today.isoformat(),))]
    for it in (data.get("items") or []):
        d = dt.date.fromisoformat(it["date"])
        pub = published_state(con, it["id"], it.get("title") or "")
        fr = fresh_material(con, it, meta, today)
        due = d <= today
        if pub["state"] == "confirmed":
            key = "green"
        elif pub["state"] == "suspect":
            key = "yellow"
        elif not due:
            key = "grey"
        elif fr["fresh"]:
            key = "red"
        else:
            key = "orange"
        slots[it["id"]] = {
            "id": it["id"], "date": it["date"], "kind": it.get("kind") or "",
            "title": it.get("title") or "", "decision": it.get("decision") or "",
            "note": it.get("note") or "", "due": due, "days_left": (d - today).days,
            "published": pub, "fresh": fr, "verdict": key,
            "label": VERDICTS[key], "why": VERDICT_WHY[key], "demoted": "",
        }
    # 扎堆降级：今天台账里有行 → **所有**原本会红的格子一律改判"顺延"。
    # 2026-10-21 复核 R2 点名两个错：① 原来只改第二个以后的，只剩一个红槽时界面照样挂红；
    # ② 原来只填一句 demoted 说明、不改 verdict/label，于是颜色、日报分组、接口 label 各说各的。
    # 改判要连 verdict/label/why/颜色/分组一起改；但**不并入橙色**——
    # 顺延是"有料、今天不做了"，缺新料是"现在就没料"，混一档第二天没人分得清该补什么。
    if published_today:
        for s in sorted([x for x in slots.values() if x["verdict"] == "red"],
                        key=lambda x: (x["date"], x["id"])):
            s["verdict"] = "deferred"
            s["label"] = VERDICTS["deferred"]
            s["why"] = VERDICT_WHY["deferred"]
            s["demoted"] = ("今天已登记发过 %d 篇，这篇缓到明天：一天一篇别扎堆，"
                            "挤在同一天等于自己抢自己的首发流量" % len(published_today))
    con.close()
    order = {"red": 0, "deferred": 1, "orange": 2, "yellow": 3, "grey": 4, "green": 5}
    ranked = sorted(slots.values(), key=lambda s: (order[s["verdict"]], s["date"]))
    return {"today": today.isoformat(), "slots": slots, "ranked": ranked,
            "news": news, "pipeline": pl, "published_today": published_today,
            "gates": meta.get("gate") or "", "verdicts": VERDICTS,
            # 窗口口径与每档解释一起下发：页面与 CLI 念同一份，谁都不再自己写一句"48h"
            "fresh_label": FRESH_LABEL, "fresh_days": FRESH_DAYS, "verdict_why": VERDICT_WHY}


def render(b: Dict[str, Any]) -> str:
    """命令行那份中文输出（页面不读它，但日报和排查要看同一份文字）。"""
    order = {"red": "【今日优先处理】", "deferred": "【顺延：今天名额已经用过（有料，只是今天不做）】",
             "orange": "【缺新料，先核或补：别当今天的消息发】",
             "yellow": "【看着像发过，请你确认一眼】", "green": "【已登记发过：别再发】"}
    L = ["排产日报 · %s（判据：发过没有 / 窗口到没到 / 素材新不新 / 别扎堆）" % b["today"],
         "颜色只回答「今天先动哪一题」，是排期与素材优先级，**不等于这篇已经能发**："
         "文字、事实、关键帧、使用依据那四道审核在下游各闸里。",
         "新料窗口 = %s（三个日历日，按日期比，不是整 48 小时——素材库里成片的行只有日期没有时刻、"
         "带时刻的也不带时区，拿它算「整 48 小时」是假精度）" % FRESH_LABEL,
         "【素材库%s的进账】共 %d 条（自 %s）：%s" % (
             FRESH_LABEL, b["pipeline"]["total"], b["pipeline"]["since"],
             "、".join("%s %d 条（最新 %s）" % (x["sheet"], x["n"], x["newest"])
                       for x in b["pipeline"]["by_sheet"]) or "一条都没有——同步没跑，先去采集中心拉"),
         ""]
    pl = b["news"]
    if pl["age_days"] <= 1:
        L.append("【可插队】暴雪官方今天或昨天有新公告（最新 %s）：谁先转述谁吃首发那 72 小时，"
                 "优先于下面所有计划稿。" % pl["latest"])
    else:
        L.append("【可插队】没有新公告——官方公告最新一条是 %s（%d 天前，共 %d 条）。"
                 "要按这条走先抓一次：python3 -m src.blizzard_news" % (
                     pl["latest"], pl["age_days"], pl["count"]))
    L.append("")
    for key, head in order.items():
        rows = [s for s in b["ranked"] if s["verdict"] == key]
        if not rows:
            continue
        L.append(head)
        for s in rows:
            L.append("  · %s｜%s" % (s["id"], s["title"]))
            L.append("      计划日 %s｜%s｜%s" % (s["date"], s["kind"], s["decision"][:26]))
            if s["published"].get("note"):
                L.append("      %s" % s["published"]["note"])
            if s["demoted"]:
                L.append("      ⚠ %s" % s["demoted"])
            fr = s["fresh"]
            for v in fr["list"][:3]:
                L.append("      %s %s｜%s｜%s%s" % (
                    v["date"], v["author"][:12], v["title"], v["sheet"],
                    "（%s的新料）" % FRESH_LABEL if v["status"] == "fresh" else ""))
            if key == "orange":
                L.append("      素材最新一条 %s；现查可用 %d 条、太短的线索 %d 条" % (
                    fr["newest"] or "无", len(fr["list"]), fr["thin"]))
            if key == "deferred":
                L.append("      这一题的料是新的（%d 条），只是今天名额用完了 —— 明天照发，别当缺料" % fr["fresh"])
            if s["published"].get("task"):
                L.append("      排产任务：%s" % s["published"]["task"])
            L.append("")
    grey = [s for s in b["ranked"] if s["verdict"] == "grey"]
    if grey:
        L.append("【往后排】（一天一篇，别扎堆——自己抢自己的首发额度）")
        for s in grey:
            L.append("  %s  %s｜%s｜%s｜素材最新 %s%s" % (
                s["date"], s["id"], (s["kind"] or "")[:4], s["title"][:34],
                s["fresh"]["newest"] or "无",
                "（%s %d 条新料）" % (FRESH_LABEL, s["fresh"]["fresh"]) if s["fresh"]["fresh"] else ""))
        L.append("")
    if b["published_today"]:
        L.append("【今天已登记】%d 篇：%s" % (
            len(b["published_today"]),
            "、".join((r.get("title") or "")[:18] for r in b["published_today"])))
        L.append("")
    else:
        L.append("【今天已登记】台账里今天还没有行——发完记得去运营页登记并填排产槽号，"
                 "否则明天这份还会催你发同一篇。")
        L.append("")
    L.append("每篇四道门：%s" % b["gates"])
    L.append("72 小时内不外发（含小红书）是首发补贴与原创判定的硬条件；素材窗口没到的格子，"
             "现在挂的素材只是选题依据。")
    return "\n".join(L)
