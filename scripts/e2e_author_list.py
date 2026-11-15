# -*- coding: utf-8 -*-
"""素材库「全部作者」名单的契约回归（隔离临时库 + 隔离索引，真 app.test_client，不碰生产库）。

号主 2026-10-28：「全部作者里又多了好多我没订阅的，从第一性原理修复」。
量出来的根因有两层，这份脚本就按这两层各设一道闸：
  ① 名单曾经直接对整张 videos 表 group by，把库里 NGA 板块那 1105 行按**发帖人**展开成
     814 项列进下拉——论坛署名不是内容来源，且这批行压根不在素材库列表的可见集里；
  ② 于是"下拉声称 1105 条、点下去 0 条"，名单与列表是两份账。

定死的不变式（改 _material_rows / _author_topics / DB_ONLY_SHEETS / FIRST_PARTY_SHEETS 都要回来跑）：
  · 名单每一项 = 订阅表订到的账号，或一手源那种整表一个账号在发的账号；
  · 名单每一项点下去筛得到的条数 == 名单声称的条数（不许出现 0 条的来源项）；
  · 论坛发帖人不进名单；库里已入库但没接进列表的源，整表另报一堆条数（不藏数，也不冒充来源）；
  · 抽公共装配函数没改变 /api/videos 原有的来源/分类/关键词筛选与响应字段。

用法：python3 scripts/e2e_author_list.py        （全过打印 ALL_OK，退出码 0）
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "XX ", name, ("  " + detail) if detail else ""))


VID_COLS = ("id, aweme_id, source_sheet, source_row, source_url, video_url, cover_url, author,"
            " title, status, published_at, tags, transcript, remark, category, game,"
            " author_sec_uid, source, view_count, duration_sec, subs_status, note_images,"
            " note_image_count, ai_tags")
Q = ",".join("?" for _ in VID_COLS.split(","))


def make_db(path):
    con = sqlite3.connect(str(path))
    # 列的类型照真库：source_row 必须是整型，否则和索引里的 row（整数）配不上对，
    # db_id/category 这些关联字段会整片取不到——那是夹具的错，不是被测代码的错。
    ints = {"id": "INTEGER PRIMARY KEY", "source_row": "INTEGER",
            "view_count": "INTEGER", "duration_sec": "INTEGER", "note_image_count": "INTEGER"}
    con.execute("CREATE TABLE videos (%s)" % ", ".join(
        "%s %s" % (c.strip(), ints.get(c.strip(), "TEXT")) for c in VID_COLS.split(",")))
    con.execute("CREATE TABLE ai_summaries (id INTEGER PRIMARY KEY, video_id INT,"
                " summary_type TEXT, title TEXT, content TEXT, status TEXT,"
                " source_video_ids TEXT, structured_data TEXT,"
                " created_at TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, author TEXT, platform TEXT)")
    con.row_factory = sqlite3.Row

    def vid(vid_id, sheet, row, author, source="", title="素材"):
        con.execute("INSERT INTO videos (%s) VALUES (%s)" % (VID_COLS, Q),
                    (vid_id, "aw%d" % vid_id, sheet, row, "https://example.com/%d" % vid_id,
                     "", "", author, title, "已完成", "2026-10-%02d" % (row % 28 + 1), "",
                     "正文正文", "", "游戏攻略" if sheet == "抖音视频数据" else "", "魔兽世界：无限",
                     "", source, 0, 0, "", "[]", 0, ""))

    # 订阅到的账号：抖音两条（走 Excel 索引）、微信三条（走 DB 直写）
    for i, (row, author) in enumerate([(1, "订到的甲"), (2, "订到的甲"), (3, "订到的乙")]):
        con.execute("INSERT INTO videos (%s) VALUES (%s)" % (VID_COLS, Q),
                    (1000 + i, "aw%d" % (1000 + i), "抖音视频数据", row,
                     "https://v.douyin.com/%d" % row, "", "", author, "抖音素材 %d" % row,
                     "已完成", "2026-10-0%d" % (i + 1), "", "转写", "", "游戏攻略", "",
                     "sec%d" % row, "subscription", 0, 0, "", "[]", 0, ""))
    for i in range(3):
        vid(2000 + i, "微信文章", 10 + i, "微信号A")
    for i in range(2):
        vid(2100 + i, "暴雪官方", 20 + i, "魔兽世界运营团队", source="official", title="官方公告")
    # 取消订阅后留在库里的行（不该进名单，但条数要报出来）
    for i in range(2):
        vid(2200 + i, "微信文章", 30 + i, "退订的旧号")
    # 表接进列表了、但这一行没进索引（真实场景是那 151 条空壳/失败行）：
    # 它既不在列表里、也不能被"整张表接没接"漏掉，否则总数对不上
    con.execute("INSERT INTO videos (%s) VALUES (%s)" % (VID_COLS, Q),
                (1099, "aw1099", "抖音视频数据", 99, "https://v.douyin.com/99", "", "",
                 "抓取失败的旧号", "", "失败", "2026-10-01", "", "", "", "", "",
                 "", "subscription", 0, 0, "", "[]", 0, ""))
    # 论坛板块：整表还没接进素材库列表，author 是发帖人（一次 6 个人）
    for i in range(6):
        vid(3000 + i, "NGA无限板块", 40 + i, "发帖人%d" % i, source="forum", title="帖子 %d" % i)
    for a, p in [("订到的甲", "douyin"), ("订到的乙", "douyin"), ("微信号A", "wechat"),
                 ("好赛雷切片", "bilibili")]:     # 订阅了但库里没料 → 不进名单
        con.execute("INSERT INTO subscriptions (author, platform) VALUES (?,?)", (a, p))
    con.commit()
    con.close()


def make_index(path):
    """Excel 侧那批行走 video_index.json（索引是抖音视频数据的权威，DB 直写源不进索引）。"""
    vids = []
    for row, author in [(1, "订到的甲"), (2, "订到的甲"), (3, "订到的乙")]:
        vids.append({"id": str(1000 + row), "sheet": "抖音视频数据", "row": row,
                     "author": author, "title": "抖音素材 %d" % row, "status": "已完成",
                     "create_time": "2026-10-0%d" % row, "published_at": "2026-10-0%d" % row,
                     "description": "", "tags": "", "transcript_length": 4})
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump({"version": "e2e", "videos": vids}, f, ensure_ascii=False)


def boot(tmp):
    import app
    app.DB_PATH = Path(tmp) / "video2text.db"
    app.INDEX_PATH = Path(tmp) / "video_index.json"
    app.EXCEL_PATH = Path(tmp) / "不存在.xlsx"
    app._INDEX_CACHE["key"], app._INDEX_CACHE["data"] = None, {}
    return app, app.app.test_client()


def main():
    tmp = tempfile.mkdtemp(prefix="author-list-e2e-")
    db = Path(tmp) / "video2text.db"
    make_db(db)
    make_index(Path(tmp) / "video_index.json")

    print("\n== A 名单只列真来源 ==")
    app, c = boot(tmp)
    st = c.get("/api/stats").get_json()
    topics = st["topics"]
    subs = {r[0] for r in sqlite3.connect(str(db)).execute(
        "SELECT author FROM subscriptions WHERE author IN "
        "(SELECT DISTINCT author FROM videos)")}
    listed = set(topics)
    allowed = subs | {"魔兽世界运营团队"}       # 订阅到的 + 一手源那个账号
    check("名单里每一项都是订阅账号或一手源账号",
          listed <= allowed, "多出的：%s" % sorted(listed - allowed))
    check("论坛发帖人一个都没进名单",
          not [k for k in listed if k.startswith("发帖人")],
          "名单 %d 项：%s" % (len(listed), sorted(listed)))
    check("订阅了但库里没料的账号不占位", "好赛雷切片" not in listed)
    check("同一账号跨表只算一项且条数相加", topics.get("订到的甲") == 2,
          "订到的甲=%s" % topics.get("订到的甲"))

    print("\n== B 名单声称的条数 == 点下去筛出的条数 ==")
    bad = []
    for name, n in topics.items():
        got = c.get("/api/videos?page_size=0&topic=%s" % name).get_json()["total"]
        if got != n:
            bad.append((name, n, got))
    check("每一项都能筛出同样条数（不许有 0 条的来源项）", not bad, "对不上：%s" % bad)
    live = c.get("/api/videos?page_size=0").get_json()
    check("名单条数 + 未订阅条数 == 列表可见总数",
          sum(topics.values()) + st["off_subscription"]["videos"] == live["total"],
          "%d+%d vs %d" % (sum(topics.values()), st["off_subscription"]["videos"], live["total"]))

    print("\n== C 收起来的不藏数，两类分开报 ==")
    off = st["off_subscription"]
    check("未订阅那堆按真实人数与条数报", off == {"authors": 1, "videos": 2}, json.dumps(off, ensure_ascii=False))
    nil = st["not_in_library"]
    nga = [x for x in nil if x["sheet"] == "NGA无限板块"]
    check("没接进列表的源整表报一条（不是 6 个发帖人）",
          len(nga) == 1 and nga[0]["videos"] == 6 and nga[0]["signers"] == 6,
          json.dumps(nga, ensure_ascii=False))
    shell = [x for x in nil if x["sheet"] == "抖音视频数据"]
    check("表已接进列表、单行没进索引的也算进来（并带失败条数）",
          len(shell) == 1 and shell[0]["videos"] == 1 and shell[0]["failed"] == 1,
          json.dumps(shell, ensure_ascii=False))
    db_total = list(sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM videos"))[0][0]
    check("库里条数 == 列表可见 + 没接进来的（一份账，不许凭空少一截）",
          live["total"] + sum(x["videos"] for x in nil) == db_total,
          "%s + %s vs %s" % (live["total"], sum(x["videos"] for x in nil), db_total))
    check("这堆的行确实筛不到（所以才不能当来源项）",
          c.get("/api/videos?page_size=0&topic=发帖人0").get_json()["total"] == 0)

    print("\n== D 接进列表的那天前，别把板块按发帖人展开（任务书第 1 件的护栏） ==")
    saved = dict(app.DB_ONLY_SHEETS)
    app.DB_ONLY_SHEETS = dict(saved, **{"NGA无限板块": "forum"})
    st2 = c.get("/api/stats").get_json()
    check("登记进 DB_ONLY_SHEETS 后名单仍不冒出 6 个发帖人",
          not [k for k in st2["topics"] if k.startswith("发帖人")],
          "名单 %d 项" % len(st2["topics"]))
    check("这时它们落到未订阅那堆并照实报数",
          st2["off_subscription"]["videos"] == 8, json.dumps(st2["off_subscription"], ensure_ascii=False))
    check("NGA 已在列表可见，不再出现在「没接进列表」那堆",
          not [x for x in st2["not_in_library"] if x["sheet"] == "NGA无限板块"])
    app.DB_ONLY_SHEETS = saved

    print("\n== E 抽公共装配函数没改变 /api/videos 行为 ==")
    base = c.get("/api/videos?page_size=0").get_json()
    check("响应字段齐全", all(k in base for k in
                        ("videos", "total", "sources", "categories", "page", "page_size", "has_more")))
    check("来源计数与合并列表一致",
          sum(base["sources"].values()) == base["total"], json.dumps(base["sources"], ensure_ascii=False))
    check("按来源筛（douyin）", base["sources"]["douyin"] ==
          c.get("/api/videos?page_size=0&source=douyin").get_json()["total"])
    cats = {x["category"]: x["count"] for x in base["categories"]}
    check("按分类筛（游戏攻略）",
          c.get("/api/videos?page_size=0&category=游戏攻略").get_json()["total"] ==
          cats.get("游戏攻略", 0))
    one = c.get("/api/videos?page_size=1").get_json()
    check("分页照旧（page_size=1 只回 1 条但 total 不变）",
          len(one["videos"]) == 1 and one["total"] == base["total"] and one["has_more"])
    check("关键词整行能命中", c.get("/api/videos?page_size=0&q=%s 微信文章" %
          "微信号A").get_json()["total"] == 3)
    check("列表里每条都带 db_id 与 source（前端卡片要用）",
          all("db_id" in v and v.get("source") for v in base["videos"]))

    print("\n== F 「导入素材库作者为订阅」那颗列表：论坛署名不当账号 ==")
    au = c.get("/api/authors").get_json()["authors"]
    names = {a["author"] for a in au}
    check("发帖人不出现在可导入名单（点一下就会解析抖音主页）",
          not [n for n in names if n.startswith("发帖人")], "名单 %d 项" % len(names))
    check("订阅账号还在、且标了已订阅",
          [a for a in au if a["author"] == "订到的甲"] and
          next(a for a in au if a["author"] == "订到的甲")["subscribed"] is True)
    check("退订的旧号仍可见（那是真账号，人可以决定要不要重新订）",
          "退订的旧号" in names)

    print("\n== G 首页那行数字也走列表口径（号主 10-28 追加） ==")
    dash = c.get("/api/dashboard").get_json()
    todo = c.get("/api/todo").get_json()
    visible = c.get("/api/videos?page_size=0").get_json()["total"]
    check("首页「素材」== 素材库页头条数", dash["total"] == visible,
          "%s vs %s" % (dash["total"], visible))
    check("待办板的 total 与首页同源", todo["stats"]["total"] == dash["total"]
          and todo["stats"]["with_asr"] == dash["with_asr"],
          "todo=%s/%s" % (todo["stats"]["total"], todo["stats"]["with_asr"]))
    check("没接进列表的行另报一条、不混进 total",
          len(dash["off_library"]) == 2 and dash["total"] == visible,
          json.dumps(dash["off_library"], ensure_ascii=False))
    check("主要作者取同源名单（没有发帖人、没有「未知作者」占位）",
          not [a for a in dash["authors"] if a["name"].startswith("发帖人") or a["name"] == "未知作者"],
          json.dumps([a["name"] for a in dash["authors"]], ensure_ascii=False))
    check("最近入库 6 条全来自可见集",
          len(dash["recent_videos"]) == 6
          and not [v for v in dash["recent_videos"] if (v["author"] or "").startswith("发帖人")],
          json.dumps([(v["author"], v["updated_at"]) for v in dash["recent_videos"]][:3], ensure_ascii=False))
    need = [i for i in todo["items"] if i["key"] == "need_summary"]
    check("「有转写还没整理稿」不会超过可见集条数（入口点进去翻得到）",
          not need or need[0]["n"] <= visible, json.dumps(need, ensure_ascii=False))

    print("\n━━ 素材库名单契约：%d 项通过 / %d 项失败 ━━" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
        return 1
    print("ALL_OK 素材库「全部作者」名单契约全部守住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
