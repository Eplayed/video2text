# -*- coding: utf-8 -*-
"""plan_brief 的确定性回归：六档判定、当日已发后的顺延、新料窗口的跨日边界、文案同源。

为什么单独一个夹具（2026-10-21 复核 `PRD-video2text-排产表Qoder修复-2026-10-21.md` R2/R4）：
这份判据有三处读者（CLI 日报、`/api/plan/brief`、排产页上色），以前**只有一处 UI 走查**在守，
而那次走查断的是"卡上有 vb- 类"和"标签有背景色"——色条选择器写错（`.item` vs `.card`）
照样全绿，顺延只改说明不改 verdict 也没人管。所以这一份把判据本身钉死：
给定一个库和一份名单，每格该是什么档、该念哪句话，必须逐条对上。

三条纪律：
· **不碰生产**：库是 `temp/` 下现造的，名单是 `data=` 直接注入的（不读 `schedule.html`），
  日期靠 `today=` 注入，跑完删；
· **不花钱**：纯函数 + SQLite，不调模型、不起服务；
· 断言查**行为**不查字面量抄本：档位名字从 `plan_brief.VERDICTS` 读，
  改了名这里跟着走（把中文写死在断言里，改文案那天就是一堆假红）。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_plan_brief.py
"""
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "temp" / "_plan_brief_test.db"

TODAY = dt.date(2026, 10, 21)
fails = []


def ck(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" — " + detail) if detail else ""))
    if not cond:
        fails.append(name)


def make_db(rows, pubs):
    if DB.exists():
        os.remove(str(DB))
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB))
    con.execute("CREATE TABLE videos (source_sheet TEXT, source_row INTEGER, title TEXT,"
                " author TEXT, published_at TEXT, transcript TEXT)")
    con.executemany("INSERT INTO videos VALUES (?,?,?,?,?,?)", rows)
    con.execute("CREATE TABLE publish_stats (channel TEXT, title TEXT, pub_date TEXT, slot_key TEXT)")
    con.executemany("INSERT INTO publish_stats VALUES (?,?,?,?)", pubs)
    con.commit()
    con.close()


def item(iid, date, title, search, kind="攻略", note=""):
    return {"id": iid, "date": date, "title": title, "kind": kind, "note": note,
            "decision": "开服前定一次", "hook": "怎么打", "since": "2026-01-01",
            "search": search, "pinned": [], "official": []}


DATA = {"meta": {"must": "五人本|天赋|老题材|未来的题|已发|像发", "block": "抽奖",
        "gate": "文字/事实/关键帧/使用依据"},
        "items": [
            item("a-red", TODAY.isoformat(), "五人本路线", ["五人本"]),
            item("b-red2", TODAY.isoformat(), "天赋模拟器", ["天赋"]),
            item("c-orange", TODAY.isoformat(), "老题材没新料", ["老题材"]),
            item("d-grey", (TODAY + dt.timedelta(days=3)).isoformat(), "未来的题", ["未来"]),
            item("e-green", TODAY.isoformat(), "已经发过的", ["已发"]),
            item("f-yellow", TODAY.isoformat(), "标题像发过", ["像发"]),
        ]}


