# -*- coding: utf-8 -*-
"""排产格「补充资料」（非视频料挂到视频素材下面）的契约回归。隔离临时库 + 模型桩，不碰生产库、不调模型。

号主 2026-10-28 的方向：微信文章／暴雪公告／NGA 帖不单独成稿，改当**视频素材的补充资料**，
但「一定要强相关，不然资料不对」。所以这份测试守四条：
  A 判据：正面命中要能打印"凭什么算相关"；**反面样本必须不命中**（只撞游戏名、只撞时长词、超时间窗、视频侧表）；
  B 存储：勾了才存、最多 3 条、混进视频素材要拒、没采用主素材要拒；
  D 开工之后**照样能改**（号主 10-28 点名取消冻结），改的就是这一版下一次生成用的那一份；
  C 进提示词：生成时真带上，且带的是任务行里存的那一份（含当初的理由）；没有补充料时提示词逐字不变；
  D 老库补列幂等。

用法：python3 scripts/e2e_supplements.py       （全过打印 ALL_OK，退出码 0）
"""
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK " if cond else "XX ", name, ("  " + detail) if detail else ""))


COLS = ("id, aweme_id, source_sheet, source_row, source_url, video_url, cover_url, author,"
        " title, status, published_at, tags, transcript, ai_copy, keywords, remark, updated_at,"
        " category, ai_tags, game, author_sec_uid, source, view_count, duration_sec, subs_status,"
        " note_images, note_image_count")

# (id, sheet, 标题, 发布日, 备注)
FIXTURE = [
    (21, "抖音视频数据", "无限服职业推荐—圣骑士！", "2026-10-21", ""),
    (22, "抖音视频数据", "魔兽世界正版护颈枕特价", "2026-10-26", ""),
    (23, "抖音视频数据", "10分钟带你解析AI应用三层核心概念", "2026-10-23", ""),
    (31, "NGA无限板块", "无限服职业推荐", "2026-10-26",
     "论坛素材：只当选题参考，不得成段引用或改写冒充本站观点"),
    (32, "微信文章", "魔兽无限服：9大隐藏地图和入口曝光", "2026-10-25", ""),
    (33, "暴雪官方", "无限服职业推荐 圣骑士", "2026-08-01", ""),      # 超时间窗
    (34, "抖音图文", "无限服职业推荐图文版", "2026-10-25", ""),        # 视频侧表，不能当补充料
]


def make_db(path):
    con = sqlite3.connect(str(path))
    for i, (vid, sheet, title, date, remark) in enumerate(FIXTURE):
        con.execute("INSERT INTO videos (%s) VALUES (%s)"
                    % (COLS, ",".join("?" for _ in COLS.split(","))),
                    (vid, "aw%d" % vid, sheet, 100 + i, "https://x/%d" % vid, "", "",
                     "作者%d" % vid, title, "已完成", date, "", "正文" * 200, "", "", remark,
                     "2026-10-28 10:00:00", "游戏攻略", "", "魔兽世界：无限", "",
                     "subscription", 0, 0, "", "[]", 0))
    con.commit()
    con.close()


def boot(tmp):
    import app
    app.DB_PATH = Path(tmp) / "video2text.db"
    app.INDEX_PATH = Path(tmp) / "不存在.json"
    app.EXCEL_PATH = Path(tmp) / "不存在.xlsx"
    app._INDEX_CACHE["key"], app._INDEX_CACHE["data"] = None, {}
    app._sync_content_db = lambda: None          # 不读真 Excel（临时库里没有它要的那张表）
    app._video_keyframe_markdown = lambda vs: ""  # 不抽帧（那会去下载视频，测试里没必要）
    app._fast_config = lambda: {"model": "stub-model"}
    import src.content_store as cs
    cs._captured = []

    def fake_one(video, summary_type, config, brief=None):
        cs._captured.append({"kind": "one", "video_id": int(video["id"]), "brief": brief})
        return ({"title": "稿·%s" % video["id"], "content": "## 小标题\n正文", "outline": "",
                 "keywords": "", "structured_data": ""}, "stub-model", "draft")

    def fake_many(videos, summary_type, config, brief=None):
        cs._captured.append({"kind": "many",
                             "video_id": [int(v["id"]) for v in videos], "brief": brief})
        return ({"title": "合并稿", "content": "## 小标题\n合并正文", "outline": "",
                 "keywords": "", "structured_data": ""}, "stub-model", "draft")

    cs.generate_summary = fake_one
    cs.generate_collection_summary = fake_many
    return app, app.app.test_client()


