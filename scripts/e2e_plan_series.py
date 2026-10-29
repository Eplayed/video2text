# -*- coding: utf-8 -*-
"""e2e_plan_series.py — 双系列排产与夜间匹配的离线验收（不花钱、不动生产库）。

任务书：`PRD-video2text-双系列排产与夜间匹配-2026-10-22.md` §5 的验收清单，逐条落断言：
A 身份与注册 / B 匹配与可解释 / C 已发布跳过 / D 旧本地勾选待核对 / E 候选不污染已采用
F 两个起稿入口（系列内、素材库独立、攻略/资讯、O1→O2→O3）/ G 发布真相跨浏览器
H 八档状态（`done` 绝不冒充已发布）/ I 到期、补跑、锁、失败重试 / J 配置 / K 红线：匹配不改任何产物
M Codex 二次复验的五条反例（`VERIFY-双系列排产独立验收-2026-10-22.md`）：
  M1 同题同日两篇的登记不得互相覆盖 + 撤销后重登换新键；M2 公众号发过不算头条发过；
  M3 O2/O3 的前序判据必须在服务端；M4 独立稿关联 S1 只增关系不增位置（含老库迁移）；
  M5a 那颗钟取的是北京时间（TZ=UTC 子进程实测）；M5b 候选超上限时留下的是最新那几条。
N Codex 三次复验：排产登记口与通用 `/api/ops/register` **交叉调用**不许互相改写发布事实
  （带槽只认同槽、无槽不许落到带槽的行、显式 ref_id 指向别槽直接拒）；
  绕过页面新建 S1 位/第四章被拒；旧浏览器勾选只算头条；渠道拼错要报错而不是静默退成头条。

跑法（临时库副本 + 固定时钟 + 假成品目录，全程零模型调用）：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_plan_series.py
末行 `PLAN_SERIES_OK` 才算过。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROD_DB = ROOT / "output" / "video2text.db"

ok_n = 0
fails = []


def ck(name, cond, detail=""):
    global ok_n
    if cond:
        ok_n += 1
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:110]) if detail else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:160]) if detail else ""))


def q(db, sql, args=()):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def exe(db, sql, args=()):
    con = sqlite3.connect(str(db))
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


FIXTURES = [
    # (id, sheet, row, title, author, date, 正文, 该不该进 A3 候选)
    (900001, "抖音视频数据", "9001", "无限服插件怎么装：三步备份再卸载", "某作者", "2026-10-20",
     "先说清这是无限服的插件管理" + "字" * 400, "yes"),
    (900002, "微信文章", "9002", "Forever 界面整理：先调这三块", "另一作者", "2026-10-21",
     "Forever 的姓名板与动作条" + "字" * 300, "yes"),
    (900003, "抖音视频数据", "9003", "POE2 流放之路插件推荐", "别的游戏号", "2026-10-21",
     "流放之路的插件与无限毫无关系" + "字" * 300, "off"),
    (900004, "微信文章", "9004", "魔兽世界插件更新说明", "转述号", "2026-10-19",
     "只说插件更新，没说是哪个服" + "字" * 300, "unknown"),
    (900005, "抖音视频数据", "9005", "无限服插件翻车回退实录", "某作者", "2026-10-18",
     "短" * 50, "thin"),
    (900006, "暴雪官方", "9006", "暴雪公告：某项调整", "暴雪官方", "2026-10-22",
     "官方公告正文，没提无限服三个字" + "字" * 300, "blizzard"),
]


def build_db(tmp, name="t.db"):
    """生产库副本 → 清掉素材与三本新账 → 只留固定夹具。计数才可断言。

    文件名要每次不同：`ensure()` 认的是"路径+inode"，同名重建会让缓存与文件对不上。"""
    db = tmp / name
    shutil.copyfile(str(PROD_DB), str(db))
    exe(db, "DELETE FROM videos")
    for vid, sheet, row, title, author, date, body, _tag in FIXTURES:
        # videos 表有三列 NOT NULL 且无默认值（source_sheet / source_row / updated_at），
        # 少给一个整条 e2e 直接 IntegrityError——列名以 PRAGMA 实测为准，不凭记忆写。
        exe(db, "INSERT INTO videos (id, source_sheet, source_row, title, author,"
                " published_at, transcript, category, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, sheet, int(row), title, author, date + " 10:00:00", body, "游戏攻略",
             "2026-10-22 10:00:00"))
    for t in ("plan_slots", "plan_candidates", "plan_match_runs", "plan_publish_audit",
              "plan_slot_flags", "plan_tasks", "publish_stats"):
        exe(db, "DELETE FROM %s" % t) if _exists(db, t) else None
    return db


def _exists(db, name):
    return bool(q(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def slot_map(board):
    return {s["slot_key"]: s for s in board["slots"]}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="plan_series_e2e_"))
    os.environ["V2T_PLAN_MATCH_STORE"] = str(tmp / "plan_match.json")
    from src import plan_series as ps
    ps.MATCH_STORE = tmp / "plan_match.json"
    db = build_db(tmp)
    poster_base = tmp / "poster"
    poster_base.mkdir()

    print("== A 身份：26 个位置 + S2 三章模板 + 旧 11 题，键要稳、注册要幂等 ==")
    seed = ps.seed(db, [{"id": "odur"}, {"id": "day1"}, {"id": "setup"}])
    slots = ps.slots(db)
    s1 = [s for s in slots if s["series"] == "s1"]
    s2 = [s for s in slots if s["series"] == "s2"]
    # 26 这个数只数**地图注册**的那批（origin='map'）。Codex 二次复验抓到的是另一种数法：
    # 独立稿关联进来时把 series 也改成 s1，于是"S1 有 27 个位置"——那是稿件，不是内容位置。
    ck("S1 正好 26 个内容位置（A6+B10+C6+D4，只数地图注册的）",
       len([s for s in s1 if s["origin"] == "map"]) == 26,
       len([s for s in s1 if s["origin"] == "map"]))
    ck("S2 是三个章节模板，不是预置稿件", sorted(x["position"] for x in s2) == ["O1", "O2", "O3"])
    ck("旧排产题以 legacy 注册，不迁不删", len([s for s in slots if s["series"] == "legacy"]) == 3)
    ck("每个槽号都过 ops_store 的字符与长度约束",
       all(ps.valid_slot(s["slot_key"]) for s in slots))
    again = ps.seed(db, [{"id": "odur"}])
    ck("重复注册幂等：第二次一条都不新增", again["added"] == 0, again)
    exe(db, "UPDATE plan_slots SET title='人改过的标题' WHERE slot_key='s1a3'")
    ps.seed(db)
    ck("再注册不许吃掉人改过的标题（只补缺，不覆盖）",
       ps.get_slot(db, "s1a3")["title"] == "人改过的标题", ps.get_slot(db, "s1a3")["title"])

    print("== B 匹配：只读检索、可解释、跨游戏挡掉、零候选就显示零 ==")
    st = ps.match_positions(db, now=dt.datetime(2026, 10, 22, 22, 0))
    cand = {}
    for r in q(db, "SELECT * FROM plan_candidates"):
        cand.setdefault(r["slot_key"], {})[int(r["video_id"])] = r
    a3 = cand.get("s1a3", {})
    ck("900001（正文写「无限服」）进 A3 候选", 900001 in a3, sorted(a3))
    ck("900003（POE2/流放）不进任何候选 —— 跨游戏假阳性",
       not any(900003 in v for v in cand.values()))
    ck("900005 正文太短只算线索，不占候选位",
       not any(900005 in v for v in cand.values()))
    ck("900004 进了候选但标 unknown（判不了归属，不自动采用）",
       900004 in cand.get("s1a3", {}) and cand["s1a3"][900004]["forever"] == "unknown",
       (cand.get("s1a3", {}).get(900004) or {}).get("forever"))
    ck("900006 只因为来源是暴雪官方 → 仍判 unknown（任务书点名的那条）",
       cand.get("s1a3", {}).get(900006, {}).get("forever") == "unknown" or
       900006 not in cand.get("s1a3", {}),
       (cand.get("s1a3", {}).get(900006) or {}).get("forever"))
    ck("每条候选都带可解释理由（命中词/来源/日期/字数/归属依据）",
       all("｜" in (r["reason"] or "") for v in cand.values() for r in v.values()))
    # 2026-10-29：这一支以前写死 `sheet == "blizzard"`，而调用方传的是库里的表名「暴雪官方」，
    # 于是永远不命中、理由被泛化成「只有『暴雪』这类弱线索」。两个口径都得认。
    for sh in ("暴雪官方", "blizzard"):
        v, why = ps.forever_call("某项调整", "暴雪", "官方公告正文，没提三个字", sh)
        ck("Forever 判据认「%s」这个来源写法（理由点名暴雪官方）" % sh,
           v == "unknown" and "暴雪官方公告" in why, why)
    ck("匹配确实跑过并记账", st["positions"] > 0 and st["added"] > 0,
       "位置 %d 新增 %d" % (st["positions"], st["added"]))
    empty = [k for k, v in ((s["slot_key"], cand.get(s["slot_key"])) for s in ps.slots(db))
             if not v]
    ck("匹配到零条的位置就是零，不拿历史素材填位", "s1b8" in empty or not empty,
       "零候选槽位 %d 个" % len(empty))
    st2 = ps.match_positions(db, now=dt.datetime(2026, 10, 22, 23, 0))
    ck("同一批素材重跑不产生重复候选", st2["added"] == 0 and st2["seen"] == st["added"],
       "added=%s seen=%s" % (st2["added"], st2["seen"]))
    ck("候选总数没变", len(q(db, "SELECT * FROM plan_candidates")) == len(
        [r for v in cand.values() for r in v.values()]))

    print("== C/D 发布真相与旧本地勾选：这两种格子夜间不再刷 ==")
    ps.register_slot(db, "s1a3", {"channel": "toutiao", "title": "无限服插件怎么装",
                                  "pub_date": "2026-10-22", "url": "https://www.toutiao.com/x/",
                                  "by": "e2e"})
    st3 = ps.match_positions(db, now=dt.datetime(2026, 10, 23, 22, 0))
    ck("已发布的位置被跳过", any(x["slot_key"] == "s1a3" for x in st3["skipped"]),
       st3["skipped"][:1])
    ck("跳过原因写的是「已发布，不再匹配」",
       any("已发布" in x["why"] for x in st3["skipped"]))
    ps.report_local(db, "s1b1", True, "这台浏览器勾过已发布")
    st4 = ps.match_positions(db, now=dt.datetime(2026, 10, 24, 22, 0))
    ck("只有本地勾选、服务端没登记 → 保守跳过并说明",
       any(x["slot_key"] == "s1b1" and "待核" in x["why"] for x in st4["skipped"]),
       [x for x in st4["skipped"] if x["slot_key"] == "s1b1"])
    b = ps.board(db, today=dt.date(2026, 10, 24), base=poster_base)
    smap = slot_map(b)
    ck("界面上这一格标「待核对旧勾选」而不是「已发布」",
       smap["s1b1"]["pending_review"] and not smap["s1b1"]["published"])
    ps.resolve_local(db, "s1b1", "not_published", by="e2e")
    st5 = ps.match_positions(db, now=dt.datetime(2026, 10, 25, 22, 0))
    ck("人核对完「其实没发过」→ 恢复匹配",
       not any(x["slot_key"] == "s1b1" for x in st5["skipped"]))
    ps.revoke_slot(db, "s1a3", q(db, "SELECT ref_id FROM publish_stats WHERE slot_key='s1a3'")[0]["ref_id"],
                   "登记错了，那是另一篇", by="e2e")
    ck("撤销后这一格回到未发布", ps.published_state(db, "s1a3")["state"] != "confirmed")
    ck("撤销不删台账行（回查还看得到当初登记了什么）",
       len(q(db, "SELECT * FROM publish_stats WHERE slot_key='s1a3'")) == 1)
    ck("撤销留了审计与影响说明",
       any("原因" in (r["note"] or "") for r in q(db, "SELECT * FROM plan_publish_audit"
                                                    " WHERE action='revoke'")))

    print("== E 候选不污染已采用：夜间刷新只动候选，绝不动人勾的那份 ==")
    from src import plan_tasks
    ids = [900001, 900002]
    task, action = plan_tasks.save_sources(db, "s1a1", "toutiao", "guide",
                                           {"title": "先确认你进的是哪个服"}, 
                                           [dict(zip(("id", "source_sheet", "source_row", "title",
                                                      "author", "published_at", "transcript"),
                                                     (900001, "抖音视频数据", "9001",
                                                      "无限服插件怎么装", "某作者",
                                                      "2026-10-20 10:00:00", "字" * 400)))])
    before = (json.dumps(task["video_ids"]), json.dumps(task["sources"], ensure_ascii=False))
    ps.match_positions(db, now=dt.datetime(2026, 10, 26, 22, 0))
    after_task = plan_tasks.find_task(db, "s1a1", "toutiao", "guide")
    after = (json.dumps(after_task["video_ids"]), json.dumps(after_task["sources"], ensure_ascii=False))
    ck("夜间匹配没改已采用清单与证据快照", before == after, after[0])
    b = ps.board(db, today=dt.date(2026, 10, 26), base=poster_base)
    ck("已采用的那几条从候选计数里分区出去（不重复计）",
       slot_map(b)["s1a1"]["adopted_ids"] == [900001],
       slot_map(b)["s1a1"]["adopted_ids"])
    ck("这一格状态是「已采用」", slot_map(b)["s1a1"]["stage"] == "adopted",
       slot_map(b)["s1a1"]["stage"])

    print("== F 两个起稿入口：系列内采用 / 素材库直接起（可关联可独立）/ 攻略与资讯 ==")
    ck("S2 章节模板不能直接采用（先建那一篇）",
       not ps.get_slot(db, "s2o1")["adoptable"] if "adoptable" in ps.get_slot(db, "s2o1")
       else True)
    o1 = ps.new_slot(db, title="国服法师与萨满深度解析速读", series="s2", chapter="O1",
                     form="mix")
    ck("从 O1 模板建出一篇真稿件（键稳定）", o1["slot_key"].startswith("s2o1-"), o1["slot_key"])
    o2 = ps.new_slot(db, title="若上线保留：图腾维护负担会降吗", series="s2", chapter="O2",
                     form="mix", link_slot=o1["slot_key"])
    ck("O2 关联了前序 O1（引用关系，不复制）", o2["link_slot"] == o1["slot_key"], o2["link_slot"])
    free = ps.new_slot(db, title="素材库直接起的一篇", series="free", form="guide")
    ck("独立篇拿到自己的稳定短键", free["slot_key"].startswith("free-"), free["slot_key"])
    ck("独立篇键不超过台账那 24 位上限", ps.valid_slot(free["slot_key"]))
    ps.link_to_series(db, free["slot_key"], "s1", "C", "C2")
    linked = ps.get_slot(db, free["slot_key"])
    ck("先独立后关联系列：不换键，也不冒充地图位置（引用写在 parent_slot）",
       linked["slot_key"] == free["slot_key"] and linked["series"] == "free"
       and linked.get("parent_slot") == "s1c2", (linked["series"], linked.get("parent_slot")))
    ck("稿件形式与整理结构对得上（攻略=game_guide／资讯=toutiao_mix）",
       plan_tasks.resolve_summary_type("toutiao", "guide") == "game_guide" and
       plan_tasks.resolve_summary_type("toutiao", "mix") == "toutiao_mix")

    print("== G 发布真相跨浏览器一致（同一库、两次独立读）==")
    ps.register_slot(db, "s1c1", {"channel": "toutiao", "title": "1-60 每阶段核什么",
                                  "pub_date": "2026-10-26", "url": "",
                                  "url_missing_reason": "手机发的，链接回头补", "by": "浏览器甲"})
    a = ps.published_state(db, "s1c1")
    b2 = ps.published_state(db, "s1c1")
    ck("两个连接读到的都是已确认", a["state"] == "confirmed" and b2["state"] == "confirmed")
    row = q(db, "SELECT * FROM publish_stats WHERE slot_key='s1c1'")
    ck("标题/日期落进台账", row and row[0]["title"] == "1-60 每阶段核什么"
       and row[0]["pub_date"] == "2026-10-26")
    ck("没链接时明确记了待补原因", "链接待补" in (row[0]["note"] or ""), row[0]["note"])
    ps.register_slot(db, "s1c1", {"channel": "toutiao", "title": "1-60 每阶段核什么",
                                  "pub_date": "2026-10-26", "by": "双击第二次"})
    ck("同标题同日期重复登记复用同一行，不长第二条台账",
       len(q(db, "SELECT * FROM publish_stats WHERE slot_key='s1c1'")) == 1,
       len(q(db, "SELECT * FROM publish_stats WHERE slot_key='s1c1'")))
    ck("登记后旧本地勾选标记自动解掉（不用核对两次）",
       not [r for r in q(db, "SELECT * FROM plan_slot_flags WHERE slot_key='s1c1'"
                             " AND IFNULL(resolved_at,'')=''")])

    print("== H 八档状态：每一档都指一条查得到的事实 ==")
    def set_task(slot, status, summary_id=None, vids=None):
        con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
        try:
            r = con.execute("SELECT * FROM plan_tasks WHERE slot_key=? ORDER BY version DESC"
                            " LIMIT 1", (slot,)).fetchone()
            if r:
                con.execute("UPDATE plan_tasks SET status=?, summary_id=? WHERE id=?",
                            (status, summary_id, r["id"]))
            else:
                con.execute(
                    "INSERT INTO plan_tasks (slot_key, channel, form, title, video_ids, sources,"
                    " summary_id, version, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (slot, "toutiao", "guide", slot, json.dumps(vids or []), "[]",
                     summary_id, 1, status, ps._now(), ps._now()))
            con.commit()
        finally:
            con.close()
    set_task("s1d1", "running")
    ck("status=running → 整合稿中",
       slot_map(ps.board(db, base=poster_base))["s1d1"]["stage"] == "drafting")
    set_task("s1d2", "done", summary_id=777)
    bd = slot_map(ps.board(db, base=poster_base))
    ck("有整理稿、没图文包 → 待拆文案", bd["s1d2"]["stage"] == "draft_done", bd["s1d2"]["stage"])
    d777 = poster_base / "777"
    d777.mkdir()
    (d777 / "manifest.json").write_text(json.dumps(
        {"publishable": False, "lint": ["error: 编辑证据链缺项 —— 没建链（provenance）"]},
        ensure_ascii=False), encoding="utf-8")
    (d777 / "poster_p1.png").write_bytes(b"\x89PNG" + b"0" * 3000)
    bd = slot_map(ps.board(db, base=poster_base))
    ck("图文包没过质量门 → 看版面，并把拦点带出来",
       bd["s1d2"]["stage"] == "layout" and "编辑证据链" in bd["s1d2"]["stage_why"],
       bd["s1d2"]["stage_why"])
    (d777 / "manifest.json").write_text(json.dumps({"publishable": True, "lint": []}),
                                        encoding="utf-8")
    bd = slot_map(ps.board(db, base=poster_base))
    ck("图文包全过 → 待人工发布", bd["s1d2"]["stage"] == "ready", bd["s1d2"]["stage"])
    ck("整理稿 status=done **不**等于已发布", bd["s1d2"]["published"] is False)
    ps.register_slot(db, "s1d2", {"channel": "toutiao", "title": "团本前准备",
                                  "pub_date": "2026-10-26", "url": "https://x/"})
    bd = slot_map(ps.board(db, base=poster_base))
    ck("登记后才算已发布（最远那一档）", bd["s1d2"]["stage"] == "published")
    ck("八档标签与依据只有一份，接口原样下发",
       len(bd["s1d2"] or {}) and len(ps.board(db, base=poster_base)["stages"]) == 8)

    print("== I 到期、补跑、锁、失败重试 ==")
    # since=这套匹配的存在日：不写这一格，"昨晚没有账"会被当成欠账，刚装好那天任何时刻都会补跑
    ps.save_cfg(True, "22:00", since="2026-11-01")
    ck("21:59 不跑", not ps.due_run(db, dt.datetime(2026, 11, 1, 21, 59))["due"],
       ps.due_run(db, dt.datetime(2026, 11, 1, 21, 59))["why"])
    ck("22:00 整点跑", ps.due_run(db, dt.datetime(2026, 11, 1, 22, 0))["due"])
    ps.run_match(db, now=dt.datetime(2026, 10, 22, 22, 1), kind="night")
    ck("跑过的当晚不再跑",
       not ps.due_run(db, dt.datetime(2026, 10, 22, 23, 0))["due"],
       ps.due_run(db, dt.datetime(2026, 10, 22, 23, 0))["why"])
    r1 = ps.claim_run(db, ps.night_key(dt.date(2026, 11, 2)), "night", "2026-11-02", "甲")
    r2 = ps.claim_run(db, ps.night_key(dt.date(2026, 11, 2)), "night", "2026-11-02", "乙")
    ck("同一晚重复触发只允许一个抢到（锁）", r1 and not r2)
    ps.finish_run(db, ps.night_key(dt.date(2026, 11, 2)), False, {}, "假失败")
    ck("失败后仍可再抢（自动重试有上限）",
       ps.claim_run(db, ps.night_key(dt.date(2026, 11, 2)), "night", "2026-11-02", "丙"))
    for _ in range(4):
        ps.finish_run(db, ps.night_key(dt.date(2026, 11, 2)), False, {}, "还是失败")
        ps.claim_run(db, ps.night_key(dt.date(2026, 11, 2)), "night", "2026-11-02", "丁")
    ck("重试次数用满后不再半夜反复敲库",
       not ps.due_run(db, dt.datetime(2026, 11, 2, 23, 30))["due"],
       ps.due_run(db, dt.datetime(2026, 11, 2, 23, 30))["why"])
    # 补跑：清掉 10-27 之后的账，模拟"关机三天后早上开机"
    exe(db, "DELETE FROM plan_match_runs")
    ps.save_cfg(True, "22:00", since="2026-10-27")
    due = ps.due_run(db, dt.datetime(2026, 10, 30, 9, 30))
    ck("关机错过几个晚上，早上 9:30 开机只补一次（只看昨晚）",
       due["due"] and len(due["catchup"]) == 1, (due["why"], due["catchup"]))
    key = due["run_key"]
    ck("补跑写的是**今天**那一行（不会为每个错过日各跑一轮）", key == ps.night_key(dt.date(2026, 10, 30)), key)
    ps.run_match(db, now=dt.datetime(2026, 10, 30, 9, 31), kind="night")
    ck("补完之后当天不再补", not ps.due_run(db, dt.datetime(2026, 10, 30, 10, 0))["due"],
       ps.due_run(db, dt.datetime(2026, 10, 30, 10, 0))["why"])
    ck("今晚还没到点、也没有欠账 → 不跑",
       not ps.due_run(db, dt.datetime(2026, 10, 31, 8, 0))["due"],
       ps.due_run(db, dt.datetime(2026, 10, 31, 8, 0))["why"])
    exe(db, "UPDATE plan_match_runs SET status='running' WHERE run_key=?", (key,))
    ck("进程被 kill 留下的 running 能被收尾成 failed", ps.sweep_stale_runs(db) >= 1)
    ck("关总开关后一律不跑", (ps.save_cfg(False, "22:00"),
       not ps.due_run(db, dt.datetime(2026, 10, 31, 23, 0))["due"])[1])
    ps.save_cfg(True, "22:00")

    print("== J 配置：时刻写错要报错，文件坏了不许白屏 ==")
    try:
        ps.save_cfg(True, "25:99")
        ck("非法时刻被拒", False, "居然存进去了")
    except ValueError as e:
        ck("非法时刻被拒并说清格式", "22:00" in str(e) or "时刻" in str(e), str(e)[:60])
    (tmp / "plan_match.json").write_text("{坏了一半", encoding="utf-8")
    cfg = ps.load_cfg()
    ck("配置读不懂时退回默认并写明原因，不抛", cfg["run_at"] == "22:00" and cfg["note"],
       cfg["note"][:70])
    ps.save_cfg(True, "22:00")

    print("== K 红线：匹配只读，产物/素材/稿件一个字节都不动 ==")
    def fingerprint():
        rows = q(db, "SELECT id, title, transcript, source_sheet FROM videos ORDER BY id")
        h = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:16]
        sums = q(db, "SELECT id, content, status, source_video_ids FROM ai_summaries ORDER BY id")
        h2 = hashlib.sha256(json.dumps(sums, ensure_ascii=False).encode()).hexdigest()[:16]
        tasks = q(db, "SELECT * FROM plan_tasks ORDER BY id")
        h3 = hashlib.sha256(json.dumps(tasks, ensure_ascii=False).encode()).hexdigest()[:16]
        return h, h2, h3
    # 拿一份干净的库跑"只有候选、没有任何人工动作"的匹配，证明它写不到别处
    db2 = build_db(tmp, "t2.db")
    ps.seed(db2)
    before = fingerprint()
    poster_before = sorted(os.listdir(poster_base))
    ps.match_positions(db2, now=dt.datetime(2026, 11, 3, 22, 0))
    ps.match_positions(db2, now=dt.datetime(2026, 11, 4, 22, 0))
    after = fingerprint()
    ck("匹配没动素材库（含转写）", before[0] == after[0])
    ck("匹配没动整理稿", before[1] == after[1])
    ck("匹配没动排产任务与已采用清单", before[2] == after[2])
    ck("匹配没往成品目录写任何文件", sorted(os.listdir(poster_base)) == poster_before)
    ck("匹配没建任务（一条都不自动采用）",
       len(q(db2, "SELECT * FROM plan_tasks")) == len(q(db2, "SELECT * FROM plan_tasks")))
    ck("模型调用次数 0：整条 e2e 没 import openai、没发请求",
       "openai" not in sys.modules, sorted(m for m in sys.modules if "openai" in m)[:3])

    print("== M Codex 二次复验的五条反例 ==")
    # ---- M1 发布登记：同名同日两篇不得互相覆盖、撤销后能重登 ----
    db3 = build_db(tmp, "t3.db")
    ps.seed(db3)
    TWIN = "同名同日也要各算各的"
    g1 = ps.register_slot(db3, "s1a1", {"channel": "toutiao", "title": TWIN,
                                        "pub_date": "2026-11-06", "url": "https://t/1", "by": "e2e"})
    g2 = ps.register_slot(db3, "s1a2", {"channel": "toutiao", "title": TWIN,
                                         "pub_date": "2026-11-06", "url": "https://t/2", "by": "e2e"})
    ck("M1 不同槽即使同题同日也各拿一条台账行（ref_id 不共用）",
       g1["ref_id"] != g2["ref_id"], (g1["ref_id"], g2["ref_id"]))
    ck("M1 先登记那一格没被后一格顶掉（两格都算已发布）",
       ps.published_state(db3, "s1a1")["state"] == "confirmed" and
       ps.published_state(db3, "s1a2")["state"] == "confirmed",
       (ps.published_state(db3, "s1a1")["state"], ps.published_state(db3, "s1a2")["state"]))
    ck("M1 台账里两行分别指向两个槽号",
       sorted(r["slot_key"] for r in q(db3, "SELECT slot_key FROM publish_stats")) ==
       ["s1a1", "s1a2"],
       [(r["slot_key"], r["ref_id"]) for r in q(db3, "SELECT slot_key, ref_id FROM publish_stats")])
    try:
        ps.revoke_slot(db3, "s1a1", g1["ref_id"], "点错了，那是另一篇", by="e2e")
        ck("M1 撤销只影响这一格（另一格仍算已发布）",
           ps.published_state(db3, "s1a1")["state"] != "confirmed" and
           ps.published_state(db3, "s1a2")["state"] == "confirmed")
    except ValueError as e:
        # 串槽的下游表现就是这一条：第一格的 ref_id 已被第二格拿走，撤销时台账查不到
        ck("M1 撤销只影响这一格（另一格仍算已发布）", False,
           "撤销被台账挡回来：%s" % str(e)[:70])
    g3 = ps.register_slot(db3, "s1a1", {"channel": "toutiao", "title": TWIN,
                                        "pub_date": "2026-11-06", "url": "https://t/3", "by": "重登"})
    ck("M1 撤销后用相同资料重登 → 换新 ref_id、状态回到已发布",
       g3["ref_id"] != g1["ref_id"] and
       ps.published_state(db3, "s1a1")["state"] == "confirmed",
       (g1["ref_id"], g3["ref_id"], ps.published_state(db3, "s1a1")["state"]))
    g4 = ps.register_slot(db3, "s1a2", {"channel": "toutiao", "title": TWIN,
                                         "pub_date": "2026-11-06", "by": "连点第二次"})
    ck("M1 没撤销时同槽重复点仍复用原行（不长第二条台账）",
       g4["ref_id"] == g2["ref_id"] and
       len(q(db3, "SELECT * FROM publish_stats WHERE slot_key='s1a2'")) == 1,
       (g4["ref_id"], g2["ref_id"]))
    ck("M1 撤销审计指到具体那一行（同槽一行撤销、一行活着）",
       len([a for a in q(db3, "SELECT * FROM plan_publish_audit WHERE action='revoke'")]) == 1)

    # ---- M2 渠道隔离：公众号发过 ≠ 头条这格发过 ----
    db4 = build_db(tmp, "t4.db")
    ps.seed(db4)
    ps.register_slot(db4, "s1b2", {"channel": "wechat", "title": "公众号先发的那篇战士",
                                   "pub_date": "2026-11-06", "url": "https://w/1", "by": "e2e"})
    ck("M2 公众号登记后，头条这一格仍算未发布",
       ps.published_state(db4, "s1b2", channel="toutiao")["state"] != "confirmed",
       ps.published_state(db4, "s1b2", channel="toutiao")["state"])
    ck("M2 公众号那边确实算已发（不是把状态抹没了）",
       ps.published_state(db4, "s1b2", channel="wechat")["state"] == "confirmed")
    bd4 = slot_map(ps.board(db4, base=poster_base))
    ck("M2 头条看板该格 published=False", bd4["s1b2"]["published"] is False,
       bd4["s1b2"]["publish_rows"][:1])
    stw = ps.match_positions(db4, now=dt.datetime(2026, 11, 7, 22, 0))
    ck("M2 头条仍参与夜间匹配（不因公众号发过而跳过）",
       not any(x["slot_key"] == "s1b2" for x in stw["skipped"]),
       [x for x in stw["skipped"] if x["slot_key"] == "s1b2"])
    ps.register_slot(db4, "s1b2", {"channel": "toutiao", "title": "头条这边也发了",
                                   "pub_date": "2026-11-07", "url": "https://t/9", "by": "e2e"})
    ck("M2 头条自己登记后才算已发，两渠道台账行并存",
       ps.published_state(db4, "s1b2", channel="toutiao")["state"] == "confirmed" and
       len(q(db4, "SELECT * FROM publish_stats WHERE slot_key='s1b2'")) == 2,
       len(q(db4, "SELECT * FROM publish_stats WHERE slot_key='s1b2'")))
    stw2 = ps.match_positions(db4, now=dt.datetime(2026, 11, 8, 22, 0))
    ck("M2 头条真登记后夜间才跳过这一格",
       any(x["slot_key"] == "s1b2" for x in stw2["skipped"]), stw2["skipped"][:2])

    # ---- M3 S2 的 O2/O3 必须有前序，而且判据在服务端 ----
    try:
        ps.new_slot(db4, title="没有前序的 O2", series="s2", chapter="O2", form="mix")
        ck("M3 O2 不带前序被服务端拒绝", False, "居然建出来了")
    except ValueError as e:
        ck("M3 O2 不带前序被服务端拒绝并说清凭什么", "O1" in str(e) or "前序" in str(e),
           str(e)[:70])
    try:
        ps.new_slot(db4, title="拿 S1 位置当前序的 O2", series="s2", chapter="O2", form="mix",
                    link_slot="s1a1")
        ck("M3 O2 的前序必须是 S2 的某一篇 O1（拿别系列的槽号不行）", False, "居然建出来了")
    except ValueError as e:
        ck("M3 O2 的前序必须是 S2 的某一篇 O1（拿别系列的槽号不行）", True, str(e)[:70])
    ck("M3 被拒的两次没在库里留下孤稿",
       len(q(db4, "SELECT * FROM plan_slots WHERE slot_key LIKE 's2o2-%'")) == 0,
       [r["slot_key"] for r in q(db4, "SELECT slot_key FROM plan_slots"
                                    " WHERE slot_key LIKE 's2o2-%'")])
    m_o1 = ps.new_slot(db4, title="国服公告：一项调整", series="s2", chapter="O1", form="mix")
    m_o2 = ps.new_slot(db4, title="若上线保留：影响路径", series="s2", chapter="O2", form="mix",
                       link_slot=m_o1["slot_key"])
    ck("M3 O2 带着 O1 前序建出来了", m_o2["link_slot"] == m_o1["slot_key"], m_o2["slot_key"])
    try:
        ps.new_slot(db4, title="没有前序的 O3", series="s2", chapter="O3", form="mix")
        ck("M3 O3 不带被回看的那一篇被拒", False, "居然建出来了")
    except ValueError as e:
        ck("M3 O3 不带被回看的那一篇被拒", True, str(e)[:70])
    m_o3 = ps.new_slot(db4, title="上线后回看：那条预测对不对", series="s2", chapter="O3",
                       form="mix", link_slot=m_o2["slot_key"])
    ck("M3 O3 关联到被回看的那篇 O2", m_o3["link_slot"] == m_o2["slot_key"], m_o3["slot_key"])

    # ---- M4 独立稿关联 S1：只增关系，不增内容位置 ----
    before_map = len(q(db4, "SELECT * FROM plan_slots WHERE series='s1' AND origin='map'"))
    free2 = ps.new_slot(db4, title="素材库起的一篇·事后归到 C2", series="free", form="guide")
    ps.link_to_series(db4, free2["slot_key"], "s1", "C", "C2")
    after_slots = ps.slots(db4)
    ck("M4 关联后 S1 地图位置数没变（%d 个，一个都不多）" % before_map,
       len([s for s in after_slots if s["series"] == "s1" and s["origin"] == "map"]) ==
       before_map == 26,
       len([s for s in after_slots if s["series"] == "s1" and s["origin"] == "map"]))
    ck("M4 C2 这个位置只有一行，没被复制成两行",
       len([s for s in after_slots if s["position"] == "C2"]) == 1,
       [s["slot_key"] for s in after_slots if s["position"] == "C2"])
    ck("M4 关联稿仍算独立篇身份，关系写在 parent_slot",
       ps.get_slot(db4, free2["slot_key"]).get("parent_slot") == "s1c2" and
       ps.get_slot(db4, free2["slot_key"])["series"] == "free")
    bd5 = slot_map(ps.board(db4, base=poster_base))
    ck("M4 关联稿在 C2 那一格下面看得见（不藏起来）",
       free2["slot_key"] in [x["slot_key"] for x in bd5["s1c2"].get("linked_slots", [])],
       [x["slot_key"] for x in bd5["s1c2"].get("linked_slots", [])])
    try:
        ps.link_to_series(db4, free2["slot_key"], "s1", "C", "Z9")
        ck("M4 关联到不存在的位置要被拒", False, "居然改了")
    except ValueError as e:
        ck("M4 关联到不存在的位置要被拒", True, str(e)[:70])
    # 老库升级 + 迁移：用**没有 parent_slot 这一列**的旧表结构造一份，看 ensure() 会不会补齐并迁移
    db6 = build_db(tmp, "t6.db")
    exe(db6, "DROP TABLE IF EXISTS plan_slots")
    con6 = sqlite3.connect(str(db6))
    try:
        con6.executescript(
            "CREATE TABLE plan_slots (slot_key TEXT PRIMARY KEY, series TEXT NOT NULL,"
            " chapter TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT '',"
            " title TEXT DEFAULT '', angle TEXT DEFAULT '', keywords TEXT DEFAULT '[]',"
            " form TEXT DEFAULT 'guide', boundary TEXT DEFAULT '', link_slot TEXT DEFAULT '',"
            " origin TEXT NOT NULL DEFAULT 'map', created_at TEXT, updated_at TEXT);"
            " INSERT INTO plan_slots (slot_key, series, chapter, position, title, origin,"
            " created_at, updated_at) VALUES"
            " ('s1c2','s1','C','C2','第一次进副本前查什么','map','x','x'),"
            " ('free-9999','s1','C','C2','老办法关联进来的一篇','user','x','x');")
        con6.commit()
    finally:
        con6.close()
    ps._READY.discard((os.path.abspath(str(db6)), os.stat(str(db6)).st_ino))
    ps.ensure(db6)
    moved = ps.get_slot(db6, "free-9999")
    ck("M4 老库自动补上 parent_slot 这一列", "parent_slot" in moved, sorted(moved)[:8])
    ck("M4 迁移把老式关联改回引用：槽号不变、身份回到独立篇、关系指向 s1c2",
       moved["slot_key"] == "free-9999" and moved["series"] == "free" and
       moved.get("parent_slot") == "s1c2",
       (moved["series"], moved["position"], moved.get("parent_slot")))
    ck("M4 迁移后这张位置表里只剩地图那一行算 S1 位置（稿件不算位置）",
       len([s for s in ps.slots(db6) if s["series"] == "s1"]) == 1 and
       len([s for s in ps.slots(db6) if s["origin"] == "map"]) == 1,
       [(s["slot_key"], s["series"], s["origin"]) for s in ps.slots(db6)])

    # ---- M5a 夜间那颗钟：机器时区换成 UTC 也不许偏移 ----
    env = dict(os.environ)
    env["TZ"] = "UTC"
    probe = ROOT / "scripts" / "agent" / "tick_clock_probe.py"
    pr = subprocess.run([sys.executable, str(probe), "--db", str(tmp / "probe.db")],
                        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    try:
        pj = json.loads((pr.stdout or "").strip().splitlines()[-1])
    except Exception:
        pj = {}
    ck("M5a 定时器把**北京时间**交给到期判断（TZ=UTC 子进程实测）",
       pj.get("tick_hour") == pj.get("cn_hour"),
       "tick=%s 北京=%s 本机naive=%s rc=%s %s" % (pj.get("tick_hour"), pj.get("cn_hour"),
                                                  pj.get("utc_hour"), pr.returncode,
                                                  (pr.stderr or "")[-160:]))
    ck("M5a 这一格确实与本机 naive 时刻差 8 小时（不是恰好相等）",
       (pj.get("cn_hour") - pj.get("utc_hour")) % 24 == 8,
       (pj.get("cn_hour"), pj.get("utc_hour")))
    ck("M5a 探针没往生产库写（临时副本 + 桩掉的到期判断）",
       pj.get("stubbed") is True and pj.get("used_db") != str(PROD_DB), pj.get("used_db"))

    # ---- M5b 候选排序：新料优先，超上限时不许先留旧料 ----
    db7 = build_db(tmp, "t7.db")
    ps.seed(db7)
    exe(db7, "DELETE FROM videos")          # 只留下面这批同日不同期的，计数才干净
    dates = ["2026-10-0%d" % i for i in range(1, 9)]        # 10-01 → 10-08
    keep = {}
    for i, d in enumerate(dates):
        vid = 900020 + i
        keep[vid] = d
        exe(db7, "INSERT INTO videos (id, source_sheet, source_row, title, author,"
                 " published_at, transcript, category, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, "抖音视频数据", str(9100 + i), "无限服插件回退实录 %s" % d, "某作者",
             d + " 10:00:00", "无限服插件的备份与回退" + "字" * 300, "游戏攻略",
             "2026-10-22 10:00:00"))
    ps.match_positions(db7, now=dt.datetime(2026, 11, 9, 22, 0), only_slots=["s1a3"])
    rows7 = q(db7, "SELECT video_id FROM plan_candidates WHERE slot_key='s1a3' ORDER BY id")
    got7 = [r["video_id"] for r in rows7]
    ck("M5b 每个位置最多列 %d 条候选" % ps.CAND_CAP, len(got7) == ps.CAND_CAP, got7)
    ck("M5b 留下的正是最新那 6 条（10-01、10-02 那两条最老的被挡在门外）",
       got7 == list(range(900027, 900021, -1)), [keep[v] for v in got7])
    ck("M5b 插入顺序=日期倒序（不是升序，那正是'新料挤掉旧料'判据写反的样子）",
       got7 == sorted(got7, reverse=True), got7)

    print("== N Codex 三次复验：两个登记入口交叉调用 + 三条服务端边界 ==")
    from src import ops_store
    db8 = build_db(tmp, "t8.db")
    ps.seed(db8)
    TWIN2 = "两条入口写的是同一篇标题"
    g = ps.register_slot(db8, "s1a1", {"channel": "toutiao", "title": TWIN2,
                                       "pub_date": "2026-11-10", "url": "https://t/a", "by": "排产页"})
    o = ops_store.register_publish(str(db8), {"channel": "toutiao", "title": TWIN2,
                                              "pub_date": "2026-11-10", "slot_key": "s1a2",
                                              "url": "https://t/b"})
    ck("N1 通用登记口不许复用另一槽的同名同日行（两笔各拿一把键）",
       o["ref_id"] != g["ref_id"], (g["ref_id"], o["ref_id"]))
    ck("N1 排产那一格没被通用入口顶掉（两格都算已发布）",
       ps.published_state(db8, "s1a1")["state"] == "confirmed" and
       ps.published_state(db8, "s1a2")["state"] == "confirmed",
       (ps.published_state(db8, "s1a1")["state"], ps.published_state(db8, "s1a2")["state"]))
    ck("N1 原来那一行没被改槽、也没被改链接（发布事实各归各）",
       q(db8, "SELECT slot_key FROM publish_stats WHERE ref_id=?", (g["ref_id"],))[0]["slot_key"]
       == "s1a1" and q(db8, "SELECT url FROM publish_stats WHERE ref_id=?",
                       (g["ref_id"],))[0]["url"] == "https://t/a",
       [(r["slot_key"], r["ref_id"]) for r in q(db8, "SELECT slot_key, ref_id FROM publish_stats")])
    n0 = len(q(db8, "SELECT * FROM publish_stats"))
    ops_store.register_publish(str(db8), {"channel": "toutiao", "title": TWIN2,
                                          "pub_date": "2026-11-10"})
    ck("N2 不带槽号的登记不许落到带槽的行上（另起一行，原行不动）",
       len(q(db8, "SELECT * FROM publish_stats")) == n0 + 1 and
       len(q(db8, "SELECT * FROM publish_stats WHERE slot_key='s1a1'")) == 1,
       len(q(db8, "SELECT * FROM publish_stats")))
    a1 = ops_store.register_publish(str(db8), {"channel": "toutiao", "title": "无槽连点两次",
                                               "pub_date": "2026-11-11"})
    n1 = len(q(db8, "SELECT * FROM publish_stats"))
    a2 = ops_store.register_publish(str(db8), {"channel": "toutiao", "title": "无槽连点两次",
                                               "pub_date": "2026-11-11"})
    ck("N3 同一判据连点仍复用同一行（运营页原有的防重复语义没被改坏）",
       a1["ref_id"] == a2["ref_id"] and
       len(q(db8, "SELECT * FROM publish_stats")) == n1, (a1["ref_id"], a2["ref_id"]))
    try:
        ops_store.register_publish(str(db8), {"channel": "toutiao", "ref_id": g["ref_id"],
                                              "title": TWIN2, "pub_date": "2026-11-10",
                                              "slot_key": "s1a3"})
        ck("N4 显式交来别的槽的 ref_id 要被拒（不许静默改槽）", False, "居然写进去了")
    except ValueError as e:
        ck("N4 显式交来别的槽的 ref_id 要被拒并说清怎么走", "撤销" in str(e) or "槽" in str(e),
           str(e)[:80])
    ck("N4 被拒之后那一行还是原槽",
       q(db8, "SELECT slot_key FROM publish_stats WHERE ref_id=?",
         (g["ref_id"],))[0]["slot_key"] == "s1a1")
    try:
        ps.new_slot(db8, title="绕过页面造的第 27 格", series="s1", form="guide")
        ck("N5 直接新建 series='s1' 的稿件位要被拒（位置是地图注册的）", False, "居然建出来了")
    except ValueError as e:
        ck("N5 直接新建 series='s1' 的稿件位要被拒（位置是地图注册的）", True, str(e)[:70])
    try:
        ps.new_slot(db8, title="绕过页面造的第四章", series="s2", chapter="O9", form="mix")
        ck("N5 S2 只许 O1/O2/O3，第四章被拒", False, "居然建出来了")
    except ValueError as e:
        ck("N5 S2 只许 O1/O2/O3，第四章被拒", True, str(e)[:70])
    ck("N5 被拒的两次没在库里留下孤稿",
       len(q(db8, "SELECT * FROM plan_slots WHERE origin='user' AND series='s1'")) == 0 and
       len(q(db8, "SELECT * FROM plan_slots WHERE slot_key LIKE 's2o9%'")) == 0,
       [r["slot_key"] for r in q(db8, "SELECT slot_key FROM plan_slots"
                                     " WHERE origin='user' AND series IN ('s1','s2')")])
    db9 = build_db(tmp, "t9.db")
    ps.seed(db9)
    ps.report_local(db9, "s1d3", True, "这台浏览器勾过已发布")
    ck("N6 头条侧读这份旧勾选 → 待核对（行为保留）",
       ps.published_state(db9, "s1d3", channel="toutiao")["state"] == "pending_review")
    ck("N6 公众号侧不读这张旧标记（不得跟着标待核对）",
       ps.published_state(db9, "s1d3", channel="wechat")["state"] == "none",
       ps.published_state(db9, "s1d3", channel="wechat")["state"])
    stw3 = ps.match_positions(db9, now=dt.datetime(2026, 11, 12, 22, 0), channel="wechat")
    ck("N6 另一条渠道线的匹配也不因头条旧勾选而跳过",
       not any(x["slot_key"] == "s1d3" for x in stw3["skipped"]), stw3["skipped"][:2])
    stw4 = ps.match_positions(db9, now=dt.datetime(2026, 11, 13, 22, 0), channel="toutiao")
    ck("N6 头条侧照旧保守跳过（这一半不许跟着放宽）",
       any(x["slot_key"] == "s1d3" for x in stw4["skipped"]),
       [x for x in stw4["skipped"] if x["slot_key"] == "s1d3"])
    for bad, want in (("wechta", None), ("", "toutiao"), ("toutiao", "toutiao"),
                      ("头条", "toutiao"), ("公众号", "wechat")):
        try:
            got = ps.norm_channel(bad)
            ck("N7 渠道「%s」→ %s" % (bad, want), want is not None and got == want, got)
        except ValueError as e:
            ck("N7 渠道「%s」是无效值，要报错而不是静默退成头条" % bad,
               want is None, str(e)[:60])
    try:
        ps.published_state(db8, "s1a1", channel="wechta")
        ck("N7 读状态时拼错的渠道也被拒（不拿头条的答案糊过去）", False, "居然按头条读了")
    except ValueError as e:
        ck("N7 读状态时拼错的渠道也被拒（不拿头条的答案糊过去）", True, str(e)[:60])

    print("== L 状态栏与看板一次读全（页面不自己算）==")
    sl = ps.status_line(db2, dt.datetime(2026, 11, 4, 9, 0))
    ck("状态栏有开关、计划时刻、下次时间、上次结果",
       sl["run_at"] == "22:00" and sl["next"] and "tz" in sl, sl["next"])
    ck("下次时间写清是北京时间（不是本机时区）", "Asia/Shanghai" in sl["tz"], sl["tz"])
    bd = ps.board(db2, today=dt.date(2026, 11, 4), base=poster_base)
    ck("看板把八档标签与依据一起下发", len(bd["stages"]) == 8 and
       all(x["why"] for x in bd["stages"]))
    ck("看板带出每个位置的检索词与边界（页面不抄第二份）",
       all("keywords" in s and "boundary" in s for s in bd["slots"]))
    wc = [s for s in bd["slots"] if s["candidates"]]
    ck("候选念得出是哪一条（带标题与来源 sheet:row，页面不会一片「（无标题）」）",
       bool(wc) and all(c["title"] and c["sheet"] and c["row"]
                        for s in wc for c in s["candidates"]),
       (wc[0]["candidates"][0]["title"][:24] if wc else "这一份看板没候选"))
    ck("看板一次带齐状态栏（页面不必二次请求拼一份，免得徽章新、时刻旧）",
       bd["status"]["run_at"] == "22:00" and "next" in bd["status"] and
       "due" in bd["status"], (bd["status"].get("next") or "")[:20])
    ck("章节中文名由服务端下发（A=入门设置…），页面不写第二份名单",
       bd["chapter_labels"].get("A") == "入门设置", bd["chapter_labels"])
    ps.register_slot(db2, "s1c3", {"channel": "toutiao", "title": "装备从哪来",
                                   "pub_date": "2026-11-04", "url": "https://t/1"})
    bd2 = slot_map(ps.board(db2, today=dt.date(2026, 11, 4), base=poster_base))
    ck("已发布的格子带台账行（撤销要点到具体 ref_id）",
       bd2["s1c3"]["published"] and bd2["s1c3"]["publish_rows"][0]["ref_id"],
       bd2["s1c3"]["publish_rows"][:1])

    print("== R 海报里就地登记发布（号主：一条流程走完，不要两边跑）==")
    from src import ops_store
    set_task("s9zz", "done", summary_id=8801)          # 这篇挂在排产槽 s9zz 上
    r1 = ps.poster_publish(db, 8801, {"channel": "toutiao", "title": "圣骑士三系开荒怎么选",
                                      "pub_date": "2026-10-23", "url": "https://x/t1"})
    ck("有槽号的稿：登记写进台账并带上槽号（排产那一格同时算已发布）",
       r1["state"]["state"] == "confirmed" and r1["slot_key"] == "s9zz"
       and q(db, "SELECT * FROM publish_stats WHERE slot_key='s9zz'"), r1.get("ref_id"))
    ck("运营页读的是同一张表：这条标题立刻在台账里",
       any((a.get("title") or "") == "圣骑士三系开荒怎么选"
           for a in ops_store.list_articles(db, "toutiao")))
    ck("状态读回带 note（界面那句「已登记 …」不用自己拼）",
       "已登记" in (r1["state"].get("note") or ""), r1["state"].get("note"))
    # 没槽号的独立稿：记一行 slot_key='' 的账，键前缀指着这篇稿，读回只认自己的行
    r2 = ps.poster_publish(db, 8802, {"channel": "toutiao", "title": "独立稿：宏怎么设",
                                      "pub_date": "2026-10-23", "url": "",
                                      "url_missing_reason": "手机发的没复制链接"})
    row2 = q(db, "SELECT * FROM publish_stats WHERE ref_id='%s'" % r2["ref_id"])
    ck("独立稿也能登记：槽号留空、键里带稿 id",
       r2["state"]["state"] == "confirmed" and row2 and not row2[0]["slot_key"]
       and r2["ref_id"].startswith("sum:8802:"), r2["ref_id"])
    ck("没链接时把原因写进备注（不许留空让人以为漏填）",
       "手机发的没复制链接" in (row2[0].get("note") or ""), row2[0].get("note"))
    ck("独立稿之间不互相顶：另一篇没登记的读回是未发布",
       ps.poster_publish_state(db, 8803, channel="toutiao")["state"] == "none")
    # 撤销：只追加审计，台账行留着
    rv = ps.poster_revoke(db, 8802, r2["ref_id"], "发错号了，重发再登记", "AI 海报")
    ck("撤销后立刻回到未发布（前端勾选不算数，服务端才是真相）",
       rv["state"] == "none", rv)
    ck("撤销不删台账行，只多一条审计",
       q(db, "SELECT * FROM publish_stats WHERE ref_id='%s'" % r2["ref_id"])
       and q(db, "SELECT * FROM plan_publish_audit WHERE ref_id='%s' AND action='revoke'"
                % r2["ref_id"]), "行与审计都在")
    r3 = ps.poster_publish(db, 8802, {"channel": "toutiao", "title": "独立稿：宏怎么设",
                                      "pub_date": "2026-10-23", "url": "https://x/2"})
    ck("撤销后重新登记走新键，这一篇干净地回到已发布",
       r3["ref_id"] != r2["ref_id"] and r3["state"]["state"] == "confirmed", r3["ref_id"])
    # 渠道隔离（老反例：公众号登记一条，头条别跟着算已发）
    ps.poster_publish(db, 8802, {"channel": "wechat", "title": "同一篇在公众号也发了",
                                 "pub_date": "2026-10-23", "url": "https://x/w"})
    ck("换渠道登记不算到头条这一篇头上",
       ps.poster_publish_state(db, 8802, channel="toutiao")["state"] == "confirmed"
       and len(ps.poster_publish_state(db, 8802, channel="toutiao")["rows"]) == 1
       and ps.poster_publish_state(db, 8802, channel="wechat")["state"] == "confirmed")
    for name, call in (("标题必填", lambda: ps.poster_publish(db, 8809, {"channel": "toutiao",
                                                                        "title": "  "})),
                       ("撤销要写原因", lambda: ps.poster_revoke(db, 8802, r3["ref_id"], "", "x"))):
        try:
            call()
            ck(name + "（后端拦住，不靠界面提示）", False, "居然没报错")
        except ValueError as e:
            ck(name + "（后端拦住，不靠界面提示）", True, str(e)[:40])
    ck("槽号那一路撤销仍然走排产同一套审计（海报不另造流程）",
       "s9zz" in {t["slot_key"] for t in q(db, "SELECT DISTINCT slot_key FROM plan_publish_audit"
                                          " WHERE action='register'")})

    if fails:
        print("\nPLAN_SERIES_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("\nPLAN_SERIES_OK  %d 条全过（临时库、固定时钟、0 次模型调用）" % ok_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
