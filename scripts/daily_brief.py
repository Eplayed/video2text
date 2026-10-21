# -*- coding: utf-8 -*-
"""daily_brief.py — 每天早上这份「必发 / 可插队 / 往后排」清单是怎么算出来的。

为什么要有这一条（2026-10-21 号主原话："最好是每天告诉我哪些是一定要发，哪些是优先级"）：
排产表（`web/static/schedule.html`）里那 11 条是**按计划要发的**，顶上那栏暴雪公告是**随时插队的时效料**，
两者不排同一个序；而"今天到底该动哪条"这件事，判据只有三条——**窗口到没到、发过没有、是不是决策题**。
这三条全都能机器算，不该每天早上靠人回忆。

三条纪律：
· **只读、零成本**：库用 `mode=ro`，不联网、不调模型、不写任何文件（除非 `--out` 显式要）；
· **判据跟页面同源**：槽位与日期读 `schedule.html` 里的 `DATA` 常量（那份才是权威源，
  这里不抄第二份名单），公告新鲜度按 `source_sheet='暴雪官方'` 查，跟排产页 `source=blizzard` 同一个口径；
· **"发过没"分两档，不装确定**：登记时带了 `slot_key` 的算**已确认**；只靠标题匹配的只敢说
  **"疑似已发，请确认"**——台账里那 136 行历史数据没有槽位号，硬判会把重复提醒当成事实。

用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/daily_brief.py
  ... --date 2026-11-05 --json        # 指定日期 / 出机器可读那份
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / "web" / "static" / "schedule.html"
DB = ROOT / "output" / "video2text.db"
NEWS_SHEET = "暴雪官方"          # 与 web/app.py 的 DB_ONLY_SHEETS["暴雪官方"]="blizzard" 同口径
ISO72_DAYS = 3                  # 头条首发 72 小时独占，按天算取 3 天窗口
MIN_TL = 200                    # 正文/转写短于这个数只算"线索"不占位（与排产页 MIN_TL 同值）


def load_data():
    """从排产页里把 DATA 常量抠出来（不复制第二份名单，页面改了这里立刻跟着改）。"""
    s = SCHEDULE.read_text(encoding="utf-8")
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


def norm(t):
    return re.sub(r"[\s《》“”\"'’‘（）()：:，,。.\-—、/]+", "", str(t or ""))


def fresh_material(db, item, meta, today, limit=4):
    """这一格**现在**能用的素材：跟排产页 `loadLive()` 同一套判据，只是不走 HTTP。

    页面上那份是 fetch `/api/videos?q=…` 现查的（走的是索引，只收有 ASR 的行）；
    日报要能在工作台没起的时候也跑得动，所以直接查 `videos` 表，但**过滤条件一条不改**：
    搜索词命中标题或作者 → 日期不早于这格的 `since` → 白名单 must 命中、黑名单 block 不中
    → 正文（转写）长度 ≥ MIN_TL。差一条就会变成"日报说有料、页面点开是空的"。
    """
    try:
        must = re.compile(meta.get("must") or "(?!)", re.I)
    except re.error:
        must = re.compile("(?!)")
    try:
        block = re.compile(meta.get("block") or "(?!)")
    except re.error:
        block = re.compile("(?!)")
    since = item.get("since") or "2026-01-01"
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    found, thin = {}, 0
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
            if (x["tl"] or 0) < MIN_TL:
                thin += 1
                continue
            found[key] = {"sheet": x["s"], "row": str(x["r"]), "title": (x["title"] or "")[:34],
                          "author": x["author"] or "", "date": (x["published_at"] or "")[:10],
                          "tl": x["tl"] or 0}
    con.close()
    out = sorted(found.values(), key=lambda v: (v["date"], v["tl"]), reverse=True)[:limit]
    newest = out[0]["date"] if out else ""
    fresh2 = [v for v in out if v["date"] >= (today - dt.timedelta(days=2)).isoformat()]
    return {"list": out, "thin": thin, "newest": newest, "fresh48": len(fresh2)}


def pipeline(db, today):
    """这两天整个素材库进了多少新料（分来源）——日报开头先说这个，
    不然"今天没有必发"会被读成"素材库没在跑"。"""
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = con.execute(
        "SELECT source_sheet, COUNT(*) n, MAX(substr(published_at,1,10)) mx FROM videos "
        "WHERE substr(published_at,1,10) >= ? GROUP BY source_sheet ORDER BY n DESC",
        ((today - dt.timedelta(days=2)).isoformat(),)).fetchall()
    con.close()
    return {"since": (today - dt.timedelta(days=2)).isoformat(),
            "by_sheet": [{"sheet": r[0] or "（没标来源）", "n": r[1], "newest": r[2]} for r in rows],
            "total": sum(r[1] for r in rows)}


def published_by(db, slot, title, day):
    """这一格发过没有。返回 (状态, 说明)；状态 ∈ confirmed / suspect / no。"""
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("pragma table_info(publish_stats)")]
    out = ("no", "")
    if "slot_key" in cols:
        r = con.execute("SELECT title, pub_date, channel FROM publish_stats "
                        "WHERE slot_key=? ORDER BY pub_date DESC LIMIT 1", (slot,)).fetchone()
        if r:
            out = ("confirmed", "已登记 %s（%s《%s》）" % (
                r["pub_date"], r["channel"] or "-", (r["title"] or "")[:22]))
    if out[0] == "no":
        key = norm(title)[:12]
        like = "%%%s%%" % key if key else None
        r = con.execute("SELECT title, pub_date, channel FROM publish_stats "
                        "WHERE title LIKE ? ORDER BY pub_date DESC LIMIT 1", (like,)).fetchone() if like else None
        if r:
            out = ("suspect", "疑似已发（标题对上 %s《%s》，没有槽位号，请自己确认）" % (
                r["pub_date"], (r["title"] or "")[:20]))
    # 排产任务这一格生成过稿没有（有稿没登记 = 卡在中间，最该提醒）
    tail = ""
    if con.execute("select count(*) from sqlite_master where name='plan_tasks'").fetchone()[0]:
        r = con.execute("SELECT status, summary_id, channel, form FROM plan_tasks "
                        "WHERE slot_key=? ORDER BY version DESC LIMIT 1", (slot,)).fetchone()
        if r:
            tail = "｜排产任务：%s/%s，状态 %s%s" % (
                r["channel"], r["form"], r["status"],
                "，稿 id=%s" % r["summary_id"] if r["summary_id"] else "，还没稿")
    con.close()
    return (out[0], out[1] + tail)


def news_fresh(db, today):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    row = con.execute("SELECT MAX(published_at) mx, COUNT(*) n FROM videos WHERE source_sheet=?",
                      (NEWS_SHEET,)).fetchone()
    latest = (row[0] or "")[:10]
    n = row[1] or 0
    if latest:
        d = dt.date.fromisoformat(latest)
        age = (today - d).days
    else:
        age = 999
    newest_titles = [r[0] for r in con.execute(
        "SELECT title FROM videos WHERE source_sheet=? ORDER BY published_at DESC LIMIT 3", (NEWS_SHEET,))]
    con.close()
    return {"count": n, "latest": latest, "age_days": age, "titles": newest_titles}


def iso72(db, today):
    """近 3 天发过的头条稿：72 小时内别外发（含小红书），这条最容易被自己踩。"""
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = con.execute(
        "SELECT pub_date, channel, title FROM publish_stats "
        "WHERE pub_date >= ? ORDER BY pub_date DESC LIMIT 8",
        ((today - dt.timedelta(days=ISO72_DAYS)).isoformat(),)).fetchall()
    con.close()
    return rows


def build(db=DB, today=None):
    today = today or dt.date.today()
    data = load_data()
    items = data.get("items") or []
    must, suspect, queue, done = [], [], [], []
    for it in items:
        d = dt.date.fromisoformat(it["date"])
        st, note = published_by(db, it["id"], it.get("title") or "", today)
        fr = fresh_material(db, it, data.get("meta") or {}, today)
        row = {"id": it["id"], "date": it["date"], "kind": it.get("kind") or "",
               "title": it.get("title") or "", "decision": it.get("decision") or "",
               "note": it.get("note") or "", "published": note, "fresh": fr}
        if st == "confirmed":
            done.append(row)
        elif d <= today:
            (suspect if st == "suspect" else must).append(row)
        else:
            queue.append(row)
    queue.sort(key=lambda r: r["date"])
    return {"today": today.isoformat(), "must": must, "suspect": suspect,
            "queue": queue, "done": done, "pipeline": pipeline(db, today),
            "news": news_fresh(db, today),
            "iso72": [{"date": r[0], "channel": r[1], "title": (r[2] or "")[:30]}
                      for r in iso72(db, today)],
            "gates": data["meta"].get("gate") or ""}


def render(b):
    L = []
    L.append("排产日报 · %s（判据：窗口到没到 / 发过没有 / 素材新不新 / 是不是决策题）" % b["today"])
    pl = b.get("pipeline") or {}
    L.append("【素材库这两天的进账】共 %d 条（自 %s）：%s" % (
        pl.get("total", 0), pl.get("since", "-"),
        "、".join("%s %d 条（最新 %s）" % (x["sheet"], x["n"], x["newest"])
                  for x in (pl.get("by_sheet") or [])) or "一条都没有——同步没跑，先去采集中心拉"))
    L.append("")
    if b["must"]:
        L.append("【今天一定要发】")
        for r in b["must"]:
            L.append("  · %s｜%s" % (r["id"], r["title"]))
            L.append("      计划日 %s｜%s｜%s" % (r["date"], r["kind"], r["decision"][:26]))
            fr = r.get("fresh") or {}
            if fr.get("list"):
                L.append("      能用的素材（现查，最新在前）：")
                for v in fr["list"][:3]:
                    L.append("        %s %s｜%s｜%s" % (v["date"], v["author"][:12], v["title"], v["sheet"]))
                if not fr.get("fresh48"):
                    L.append("      ⚠ 但这些都不是 48 小时内的新料（最新 %s）——"
                             "先跑一次同步补素材，或按旧口径写、别当今天的消息发" % fr.get("newest"))
            else:
                L.append("      ⚠ 现查没有可用素材（正文≥%d 字、过白名单）——这篇要么先补素材要么别发" % MIN_TL)
            if r["note"]:
                L.append("      备注：%s" % r["note"][:46])
    else:
        L.append("【今天一定要发】没有——到期的格子都发过了或还没到窗口，今天不用硬发。")
    if b["suspect"]:
        L.append("")
        L.append("【看着像发过，请你确认一眼】")
        for r in b["suspect"]:
            L.append("  · %s｜%s｜%s" % (r["id"], r["title"][:30], r["published"]))
    n = b["news"]
    L.append("")
    if n["age_days"] <= 1:
        L.append("【可插队】暴雪官方 24 小时内有新公告（最新 %s）：谁先转述谁吃首发那 72 小时，"
                 "优先于下面所有计划稿。" % n["latest"])
        for t in n["titles"][:2]:
            L.append("      · %s" % t[:40])
    else:
        L.append("【可插队】没有新公告——库里官方公告最新一条是 %s（%d 天前，共 %d 条）。"
                 "要按这条走先抓一次：python3 -m src.blizzard_news" % (n["latest"], n["age_days"], n["count"]))
    if b["queue"]:
        L.append("")
        L.append("【往后排】（一天一篇，别扎堆——自己抢自己的首发额度）")
        for r in b["queue"]:
            fr = r.get("fresh") or {}
            L.append("  %s  %s｜%s｜%s｜素材最新 %s%s" % (
                r["date"], r["id"], (r["kind"] or "")[:4], r["title"][:34],
                fr.get("newest") or "无",
                "（48h 内有 %d 条新料）" % fr["fresh48"] if fr.get("fresh48") else ""))
    if b["done"]:
        L.append("")
        L.append("【已发（登记里带槽位号）】%s" % "、".join(r["id"] for r in b["done"]))
    if b["iso72"]:
        L.append("")
        L.append("【72 小时独占里，别外发（含小红书）】")
        for r in b["iso72"]:
            L.append("  %s %s｜%s" % (r["date"], r["channel"], r["title"]))
    L.append("")
    L.append("每篇四道门：%s" % b["gates"])
    L.append("素材窗口没到的格子，现在挂的素材只是选题依据——到日子再去采集中心拉新的。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="按哪天算（默认今天），格式 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    b = build(a.db, today)
    if a.json:
        print(json.dumps(b, ensure_ascii=False, indent=1))
    else:
        print(render(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
