"""ops_store.py — 运营数据回流：每篇的真实表现 + 每周那两个数（涨粉、钱）。

为什么要有这一层：2026-10-08 取证，公众号线发布停在 09-21、真实阅读 4 和 5，
「每周只看两个数」这件事在系统里**没有任何一个地方能填**——台账（media-workbench
runtime/publications.json）只登记发布动作，feedback 字段是 09-17 手工回填过一次的就再没动。
没有尺子，"先涨粉"就是一句感觉。

为什么是手填而不是自动抓：
  · 个人主体未认证号没有官方数据接口；
  · 内置浏览器打开 mp.weixin.qq.com 是扫码页（没有他的登录态，实测过）；
  · 同类看板（We分析）的取数接口是 wasm 加解密，不能 replay，导出点了不落盘——
    公众号后台大概率同一类坑。所以这里**不写没实测过的爬虫**，先做一分钟能填完的录入，
    取数那一步等真找到可用登录态再补（表里留了 source 字段区分来源）。

能自动的部分照做：从旧台账（media-workbench/runtime/publications.json）导入已登记的
发布记录（滤掉 [测试] 那条），从审核台把标记「已发布」的图文包补成空行——他只需要填数，
不用自己想起发过什么。

2026-10-18：5180 那个服务已经死了（无监听、数据冻在 09-24），**发布登记这一格搬到本表**。
所以这里同时是"历史台账的接收方"和"以后唯一的人工登记口"（`register_publish`）。
旧台账里 96 条头条记录带着 2026-04-14→09-17 的展现/阅读/点赞/评论，是半年账本的唯一副本，
`import_ledger` 从"只导微信 4 条"改成全渠道导，撞键或撞标题的合并进现有行、只填空不覆盖。

口径提醒：台账字段名叫 reads24h，实际存的是**累计阅读**（2026-10-06 取证），
导入时按累计读，别再当 24 小时数用。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

LEDGER = os.path.expanduser(
    "~/Documents/project/media-workbench/runtime/publications.json")
CHANNELS = {"wechat": "公众号", "toutiao": "头条", "xiaohongshu": "小红书"}
# 同一列在不同渠道的后台叫什么，写在这里一份，界面按这张表出表头——
# 以前表头写死"阅读/在看/转发"（那是公众号的词），头条的"点赞"、小红书的"收藏"塞进去就是假标签
# （2026-10-14 要做每日回填时撞上的）。"—" 表示那个后台根本没有这个指标。
COLUMN_LABELS = {
    "show":       {"wechat": "—", "toutiao": "展现", "xiaohongshu": "曝光"},
    "reads":      {"wechat": "阅读", "toutiao": "阅读", "xiaohongshu": "浏览"},
    "looks":      {"wechat": "在看", "toutiao": "点赞", "xiaohongshu": "点赞"},
    "comment":    {"wechat": "—", "toutiao": "评论", "xiaohongshu": "评论"},
    "saves":      {"wechat": "—", "toutiao": "—", "xiaohongshu": "收藏"},
    "shares":     {"wechat": "分享", "toutiao": "转发", "xiaohongshu": "分享"},
    "net_follow": {"wechat": "涨粉", "toutiao": "涨粉", "xiaohongshu": "涨粉"},
    "revenue":    {"wechat": "钱(元)", "toutiao": "钱(元)", "xiaohongshu": "钱(元)"},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_stats (
  channel TEXT NOT NULL, ref_id TEXT NOT NULL,
  title TEXT, pub_date TEXT,
  reads INTEGER, looks INTEGER, shares INTEGER, net_follow INTEGER, revenue REAL,
  show_n INTEGER, comment INTEGER, saves INTEGER,
  source TEXT, note TEXT, url TEXT, updated_at TEXT,
  PRIMARY KEY (channel, ref_id)
);
CREATE TABLE IF NOT EXISTS week_stats (
  week TEXT PRIMARY KEY,
  followers_total INTEGER, followers_new INTEGER, revenue REAL,
  note TEXT, updated_at TEXT
)
"""
# 允许前端提交的数字字段（白名单，避免把 SQL 列名当参数拼出去）
# `show` 是 SQL 保留字，库里那一列叫 show_n，对外（接口与界面）仍叫 show，调用方不用知道
# url 是 2026-10-18 加的：发布登记要能带回平台原文链接，不然"发了哪篇"还是只能靠标题猜
ARTICLE_FIELDS = ("title", "pub_date", "reads", "looks", "shares", "net_follow",
                  "revenue", "show", "comment", "saves", "source", "note", "url")