def main():
    from src import plan_brief as pb

    # 素材：a/b 两格有"今天和前两天"的新料，c 只有三天前的，d 未来的也有料（但不该判红）
    rows = [
        ("抖音视频数据", 1, "五人本路线怎么排", "甲", TODAY.isoformat() + " 09:00:00", "x" * 400),
        ("抖音视频数据", 2, "天赋模拟器说明", "乙", (TODAY - dt.timedelta(days=2)).isoformat()
         + " 23:59:00", "x" * 400),
        ("抖音视频数据", 3, "老题材的一条", "丙", (TODAY - dt.timedelta(days=3)).isoformat()
         + " 23:00:00", "x" * 400),
        ("抖音视频数据", 4, "未来的题也有一条新料", "丁", TODAY.isoformat() + " 08:00:00", "x" * 400),
        ("抖音视频数据", 5, "已发那题有新料", "戊", TODAY.isoformat() + " 07:00:00", "x" * 400),
        ("抖音视频数据", 6, "像发那题有新料", "己", TODAY.isoformat() + " 06:00:00", "x" * 400),
        ("抖音视频数据", 7, "五人本抽奖广告", "庚", TODAY.isoformat() + " 10:00:00", "x" * 400),
        ("抖音视频数据", 8, "五人本但正文太短", "辛", TODAY.isoformat() + " 11:00:00", "x" * 50),
    ]

    print("== 1. 没发过：五档各归各位（红/橙/灰/绿/黄 不许互相冒充） ==")
    make_db(rows, [("toutiao", "已经发过的《…》", "2026-10-20", "e-green"),
                   ("toutiao", "标题像发过这半句", "2026-10-19", "")])
    b = pb.build(str(DB), TODAY, data=DATA)
    v = {k: s["verdict"] for k, s in b["slots"].items()}
    ck("到期 + 有窗口内新料 = 红", v["a-red"] == "red", str(v))
    ck("到期 + 只剩三天前的旧料 = 橙（缺新料）", v["c-orange"] == "orange", v["c-orange"])
    ck("计划日在未来 = 灰（哪怕它也有新料）", v["d-grey"] == "grey", v["d-grey"])
    ck("台账带槽号 = 绿", v["e-green"] == "green", v["e-green"])
    ck("只有标题对上、没槽号 = 黄（要人确认，不当事实）", v["f-yellow"] == "yellow", v["f-yellow"])
    ck("黑名单那条没被算进可用素材",
       all("抽奖" not in x["title"] for x in b["slots"]["a-red"]["fresh"]["list"]),
       str([x["title"] for x in b["slots"]["a-red"]["fresh"]["list"]]))
    ck("正文太短的那条只算线索、不占位",
       all("太短" not in x["title"] for x in b["slots"]["a-red"]["fresh"]["list"])
       and b["slots"]["a-red"]["fresh"]["thin"] >= 1,
       "thin=%d" % b["slots"]["a-red"]["fresh"]["thin"])

    print("== 2. R1：档位名字不许读成「已经能发」 ==")
    ck("六档键齐了（多出来的是 deferred）",
       set(pb.VERDICTS) == {"red", "deferred", "orange", "yellow", "green", "grey"},
       str(sorted(pb.VERDICTS)))
    for k, label in pb.VERDICTS.items():
        ck("「%s」这档的名字不含「今天就发/一定要发/可发布」" % k,
           ("今天就发" not in label) and ("一定要发" not in label) and ("可发布" not in label), label)
    ck("每档都有一句「为什么是这个颜色」，且都来自服务端（前端不抄第二份）",
       set(pb.VERDICT_WHY) == set(pb.VERDICTS) and all(len(str(x)) > 12 for x in
                                                       pb.VERDICT_WHY.values()))
    ck("接口把 label / why / verdicts / verdict_why / fresh_label 一起下发",
       all(k in b for k in ("verdicts", "verdict_why", "fresh_label"))
       and all(k in b["slots"]["a-red"] for k in ("label", "why")),
       ",".join(sorted(b["slots"]["a-red"].keys()))[:70])
    ck("label 与 VERDICTS 同源、why 与 VERDICT_WHY 同源",
       b["slots"]["a-red"]["label"] == pb.VERDICTS["red"]
       and b["slots"]["a-red"]["why"] == pb.VERDICT_WHY["red"])
    txt = pb.render(b)
    ck("日报那份也不含「今天就发/一定要发」",
       "今天就发" not in txt and "一定要发" not in txt)
    ck("日报明写「颜色只说排期，不等于能发」",
       "不等于这篇已经能发" in txt or "不等于「这篇能发」" in txt,
       [ln for ln in txt.split("\n") if "能发" in ln][:1])

    print("== 3. R4：新料窗口说的是日历日，不是整 48 小时 ==")
    ck("窗口口径由服务端下发一份中文（页面与日报共用）",
       b["fresh_label"] == pb.FRESH_LABEL and pb.FRESH_LABEL in txt, b["fresh_label"])
    ck("谁都不把窗口说成「48 小时内 / 48h 内」（解释那句「不是整 48 小时」除外）",
       "48h" not in txt and "48 小时内" not in txt and "48 小时的新料" not in txt,
       [ln for ln in txt.split("\n") if "48" in ln][:1])
    st = {x["title"]: x["status"] for s in b["slots"].values() for x in s["fresh"]["list"]}
    ck("今天 09:00 算新料", st.get("五人本路线怎么排") == "fresh", str(st)[:60])
    ck("前两天 23:59 也算新料（按日历日，不按 48 整点）",
       st.get("天赋模拟器说明") == "fresh", str(st)[:60])
    ck("三天前的那条不算新料（哪怕只差一小时就满 72 小时）",
       st.get("老题材的一条") in (None, "window"), str(st)[:60])
    # 跨日边界：把"今天"往前挪一天，原来"今天"的那条就掉出窗口
    b2 = pb.build(str(DB), TODAY + dt.timedelta(days=3), data=DATA)
    ck("过三天再算，这批素材全部掉出窗口 → 到期格改判橙（缺新料）而不是红",
       b2["slots"]["a-red"]["verdict"] == "orange" and b2["slots"]["a-red"]["fresh"]["fresh"] == 0,
       "%s / fresh=%d" % (b2["slots"]["a-red"]["verdict"], b2["slots"]["a-red"]["fresh"]["fresh"]))

    print("== 4. R2：今天已发一篇 → 所有原本会红的格子明确顺延（不是继续红、也不是缺料） ==")
    make_db(rows, [("toutiao", "已经发过的《…》", "2026-10-20", "e-green"),
                   ("toutiao", "标题像发过这半句", "2026-10-19", ""),
                   ("toutiao", "今天先动的那篇", TODAY.isoformat(), "a-red")])
    one = pb.build(str(DB), TODAY, data=DATA)
    s = one["slots"]["a-red"]
    ck("已登记那一格自己是绿（发了就是发了）", s["verdict"] == "green" or s["published"]["state"] == "confirmed",
       "%s / %s" % (s["verdict"], s["published"]["state"]))
    ck("另一个原本会红的格子改判 deferred，label/why 一起改",
       one["slots"]["b-red2"]["verdict"] == "deferred"
       and one["slots"]["b-red2"]["label"] == pb.VERDICTS["deferred"]
       and one["slots"]["b-red2"]["why"] == pb.VERDICT_WHY["deferred"],
       one["slots"]["b-red2"]["label"])
    ck("顺延里写明是「今天名额用完了」，不是没料",
       "名额" in one["slots"]["b-red2"]["demoted"] or "一天一篇" in one["slots"]["b-red2"]["demoted"],
       one["slots"]["b-red2"]["demoted"][:40])
    ck("顺延那格确实有新料（跟橙色「缺新料」划清）",
       one["slots"]["b-red2"]["fresh"]["fresh"] > 0,
       "fresh=%d" % one["slots"]["b-red2"]["fresh"]["fresh"])
    ck("缺新料那格仍是橙，没被顺延档吃掉", one["slots"]["c-orange"]["verdict"] == "orange")
    ck("未来的仍灰、疑似已发的仍黄（改判只动原本会红的）",
       one["slots"]["d-grey"]["verdict"] == "grey" and one["slots"]["f-yellow"]["verdict"] == "yellow")
    ck("日报分组里 deferred 单独一组，且这格不出现在红组里",
       "【顺延" in pb.render(one)
       and one["slots"]["b-red2"]["id"] not in [x["id"] for x in one["ranked"]
                                                if x["verdict"] == "red"],
       str([x["verdict"] for x in one["ranked"]]))

    print("== 5. R2 反例：只剩一个原本会红的格子时，也必须改判（原来这里漏了） ==")
    make_db(rows[:2] + rows[4:6],
            [("toutiao", "已经发过的《…》", "2026-10-20", "e-green"),
             ("toutiao", "今天已经动过一篇", TODAY.isoformat(), "x-other")])
    only = {"meta": DATA["meta"], "items": [item("a-red", TODAY.isoformat(), "五人本路线", ["五人本"]),
                                            item("c-orange", TODAY.isoformat(), "老题材没新料", ["老题材"]),
                                            item("e-green", TODAY.isoformat(), "已经发过的", ["已发"])]}
    b3 = pb.build(str(DB), TODAY, data=only)
    ck("唯一那个红槽也改判 deferred（不是「第一个保留红」）",
       b3["slots"]["a-red"]["verdict"] == "deferred", b3["slots"]["a-red"]["verdict"])
    ck("页面上没有任何一格还挂着 red",
       not [k for k, x in b3["slots"].items() if x["verdict"] == "red"],
       str({k: x["verdict"] for k, x in b3["slots"].items()}))
    ck("ranked 的排序把 deferred 排在红之后、橙之前（分组与颜色同一份序）",
       [x["verdict"] for x in b3["ranked"]] == ["deferred", "orange", "green"],
       str([x["verdict"] for x in b3["ranked"]]))

    print("== 6. R5：名单文件不在时报错要说人话（干净检出里就是这种情况） ==")
    try:
        pb.load_data(str(ROOT / "temp" / "_definitely_not_here.html"))
        ck("缺文件要抛错", False, "没抛错——页面缺席被当成「今天没题可发」了")
    except FileNotFoundError as e:
        ck("缺文件抛 FileNotFoundError 且说清是名单没提交/没部署",
           "不在" in str(e) and ("没提交" in str(e) or "没部署" in str(e))
           and "别拿空名单" in str(e), str(e)[:90])
    except Exception as e2:
        ck("异常类型要可预期（FileNotFoundError）", False, "%s: %s" % (type(e2).__name__, e2))

    if DB.exists():
        os.remove(str(DB))
    if fails:
        print("\nPLAN_BRIEF_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("PLAN_BRIEF_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