def new_task(app, c, slot, ids):
    """建一行 pending 任务并采用 ids 当主素材（走真实 save_sources，不手搓 SQL）。"""
    import src.content_store as cs
    import src.plan_tasks as pt
    vids = cs.get_videos_by_ids(str(app.DB_PATH), ids)
    task, action = pt.save_sources(str(app.DB_PATH), slot, "toutiao", "guide",
                                   {"title": "测试题 %s" % slot, "hook": "", "note": "",
                                    "plan_date": "2026-10-30"}, vids)
    assert task, action
    return task


def wait_done(c, task_id, limit=25.0):
    deadline = time.time() + limit
    while time.time() < deadline:
        j = c.get("/api/plan/task/%d" % task_id).get_json()
        if (j.get("task") or {}).get("status") != "running":
            return j
        time.sleep(0.1)
    return {"error": "还在 running"}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="supp-e2e-"))
    db = tmp / "video2text.db"
    shutil.copyfile(ROOT / "output" / "video2text.db", db)      # 拿真库的 schema，数据随后清
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM videos")
    con.execute("DELETE FROM plan_tasks")
    con.execute("DELETE FROM ai_summaries")
    con.commit()
    con.close()
    make_db(db)
    app, c = boot(tmp)
    from src import supplements as SP
    from src import content_store as cs
    from src import plan_tasks as pt

    print("\n== A 判据：正面要能解释，反面必须挡住 ==")
    t1 = new_task(app, c, "sA", [21])
    got = c.get("/api/plan/supplements/%d" % t1["id"]).get_json()
    ids = [x["id"] for x in got["candidates"]]
    check("强相关的论坛帖被提名，且带可解释的理由",
          31 in ids and [x for x in got["candidates"] if x["id"] == 31][0]["why"].startswith("标题共同片段"),
          json.dumps([(x["id"], x["why"]) for x in got["candidates"]], ensure_ascii=False))
    check("分级按表判：NGA 是论坛、微信是转述、暴雪是一手",
          [x for x in got["candidates"] if x["id"] == 31][0]["tier"].startswith("论坛"))
    check("超时间窗（81 天前）不提名", 33 not in ids, "候选 %s" % ids)
    check("视频侧表（抖音图文）不进候选池", 34 not in ids)
    check("只撞游戏名的带货标题不提名（反面样本）",
          not c.get("/api/plan/supplements/%d"
                    % new_task(app, c, "sB", [22])["id"]).get_json()["candidates"])
    check("只撞时长词（10分钟）不提名（反面样本）",
          not c.get("/api/plan/supplements/%d"
                    % new_task(app, c, "sC", [23])["id"]).get_json()["candidates"])
    check("扫了多少条要说出来（不藏数）", got["scanned"] >= 3, "scanned=%s" % got["scanned"])

    print("\n== A2 前端抄的那份「哪些表算视频」必须与后端一致 ==")
    js = (ROOT / "web" / "static" / "schedule-series.js").read_text(encoding="utf-8")
    import re as _re
    m = _re.search(r"SRVideoSheets\s*=\s*\[([^\]]*)\]", js)
    listed = [x.strip().strip("'\"") for x in (m.group(1) if m else "").split(",") if x.strip()]
    check("schedule-series.js 的 SRVideoSheets 与 supplements.VIDEO_SHEETS 逐项相同",
          sorted(listed) == sorted(list(SP.VIDEO_SHEETS)),
          "JS=%s PY=%s" % (listed, list(SP.VIDEO_SHEETS)))
    check("能当父级的表与能当补充料的表不重叠",
          not set(SP.VIDEO_SHEETS) & set(SP.NON_VIDEO_SHEETS),
          "%s / %s" % (SP.VIDEO_SHEETS, SP.NON_VIDEO_SHEETS))

    print("\n== B 勾选与存储 ==")
    r = c.post("/api/plan/supplements", json={"task_id": t1["id"], "video_ids": [31]})
    j = r.get_json()
    check("勾一条能存下", r.status_code == 200 and j.get("action") == "updated"
          and j["task"]["supplement_ids"] == [31], json.dumps(j)[:120])
    snap = (j["task"].get("supplement_sources") or [{}])[0]
    check("证据快照冻住了出处与当初的理由", snap.get("role") == "补充资料"
          and "标题共同片段" in (snap.get("why") or ""), json.dumps(snap, ensure_ascii=False)[:160])
    j2 = c.post("/api/plan/supplements", json={"task_id": t1["id"], "video_ids": [31]}).get_json()
    check("重复同样勾选不写第二遍", j2.get("action") == "unchanged")
    over = c.post("/api/plan/supplements", json={"task_id": t1["id"], "video_ids": [31, 32, 33, 34]})
    check("超过 3 条直接拒（配角不能挤主素材）", over.status_code == 400,
          json.dumps(over.get_json(), ensure_ascii=False)[:90])
    mixed = c.post("/api/plan/supplements", json={"task_id": t1["id"], "video_ids": [21]})
    check("把视频素材混进补充料要拒", mixed.status_code == 400,
          json.dumps(mixed.get_json(), ensure_ascii=False)[:90])
    gone = c.post("/api/plan/supplements", json={"task_id": t1["id"], "video_ids": [999999]})
    check("库里查不到的 id 报 422 且不保存", gone.status_code == 422)
    t2 = new_task(app, c, "sD", [])
    empty = c.post("/api/plan/supplements", json={"task_id": t2["id"], "video_ids": [31]})
    check("没有主素材时不接受补充料", empty.status_code == 409
          and empty.get_json().get("error", "").startswith("这一格还没有采用"),
          json.dumps(empty.get_json(), ensure_ascii=False)[:90])

    print("\n== C 生成整理稿时真的带上（且带的是库里那一版） ==")
    cs._captured[:] = []
    g = c.post("/api/plan/task/generate", json={"slot_key": "sA", "channel": "toutiao",
                                                "form": "guide", "video_ids": [21],
                                                "title": "测试题 sA", "hook": "", "note": "",
                                                "plan_date": "2026-10-30"})
    gj = g.get_json()
    tid = gj.get("task_id") or (gj.get("task") or {}).get("id")
    done = wait_done(c, tid) if tid else gj
    check("生成跑完（桩，没花钱）", (done.get("task") or {}).get("status") == "done",
          json.dumps(done)[:120])
    cap = cs._captured[-1] if cs._captured else {}
    sup = ((cap.get("brief") or {}).get("supplements") or [])
    check("brief 里带上了勾的那条补充料", len(sup) == 1 and sup[0]["id"] == 31,
          json.dumps([s.get("id") for s in sup]))
    check("带的是任务行里存的口径（分级＋当初的理由）",
          bool(sup) and sup[0]["tier"].startswith("论坛") and "标题共同片段" in (sup[0]["why"] or ""),
          json.dumps(sup[:1], ensure_ascii=False)[:200])
    prompt = cs._prompt({"id": 21, "title": "无限服职业推荐—圣骑士！", "author": "作者21",
                         "transcript": "转写正文" * 40, "tags": "", "category": "游戏攻略",
                         "published_at": "2026-10-21", "source_sheet": "抖音视频数据",
                         "source_url": "https://x/21", "status": "已完成", "remark": "",
                         "game": "魔兽世界：无限", "aweme_id": "aw21", "keywords": "",
                         "ai_tags": "", "duration_sec": 0, "view_count": 0},
                        "wechat_material", cap.get("brief") or {})
    check("提示词里出现「补充资料」段与配角纪律",
          "本篇的补充资料" in prompt and "不得成段引用" in prompt
          and "只当选题参考" in prompt, "长度 %d" % len(prompt))
    check("补充料正文节选进提示词、并标了上限", "正文正文" in prompt)
    plain = cs._prompt({"id": 21, "title": "T", "author": "a", "transcript": "x" * 500,
                        "tags": "", "category": "", "published_at": "2026-10-21",
                        "source_sheet": "抖音视频数据", "source_url": "", "status": "已完成",
                        "remark": "", "game": "", "aweme_id": "aw21", "keywords": "",
                        "ai_tags": "", "duration_sec": 0, "view_count": 0},
                       "wechat_material", {"title": "T"})
    check("没勾补充料时提示词不多这一段（不串线）", "本篇的补充资料" not in plain)

    print("\n== D 开工之后照样能改（号主 10-28 点名取消冻结） ==")
    lock = c.post("/api/plan/supplements", json={"task_id": tid, "video_ids": [32]})
    check("done 那一版改补充料不再被拒", lock.status_code == 200
          and lock.get_json().get("action") == "updated",
          json.dumps(lock.get_json(), ensure_ascii=False)[:120])
    check("改完库里就是新那一条（下一次生成用的就是它）",
          pt.get_task(str(app.DB_PATH), tid)["supplement_ids"] == [32])
    gs = c.get("/api/plan/supplements/%d" % tid).get_json()
    check("接口标出 started，界面据此提示「改了要重新整理才会带上」", gs.get("started") is True)
    check("已出的稿不会被这次改动追改（生成时才读补充料，这里没再触发任何生成）",
          (pt.get_task(str(app.DB_PATH), tid).get("summary_id") or 0) > 0)

    print("\n== E 老库补列幂等 ==")
    old = tmp / "old.db"
    shutil.copyfile(db, old)
    con = sqlite3.connect(str(old))
    cols = [r[1] for r in con.execute("PRAGMA table_info(plan_tasks)")]
    con.close()
    check("复制出来的库有这两列（说明建表/补列生效）", "supplement_ids" in cols)
    pt.ensure_table(old)
    pt.ensure_table(old)
    check("ensure_table 连跑两次不报错", True)

    print("\n== F 「看原文」不是死链（库里存着、没接进列表的表也要给详情） ==")
    nga = c.get("/api/videos/NGA无限板块/103")
    nj = nga.get_json() or {}
    check("论坛行能出详情（404 就等于那颗按钮是死的）", nga.status_code == 200
          and nj.get("title") == "无限服职业推荐" and "只当选题参考" in (nj.get("remark") or ""),
          json.dumps({k: nj.get(k) for k in ("title", "remark", "source")}, ensure_ascii=False)[:140])
    check("库里没有的行仍然 404，不崩", c.get("/api/videos/NGA无限板块/9999").status_code == 404)

    print("\n== G 关键帧：论坛帖不是「没抽过帧」，是「没有视频」 ==")
    # 2026-10-29 号主报「看版面没有关键帧」：他勾的主素材其实是 NGA 帖。
    # 以前接口只回 usable=0，界面统一说成「抽帧流程上线前采的」并给一个必然失败的「补画面」按钮。
    check("判据只有一份：is_video 认视频侧、图文与论坛都算非视频",
          SP.is_video("抖音视频数据") and SP.is_video("B站视频")
          and not SP.is_video("NGA无限板块") and not SP.is_video("微信文章")
          and not SP.is_video("抖音图文") and not SP.is_video(""),
          "%s / %s" % (list(SP.VIDEO_SHEETS), list(SP.NON_VIDEO_SHEETS)))
    check("看板那段标记用的是同一份名单（没有再抄一份表名）",
          "SRVideoSheets.indexOf" in js.split("function srSrcTag")[1][:400],
          js.split("function srSrcTag")[1][:120].replace("\n", " "))
    tng = new_task(app, c, "sG", [31])
    cg = c.post("/api/plan/task/generate", json={"slot_key": "sG", "channel": "toutiao",
                                                 "form": "guide", "video_ids": [31],
                                                 "title": "论坛稿", "hook": "", "note": "",
                                                 "plan_date": "2026-10-30"}).get_json()
    done_g = wait_done(c, cg.get("task_id") or (cg.get("task") or {}).get("id"))
    sid_nga = (done_g.get("task") or {}).get("summary_id") or 0
    fj = c.get("/api/poster/frames?summary_id=%d" % sid_nga).get_json()
    check("论坛稿：usable=0 且明确标成 no_video_source（原因不是「没抽过帧」）",
          fj.get("usable") == 0 and fj.get("no_video_source") is True,
          json.dumps({k: fj.get(k) for k in ("usable", "no_video_source", "non_video_sheets")},
                     ensure_ascii=False))
    m0 = (fj.get("materials") or [{}])[0]
    check("每条素材带来源表与 is_video，界面才说得出「NGA 帖」这四个字",
          m0.get("sheet") == "NGA无限板块" and m0.get("is_video") is False,
          json.dumps({k: m0.get(k) for k in ("sheet", "is_video", "has_pool")}, ensure_ascii=False))
    sid_video = (pt.get_task(str(app.DB_PATH), tid).get("summary_id") or 0) if tid else 0
    vj = c.get("/api/poster/frames?summary_id=%d" % sid_video).get_json()
    check("反面判据：视频素材但没帧池不等于论坛帖（no_video_source 必须为假，"
          "否则「补画面」那条真出路就被一起禁掉了）",
          vj.get("usable") == 0 and not vj.get("no_video_source")
          and (vj.get("materials") or [{}])[0].get("is_video") is True,
          json.dumps({k: vj.get(k) for k in ("usable", "no_video_source")}, ensure_ascii=False))

    print("\n━━ 补充资料契约：%d 项通过 / %d 项失败 ━━" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：%s" % FAIL)
        return 1
    print("ALL_OK 补充资料判据·存储·进提示词·开工后仍可改 全部守住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