_COL_ALIAS = {"show": "show_n"}
WEEK_FIELDS = ("followers_total", "followers_new", "revenue", "note")
# 表单交上来的都是字符串；空串按"没填"存 NULL，不存 0——0 会被读成一个结论
NUM_FIELDS = {"reads": int, "looks": int, "shares": int, "net_follow": int,
              "revenue": float, "show": int, "comment": int, "saves": int,
              "followers_total": int, "followers_new": int}


def _clean(fields: Dict[str, Any], allowed) -> Dict[str, Any]:
    out = {}
    for k, v in ((k, fields[k]) for k in fields if k in allowed):
        if k in NUM_FIELDS:
            if v is None or (isinstance(v, str) and not v.strip()):
                out[k] = None
                continue
            try:
                out[k] = NUM_FIELDS[k](float(v))
            except (TypeError, ValueError):
                raise ValueError("%s 要填数字，现在填的是「%s」" % (k, v))
        else:
            out[k] = str(v if v is not None else "").strip()
    return out


def ensure(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        # 老库补列：CREATE TABLE IF NOT EXISTS 对已存在的表不生效，
        # 2026-10-14 加了 展现/评论/收藏 三列，不补一次的话所有写入都会 "no such column"
        have = {r[1] for r in conn.execute("PRAGMA table_info(publish_stats)").fetchall()}
        for col, typ in (("show_n", "INTEGER"), ("comment", "INTEGER"), ("saves", "INTEGER"),
                         ("url", "TEXT")):
            if col not in have:
                conn.execute("ALTER TABLE publish_stats ADD COLUMN %s %s" % (col, typ))
        conn.commit()
    finally:
        conn.close()


def _col(name: str) -> str:
    """对外字段名 → 库里的列名（只有 show 这一个例外，show 是 SQL 保留字）。"""
    return _COL_ALIAS.get(name, name)


def _row_out(row: Dict[str, Any]) -> Dict[str, Any]:
    if "show_n" in row:
        row["show"] = row.pop("show_n")
    return row


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def week_of(day: Optional[str] = None) -> str:
    """给任意日期返回它所在周的周一（YYYY-MM-DD）。周是这里的最小复盘单位。"""
    try:
        d = datetime.strptime((day or "")[:10], "%Y-%m-%d")
    except ValueError:
        d = datetime.now()
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def list_articles(db_path: str, channel: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure(db_path)
    conn = sqlite3.connect(db_path)
    try:
        if channel:
            rows = _rows(conn, "SELECT * FROM publish_stats WHERE channel=? "
                               "ORDER BY COALESCE(pub_date,'') DESC, channel, ref_id", (channel,))
        else:
            rows = _rows(conn, "SELECT * FROM publish_stats ORDER BY "
                               "COALESCE(pub_date,'') DESC, channel, ref_id")
    finally:
        conn.close()
    for r in rows:
        r["channel_label"] = CHANNELS.get(r.get("channel"), r.get("channel"))
        r["week"] = week_of(r.get("pub_date"))
    return [_row_out(r) for r in rows]


def upsert_article(db_path: str, channel: str, ref_id: str,
                   fields: Dict[str, Any]) -> Dict[str, Any]:
    """一行明细。只认白名单字段；没提交的字段保持原值（表单里留空不等于清零）。"""
    ensure(db_path)
    cols = _clean(fields, ARTICLE_FIELDS)
    conn = sqlite3.connect(db_path)
    try:
        exist = _rows(conn, "SELECT * FROM publish_stats WHERE channel=? AND ref_id=?",
                      (channel, str(ref_id)))
        if exist:
            if cols:
                sets = ", ".join("%s=?" % _col(k) for k in cols)
                conn.execute("UPDATE publish_stats SET %s, updated_at=? "
                             "WHERE channel=? AND ref_id=?" % sets,
                             tuple(cols.values()) + (_now(), channel, str(ref_id)))
        else:
            names = ["channel", "ref_id", "updated_at"] + [_col(k) for k in cols]
            vals = [channel, str(ref_id), _now()] + list(cols.values())
            conn.execute("INSERT INTO publish_stats (%s) VALUES (%s)"
                         % (", ".join(names), ", ".join("?" * len(names))), tuple(vals))
        conn.commit()
        row = _rows(conn, "SELECT * FROM publish_stats WHERE channel=? AND ref_id=?",
                    (channel, str(ref_id)))
    finally:
        conn.close()
    out = _row_out(row[0] if row else {})
    out["channel_label"] = CHANNELS.get(channel, channel)
    out["week"] = week_of(out.get("pub_date"))
    return out


def register_publish(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """人工登记一条"这篇发出去了"。5180 死了以后这是唯一的登记口（发布动作本身永远是人工那一下）。

    为什么判据写在这里而不是界面里：前端抄一份校验迟早和后端对不上（本项目反复踩过的静默失效）。
    几条口径：
      · 标题必填 —— 没有标题的行事后没法复盘；
      · 日期没填按今天算（登记的就是"今天发了"），填了必须是 YYYY-MM-DD，不猜也别静默改；
      · 没给 ref_id 时先生成 `reg:<时间戳>`，但**同渠道同标题同一天已有行就复用那一把键**，
        免得连点两次在台账里长出两条重复；
      · 数字列一概不写 0：登记时通常还没有数据，留 NULL 等回填来补（0 会被读成"阅读 0"这个结论）。
    """
    channel = str(payload.get("channel") or "").strip()
    # 界面可能交来键（toutiao）也可能交来中文名（头条），两个都收，但库里只存键
    if channel not in CHANNELS:
        channel = next((k for k, v in CHANNELS.items() if v == channel), "")
    if not channel:
        raise ValueError("渠道要选 %s 之一" % " / ".join(CHANNELS.values()))
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("标题不能空——台账里没有标题的行没法复盘")
    pub_date = str(payload.get("pub_date") or "").strip()[:10]
    if not pub_date:
        pub_date = datetime.now().strftime("%Y-%m-%d")
    elif not re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
        raise ValueError("发布日期要写成 2026-10-18 这样，现在填的是「%s」" % pub_date)
    ref_id = str(payload.get("ref_id") or "").strip()
    if not ref_id:
        same = [a for a in list_articles(db_path, channel)
                if (a.get("title") or "").strip() == title and (a.get("pub_date") or "") == pub_date]
        ref_id = same[0]["ref_id"] if same else "reg:%s" % datetime.now().strftime("%Y%m%d%H%M%S")
    fields = {"title": title, "pub_date": pub_date, "source": "人工登记"}
    for key in ("url", "note"):
        val = str(payload.get(key) or "").strip()
        if val:
            fields[key] = val
    existed = bool(_existing(db_path, channel, ref_id))
    row = upsert_article(db_path, channel, ref_id, fields)
    return {"row": row, "created": not existed, "ref_id": ref_id}


def list_weeks(db_path: str, limit: int = 12) -> List[Dict[str, Any]]:
    ensure(db_path)
    conn = sqlite3.connect(db_path)
    try:
        return _rows(conn, "SELECT * FROM week_stats ORDER BY week DESC LIMIT ?", (limit,))
    finally:
        conn.close()


def upsert_week(db_path: str, week: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    ensure(db_path)
    week = week_of(week)
    cols = _clean(fields, WEEK_FIELDS)
    conn = sqlite3.connect(db_path)
    try:
        if _rows(conn, "SELECT week FROM week_stats WHERE week=?", (week,)):
            if cols:
                sets = ", ".join("%s=?" % k for k in cols)
                conn.execute("UPDATE week_stats SET %s, updated_at=? WHERE week=?" % sets,
                             tuple(cols.values()) + (_now(), week))
        else:
            names = ["week", "updated_at"] + list(cols)
            vals = [week, _now()] + list(cols.values())
            conn.execute("INSERT INTO week_stats (%s) VALUES (%s)"
                         % (", ".join(names), ", ".join("?" * len(names))), tuple(vals))
        conn.commit()
        rows = _rows(conn, "SELECT * FROM week_stats WHERE week=?", (week,))
    finally:
        conn.close()
    return rows[0] if rows else {}


def report(db_path: str, week: Optional[str] = None) -> Dict[str, Any]:
    """周报：只回答两个数——这周涨了多少粉、哪篇带来钱。

    没填的地方一律写"未填"而不是补 0：0 会被读成"这周涨粉 0"，那是个结论。
    """
    wk = week_of(week)
    weeks = {r["week"]: r for r in list_weeks(db_path, limit=200)}
    cur = weeks.get(wk) or {}
    nxt = weeks.get(week_of((datetime.strptime(wk, "%Y-%m-%d")
                             - timedelta(days=7)).strftime("%Y-%m-%d"))) or {}
    arts = [a for a in list_articles(db_path) if a.get("week") == wk]
    money = [a for a in arts if a.get("revenue")]
    reads = [a for a in arts if a.get("reads")]
    return {
        "week": wk,
        "followers_new": cur.get("followers_new"),
        "followers_total": cur.get("followers_total"),
        "followers_new_prev": nxt.get("followers_new"),
        "week_revenue": cur.get("revenue"),
        "week_updated_at": cur.get("updated_at") or "",
        "week_note": cur.get("note") or "",
        "article_count": len(arts),
        "reads_sum": sum(a["reads"] or 0 for a in reads) if reads else None,
        "best_read": max(arts, key=lambda a: a.get("reads") or 0)["title"] if reads else "",
        "money_article": max(money, key=lambda a: a.get("revenue") or 0)["title"] if money else "",
        "money_sum": round(sum(a["revenue"] or 0 for a in money), 2) if money else None,
        "articles": arts,
        "unfilled": not cur.get("followers_new") and cur.get("followers_new") != 0,
    }


def _ledger_ref_id(entries_title: str, channel: str, item_id: str, entry_id: str,
                   existing: List[Dict[str, Any]]) -> str:
    """旧台账一条记录该落在哪把键上。

    直接按 link 里的头条 id 建 `tt:<id>` 是最省事的，但实测新回填那 31 行用的键是
    `tt:<gid>`（gid 与 item id 只有 6 条撞上），照 link 建键会让同一篇稿子在台账里长成两行
    ——他看台账第一眼看的就是篇数，重复行等于把数读错。所以判两遍：
    ① 库里已有同标题同渠道的行 → 用它那把键（合并）；② 否则用 link 里的 id；③ 再否则 `ledger:<旧 id>`。
    """
    t = (entries_title or "").strip()
    for row in existing:
        if (row.get("title") or "").strip() == t and t:
            return row["ref_id"]
    if channel == "toutiao" and item_id:
        return "tt:%s" % item_id
    return "ledger:%s" % (item_id or entry_id or t[:16])


def import_ledger(db_path: str, ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """把旧台账（5180 那份 publications.json）整本接进本表，再按审核台「已发布」补空行。

    2026-10-18 从"只导微信 4 条"改成**全渠道**：那边 96 条头条记录是 2026-04-14→09-17
    唯一的半年账本（展现/阅读/点赞/评论/点击率都在 feedback 里），号主打算删掉那个仓库，
    不搬就等于把半年数据一起删了。
    只补空不覆盖：人工后录的数比台账旧快照准；已有人工行时只填 title/pub_date/url 这种描述字段。
    数字一律"非零才写"：旧台账 shares24h 96 条全 0，写进去就成了"他转发都是 0"这个结论。
    """
    path = ledger_path or LEDGER
    out = {"ledger_file": path, "created": 0, "merged": 0, "skipped": [], "seeded": 0,
           "error": "", "channels": {}}
    if not os.path.isfile(path):
        out["error"] = "找不到台账：%s" % path
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
    except ValueError as e:
        out["error"] = "台账读不了：%s" % e
        return out
    if not isinstance(entries, list):
        out["error"] = "台账格式不是列表，没敢动"
        return out
    for p in entries:
        channel = (p.get("channel") or "").strip()
        title = (p.get("title") or "").strip()
        if channel not in CHANNELS:
            out["skipped"].append("未知渠道：%s（%s）" % (channel or "空", title[:20]))
            continue
        if title.startswith("[测试]") or title.startswith("【测试】") or "端到端验证" in title:
            out["skipped"].append("测试条目：%s" % title[:24])
            continue
        if not title:
            out["skipped"].append("没标题的条目：%s" % (p.get("id") or "?"))
            continue
        link = (p.get("link") or p.get("url") or "").strip()
        m = re.search(r"/(?:item|w|article)/(\d{10,20})", link)
        item_id = m.group(1) if m else ""
        rid = _ledger_ref_id(title, channel, item_id, p.get("id") or "",
                             list_articles(db_path, channel))
        fb = p.get("feedback") or {}
        pub = (p.get("publishedAt") or p.get("articleDate") or "")[:10]
        fields = {"title": title, "pub_date": pub, "source": "旧台账"}
        if link:
            fields["url"] = link
        # reads24h 实为累计阅读（2026-10-06 取证），按累计读
        if fb.get("reads24h"):
            fields["reads"] = int(fb["reads24h"])
        if fb.get("impressions"):
            fields["show"] = int(fb["impressions"])
        if fb.get("looking24h"):          # 公众号的"在看"
            fields["looks"] = int(fb["looking24h"])
        if fb.get("likes24h"):            # 头条的"点赞"落在同一列
            fields["looks"] = int(fb["likes24h"])
        if fb.get("comments24h"):
            fields["comment"] = int(fb["comments24h"])
        if fb.get("shares24h"):
            fields["shares"] = int(fb["shares24h"])
        if fb.get("saves24h"):
            fields["saves"] = int(fb["saves24h"])
        if fb.get("followers"):
            fields["net_follow"] = int(fb["followers"])
        if fb:
            bits = ["旧台账 recordedAt %s" % str(fb.get("recordedAt") or "")[:10]]
            if fb.get("ctr24h"):
                bits.append("点击率 %.2f%%" % (float(fb["ctr24h"]) * 100))
            note = "·".join(bits)
            if (p.get("note") or "").strip():
                note = "%s（%s）" % (note, str(p.get("note")).strip()[:60])
            fields["note"] = note
        existed = _existing(db_path, channel, rid)
        upsert_article(db_path, channel, rid, _merge_only_empty(existed, fields))
        out["channels"][CHANNELS[channel]] = out["channels"].get(CHANNELS[channel], 0) + 1
        if existed:
            out["merged"] += 1
        else:
            out["created"] += 1
    out["seeded"] = seed_from_reviews(db_path)
    return out


def _existing(db_path: str, channel: str, ref_id: str) -> Dict[str, Any]:
    ensure(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = _rows(conn, "SELECT * FROM publish_stats WHERE channel=? AND ref_id=?",
                     (channel, ref_id))
    finally:
        conn.close()
    # 这里的键要和 fields 的键同名（show 而不是 show_n），不然"只填空位"会把已填的展现当成空覆盖掉
    return _row_out(rows[0]) if rows else {}


def _merge_only_empty(existed: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """台账导入只填空着的位置；人工填过的数一律保留。"""
    if not existed:
        return fields
    keep = {}
    for k, v in fields.items():
        if k in ("title", "pub_date", "source", "note"):
            if not (existed.get(k) or "").strip():
                keep[k] = v
        elif existed.get(k) in (None, ""):
            keep[k] = v
    return keep


def seed_from_reviews(db_path: str) -> int:
    """审核台里标了「已发布」但还没有数据行的图文包，补一行空的等他填数。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                     "AND name='content_reviews'").fetchone()
        rows = _rows(conn, "SELECT kind, ref_id, published_at FROM content_reviews "
                           "WHERE status='published'")
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    n = 0
    for r in rows:
        channel = "wechat" if r["kind"] == "wechat" else "toutiao"
        if _existing(db_path, channel, str(r["ref_id"])):
            continue
        upsert_article(db_path, channel, str(r["ref_id"]),
                       {"pub_date": (r["published_at"] or "")[:10], "source": "审核台已发布"})
        n += 1
    return n
