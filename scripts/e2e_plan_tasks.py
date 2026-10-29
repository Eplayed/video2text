# -*- coding: utf-8 -*-
"""排产入口第一单：精确关联、渠道贯通、防重复收费生成（纯本地、不调模型、不碰真库）。

改了什么必须跑它：`src/plan_tasks.py` 那一层、`/api/plan/task*`、`/api/content/summaries/by-video/`、
选题包文件名。这些坏掉的表象都很安静——稿找错了人看不出来、双击多花一次钱看不出来、
公众号的题被当成游戏攻略看不出来，所以不能只靠肉眼看界面。

隔离做法（三条都是硬要求）：
  · 数据库是 `output/video2text.db` 的一份临时拷贝，测试数据绝不进真实库；
  · `generate_summary` / `generate_collection_summary` 全部打桩并计数——
    **一次真实模型调用都不发**，"重复点会不会多花钱"就靠这个计数证明；
  · `OUTPUT_DIR` 指到临时目录，选题包不落在真实 `output/选题包/`。

用法：python3 scripts/e2e_plan_tasks.py      末行 ALL_OK 才算通过
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))
from src import plan_tasks  # noqa: E402

FAILS = []
CALLS = []          # 每次"生成"打桩记一条：(类型, 素材 id 列表)
REAL = {}           # boot() 把真的 generate_summary / collection 存这里，K 段要还原
SLOW = {"on": False}


def _gate():
    """把打桩的生成"挂住"，用来测 running 中重复点。

    不挂就测不到：桩函数瞬间返回，第二次点下去时任务已经 done，
    走的是「继续处理」那条分支（返回 200），而不是"正在跑、不许再起"（409）。
    真跑一次生成要十几秒，人在这期间双击是完全可能的，所以这一刀必须测。
    """
    deadline = time.time() + 6
    while SLOW["on"] and time.time() < deadline:
        time.sleep(0.05)


def ck(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        FAILS.append(name)


def make_db(tmp):
    """真库的一份拷贝 + 四条造出来的素材（id 12 / 112 / 113 / 120）。"""
    db = os.path.join(tmp, "t.db")
    shutil.copyfile(str(ROOT / "output" / "video2text.db"), db)
    con = sqlite3.connect(db)
    for vid, sheet, row, body in ((12, "E2E", 9001, "有正文" * 200),
                                  (112, "E2E", 9002, "有正文" * 200),
                                  (113, "E2E", 9003, "有正文" * 200),
                                  (120, "E2E", 9004, "")):        # 故意没正文
        con.execute(
            "INSERT OR REPLACE INTO videos (id, source_sheet, source_row, title, author,"
            " transcript, published_at, updated_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, sheet, row, "E2E 素材 %d" % vid, "测试号", body, "2026-10-18", "2026-10-18 10:00:00",
             "已完成" if body else "未开始"))
    # 一条合集稿：source_video_ids 里是 112、113 —— 老的字符串 indexOf 会让 id=12 命中它
    con.execute(
        "INSERT INTO ai_summaries (video_id, summary_type, title, content, status,"
        " source_video_ids, created_at, updated_at) VALUES (112,'toutiao_mix','E2E 合集稿','正文',"
        " 'draft','[112, 113]','2026-10-18 10:00:00','2026-10-18 10:00:00')")
    # 600 条普通稿，专门测"前端只拉前 500 篇"那一刀
    for i in range(600):
        con.execute(
            "INSERT INTO ai_summaries (video_id, summary_type, title, content, status,"
            " source_video_ids, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (900000 + i, "game_guide", "E2E 灌量稿 %d" % i, "正文", "draft",
             json.dumps([900000 + i]), "2026-01-01 00:00:00", "2026-01-01 00:00:00"))
        if i % 100 == 0:
            con.execute("INSERT OR REPLACE INTO videos (id, source_sheet, source_row, title,"
                        " transcript, updated_at, status) VALUES (?,?,?,?,?,?,'已完成')",
                        (900000 + i, "E2E", 20000 + i, "灌量素材 %d" % i, "正文" * 50,
                         "2026-01-01 00:00:00"))
    con.commit()
    con.close()
    return db


def boot(db):
    import app
    app.DB_PATH = Path(db)
    app.OUTPUT_DIR = Path(db).parent / "output"
    app._sync_content_db = lambda: None          # 不读真 Excel
    app._fast_config = lambda: {"model": "stub-model"}
    app._video_keyframe_markdown = lambda vs: ""  # 不抽帧（那会去下载视频）

    def fake_one(video, summary_type, config, brief=None):
        CALLS.append((summary_type, [int(video["id"])]))
        _gate()
        return ({"title": "稿·%s·%s" % (summary_type, video["id"]),
                 "content": "## 小标题\n正文", "outline": "", "keywords": "",
                 "structured_data": ""}, "stub-model", "draft")

    def fake_many(videos, summary_type, config, brief=None):
        CALLS.append((summary_type, [int(v["id"]) for v in videos]))
        _gate()
        return ({"title": "合并稿·%s" % summary_type, "content": "## 小标题\n合并正文",
                 "outline": "", "keywords": "", "structured_data": ""}, "stub-model", "draft")

    import src.content_store as cs
    REAL["one"] = cs.generate_summary
    REAL["many"] = cs.generate_collection_summary
    cs.generate_summary = fake_one
    cs.generate_collection_summary = fake_many
    return app, app.app.test_client()


def wait_done(client, task_id, limit=12.0):
    """等后台线程跑完。打桩的生成几乎是瞬间，给 12 秒是防哪一步卡死时别把测试挂住。"""
    deadline = time.time() + limit
    while time.time() < deadline:
        j = client.get("/api/plan/task/%d" % task_id).get_json()
        st = (j.get("task") or {}).get("status")
        if st != "running":
            return j
        time.sleep(0.15)
    return {"error": "任务超时仍在 running"}


def drain(client, resp_json):
    """POST 回来 mode=started 时先等到跑完再往下走。

    不这么做会假失败：生成在后台线程里，打桩那次调用可能落到**下一节**的计数里
    （第一版就被坑过两次——B 节起的任务算进了 C 节，D 节断言时计数还是空的）。
    """
    if isinstance(resp_json, dict) and resp_json.get("mode") == "started" and resp_json.get("task_id"):
        return wait_done(client, resp_json["task_id"])
    return {}


def plan_get(db, task_id):
    return plan_tasks.get_task(db, task_id)


def payload(slot="odur", channel="toutiao", form="guide", vids=(12,), regenerate=None,
            base_version=None, base_task_id=None, **kw):
    body = {"slot_key": slot, "channel": channel, "form": form, "title": "测试选题",
            "hook": "必须有的那一格", "plan_date": "2026-11-05", "video_ids": list(vids)}
    body.update(kw)
    if regenerate is not None:
        body["regenerate"] = regenerate
    if base_version is not None:
        body["base_version"] = base_version
    if base_task_id is not None:
        body["base_task_id"] = base_task_id
    return body


def main():
    import src.content_store as cs        # noqa: F811  (M/O 段要还原真函数)
    tmp = tempfile.mkdtemp(prefix="plan_tasks_e2e_")
    try:
        db = make_db(tmp)
        app, client = boot(db)

        print("== A 精确关联：id 12 不许命中 112 的合集稿，600 条也不许漏 ==")
        got = client.get("/api/content/summaries/by-video/12").get_json()
        ids = [x["id"] for x in got["items"]]
        ck("by-video/12 不含 [112,113] 那条合集稿",
           all("E2E 合集稿" != (x.get("title") or "") for x in got["items"]), "命中 %s" % ids)
        got112 = client.get("/api/content/summaries/by-video/112").get_json()
        ck("by-video/112 含那条合集稿（精确匹配不是漏匹配）",
           any(x.get("title") == "E2E 合集稿" for x in got112["items"]),
           "%d 条" % len(got112["items"]))
        last = 900000 + 599
        got_last = client.get("/api/content/summaries/by-video/%d" % last).get_json()
        ck("第 600 条灌量稿仍查得到（没有前 500 篇那刀）",
           any(x.get("title") == "E2E 灌量稿 599" for x in got_last["items"]),
           "%d 条" % len(got_last["items"]))
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE ai_summaries RENAME TO ai_summaries_bak")
        con.commit(); con.close()
        r = client.get("/api/content/summaries/by-video/12")
        j = r.get_json()
        ck("查询本身出错 → 500 + error，不返回空列表冒充「没有旧稿」",
           r.status_code == 500 and j.get("error"), "HTTP %s" % r.status_code)
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE ai_summaries_bak RENAME TO ai_summaries")
        con.commit(); con.close()

        print("== B 渠道与内容形式：channel 不等于 summary_type ==")
        r = client.post("/api/plan/task/generate", json=payload(channel="wechat", vids=(12,)))
        j = r.get_json()
        ck("公众号·攻略教程 → wechat_material（不再硬塞 game_guide）",
           j.get("summary_type") == "wechat_material", j.get("summary_type"))
        drain(client, j)
        r = client.post("/api/plan/task/generate", json=payload(form="mix", vids=(12,)))
        ck("头条·资讯整合 → toutiao_mix", r.get_json().get("summary_type") == "toutiao_mix")
        drain(client, r.get_json())
        r = client.post("/api/plan/task/generate", json=payload(form="nope", vids=(12,)))
        ck("认不出的形式 → 400 并给可选值，不许悄悄落默认档",
           r.status_code == 400 and r.get_json().get("forms"), r.status_code)
        r = client.post("/api/plan/task/generate", json=payload(channel="xhs", vids=(12,)))
        ck("本单没开的渠道（小红书）→ 400 明确拒绝", r.status_code == 400, r.status_code)

        print("== C 任务、版本与默认继续 ==")
        del CALLS[:]
        r = client.post("/api/plan/task/generate", json=payload(slot="sky", vids=(12,)))
        j = r.get_json()
        tid = j.get("task_id")
        ck("首次生成 → started", r.status_code == 200 and j.get("mode") == "started", j.get("mode"))
        done = wait_done(client, tid)
        ck("这一路只调了一次生成桩", len(CALLS) == 1, CALLS)
        task = done.get("task") or {}
        sid = task.get("summary_id")
        ck("跑完 status=done 且记了 summary_id", task.get("status") == "done" and sid,
           "%s / %s" % (task.get("status"), sid))
        r = client.post("/api/plan/task/generate", json=payload(slot="sky", vids=(12,)))
        j = r.get_json()
        ck("再点一次（没勾重新生成）→ continue，一次模型都不调",
           j.get("mode") == "continue" and len(CALLS) == 1, "%s / %d 次" % (j.get("mode"), len(CALLS)))
        ck("continue 回的就是那篇已有稿", j.get("summary_id") == sid, "%s vs %s" % (j.get("summary_id"), sid))
        r = client.post("/api/plan/task/generate", json=payload(slot="sky", vids=(12,), regenerate=True))
        j = r.get_json()
        tid2 = j.get("task_id")
        ck("显式重新生成 → 新任务 id、version 2", j.get("mode") == "started" and tid2 != tid,
           "%s → %s" % (tid, tid2))
        wait_done(client, tid2)
        v2 = (client.get("/api/plan/task/%d" % tid2).get_json().get("task") or {})
        ck("新版本 version=2 且指向另一篇稿", v2.get("version") == 2 and v2.get("summary_id")
           and v2["summary_id"] != sid, "v%s / %s" % (v2.get("version"), v2.get("summary_id")))
        con = sqlite3.connect(db)
        both = con.execute("SELECT COUNT(*) FROM ai_summaries WHERE id IN (?,?)",
                           (sid, v2["summary_id"])).fetchone()[0]
        old_row = con.execute("SELECT title FROM ai_summaries WHERE id=?", (sid,)).fetchone()
        con.close()
        ck("前稿那一行还在（重生成不覆盖旧版）", both == 2 and old_row, "查到 %d 行" % both)

        print("== D 一题两条素材 → 一份合并稿，两条都回查得到 ==")
        del CALLS[:]
        r = client.post("/api/plan/task/generate", json=payload(slot="ban", vids=(112, 113)))
        j = r.get_json()
        ck("两条素材走合并档", j.get("mode") == "started" and j.get("combine") is True, j.get("mode"))
        d2 = wait_done(client, j["task_id"])
        ck("合并档只调一次、一次带两条素材", len(CALLS) == 1 and len(CALLS[0][1]) == 2, CALLS)
        t2 = d2.get("task") or {}
        by112 = client.get("/api/content/summaries/by-video/112").get_json()["items"]
        by113 = client.get("/api/content/summaries/by-video/113").get_json()["items"]
        merged = t2.get("summary_id")
        ck("合并稿两条来源都能回查",
           any(x["id"] == merged for x in by112) and any(x["id"] == merged for x in by113),
           "112 查到 %d 条 / 113 查到 %d 条" % (len(by112), len(by113)))

        print("== E 防重复起任务（双击、并发）==")
        del CALLS[:]
        SLOW["on"] = True                       # 让桩"卡在生成里"，模拟真跑那十几秒
        try:
            r = client.post("/api/plan/task/generate", json=payload(slot="day", vids=(12,)))
            first = r.get_json()
            deadline = time.time() + 6
            while time.time() < deadline and not CALLS:
                time.sleep(0.05)                # 等到线程真进了桩，此刻状态一定是 running
            dup = client.post("/api/plan/task/generate", json=payload(slot="day", vids=(12,)))
            ck("running 中重复点 → 409 running（不是再起一个）", dup.status_code == 409
               and dup.get_json().get("mode") == "running", "%s / %s"
               % (dup.status_code, dup.get_json().get("mode")))
            SLOW["on"] = False
            wait_done(client, first["task_id"])
            ck("这一路只起了一次生成", len(CALLS) == 1, CALLS)

            # 并发：两个线程同时投同一题同一渠道（模拟双击与第二个标签页）
            del CALLS[:]
            SLOW["on"] = True
            out, bodies = [], []

            def shoot():
                rr = client.post("/api/plan/task/generate", json=payload(slot="level", vids=(12,)))
                out.append(rr.status_code)
                bodies.append(rr.get_json())

            th = [threading.Thread(target=shoot) for _ in range(2)]
            [t.start() for t in th]; [t.join() for t in th]
            started = sum(1 for c in out if c == 200)
            ck("并发投同一题 → 只有一个 200，另一个 409（撞 UNIQUE 也算对方先起，不许 500）",
               started == 1 and 409 in out, out)
            SLOW["on"] = False
            for b in bodies:
                drain(client, b)                # 等这一路真跑完，别把这次调用算进下一节
            ck("并发这一路真只发了一次生成", len(CALLS) == 1, CALLS)
        finally:
            SLOW["on"] = False

        print("== F 缺正文、失效来源给具体恢复入口 ==")
        r = client.post("/api/plan/task/generate", json=payload(slot="prof", vids=(120,)))
        j = r.get_json()
        ck("没正文的素材 → 422，不起任务，带 action=transcribe",
           r.status_code == 422 and (j.get("problems") or [{}])[0].get("action") == "transcribe",
           json.dumps(j.get("problems"), ensure_ascii=False)[:90])
        ck("422 的文案说清了缺什么", "没有转写正文" in ((j.get("problems") or [{}])[0].get("msg") or ""))
        r = client.post("/api/plan/task/generate", json=payload(slot="prof", vids=(999999,)))
        j = r.get_json()
        ck("库里没有的 id → action=reselect（来源失效那条路）",
           (j.get("problems") or [{}])[0].get("action") == "reselect",
           json.dumps(j.get("problems"), ensure_ascii=False)[:90])
        del CALLS[:]
        r = client.post("/api/plan/task/generate", json=payload(slot="prof", vids=(12, 120)))
        j = r.get_json()
        ck("一条能用一条不能用 → 能用那条照常起，问题单独列给用户",
           j.get("mode") == "started" and len(j.get("problems") or []) == 1,
           "mode=%s problems=%s" % (j.get("mode"), j.get("error") or [p_.get("code") for p_ in (j.get("problems") or [])]))
        wait_done(client, j["task_id"])

        print("== G 采用清单：存服务端、可清空、刷候选不动它 ==")
        r = client.post("/api/plan/task", json=payload(slot="setup", vids=(12,)))
        j = r.get_json()
        ck("确认采用 → 任务落库并带证据快照",
           r.status_code == 200 and (j.get("task") or {}).get("sources"),
           json.dumps((j.get("task") or {}).get("sources"), ensure_ascii=False)[:90])
        ck("证据快照里钉了当时的标题与日期（以后素材行被改也认得回来）",
           (j["task"]["sources"][0].get("title") or "").startswith("E2E 素材")
           and j["task"]["sources"][0].get("date") == "2026-10-18")
        r = client.post("/api/plan/task", json=payload(slot="setup", vids=[]))
        ck("显式清空采用清单存得下来（点「取消采用」到最后一条）",
           r.status_code == 200 and not r.get_json()["task"]["video_ids"],
           r.get_json().get("error", ""))
        r = client.post("/api/plan/task", json={"slot_key": "setup", "channel": "toutiao",
                                                "form": "guide"})
        ck("没带 video_ids 字段 → 400，不当成清空", r.status_code == 400, r.status_code)

        print("== H 同题双渠道互不覆盖 ==")
        del CALLS[:]
        a = client.post("/api/plan/task/generate", json=payload(slot="gift", channel="toutiao",
                                                                form="guide", vids=(12,))).get_json()
        b = client.post("/api/plan/task/generate", json=payload(slot="gift", channel="wechat",
                                                                form="guide", vids=(112,))).get_json()
        wait_done(client, a["task_id"]); wait_done(client, b["task_id"])
        rows = client.get("/api/plan/tasks?slot_key=gift").get_json()["tasks"]
        ck("同一题两条渠道各一条任务", len(rows) == 2
           and {r["channel"] for r in rows} == {"toutiao", "wechat"},
           [(r["channel"], r["form"]) for r in rows])
        ck("两条各指各的稿，没有互相顶掉",
           len({r["summary_id"] for r in rows}) == 2 and all(r["summary_id"] for r in rows),
           [r["summary_id"] for r in rows])
        ck("两渠道各调一次生成（类型不同）",
           sorted(c[0] for c in CALLS) == ["game_guide", "wechat_material"], CALLS)

        print("== I 生成失败要落成 failed，并且失败后还能重试 ==")
        # 打桩改成抛异常：真跑时这一步是模型超时/额度耗尽，任务必须留下原因，
        # 不能停在 running（那会让界面永远转圈、人也再点不动）
        import src.content_store as cs2
        real_one = cs2.generate_summary

        def boom(*args, **kw):   # 位置参数写死三个会被 brief 这个新参数打爆
            CALLS.append(("boom", [int(args[0]["id"])]))
            raise RuntimeError("stub: 模型超时")

        cs2.generate_summary = boom
        r = client.post("/api/plan/task/generate", json=payload(slot="server", vids=(12,)))
        j = r.get_json()
        f = wait_done(client, j["task_id"])
        ft = f.get("task") or {}
        ck("生成抛异常 → 任务落 failed 且写了原因",
           ft.get("status") == "failed" and "模型超时" in (ft.get("error") or ""),
           "%s / %s" % (ft.get("status"), (ft.get("error") or "")[:40]))
        ck("failed 的那版没有 summary_id（不假装出稿了）", not ft.get("summary_id"), ft.get("summary_id"))
        cs2.generate_summary = real_one
        r = client.post("/api/plan/task/generate", json=payload(slot="server", vids=(12,)))
        j = r.get_json()
        ck("failed 之后能直接重试（不会被 running 卡死）", j.get("mode") == "started", j.get("mode"))
        drain(client, j)
        ok2 = (client.get("/api/plan/task/%d" % j["task_id"]).get_json().get("task") or {})
        ck("重试这一版跑成了 done", ok2.get("status") == "done" and ok2.get("summary_id"),
           "%s / %s" % (ok2.get("status"), ok2.get("summary_id")))

        print("== J 选题包文件名带渠道（同日同主题不许互相覆盖）==")
        pack_dir = Path(tmp) / "output" / "选题包"
        for ch in ("toutiao", "wechat"):
            r = client.post("/api/topic/pack", json={"channel": ch, "theme": "E2E 双渠道",
                                                     "videos": ["E2E:9001", "E2E:9002"]})
            ck("选题包 %s 生成成功" % ch, r.status_code == 200 and r.get_json().get("name"),
               str(r.get_json().get("error") or r.get_json().get("name"))[:70])
        names = sorted(p.name for p in pack_dir.glob("*.md")) if pack_dir.exists() else []
        ck("两份包文件名不同（渠道进了文件名）", len(names) == 2
           and any("toutiao" in n for n in names) and any("wechat" in n for n in names), names)


        print("== K R1：已开工版本的证据不可被覆盖 ==")
        del CALLS[:]
        SLOW["on"] = True
        a = client.post("/api/plan/task/generate", json=payload(slot="iso1", vids=(12,))).get_json()
        tid_g = a["task_id"]
        deadline = time.time() + 6
        while time.time() < deadline and not CALLS:
            time.sleep(0.05)
        # running 中改采用清单 → 只能开新版，v1 的 video_ids 一个字不许变
        r = client.post("/api/plan/task", json=payload(slot="iso1", vids=(112, 113)))
        j = r.get_json()
        v1 = plan_get(db, tid_g)
        ck("running 中保存新清单 → 开新版本（action=versioned）",
           j.get("action") == "versioned" and j.get("task", {}).get("version") == 2,
           j.get("action"))
        ck("v1 采用的素材没被改（还是 12）", v1["video_ids"] == [12], v1["video_ids"])
        SLOW["on"] = False
        drain(client, a)
        done_v1 = plan_get(db, tid_g)
        ck("v1 跑完仍是 done 且 summary_id 指向素材 12 的稿",
           done_v1["status"] == "done" and done_v1["summary_id"], done_v1["status"])
        src12 = client.get("/api/content/summaries/by-video/12").get_json()["items"]
        src112 = client.get("/api/content/summaries/by-video/112").get_json()["items"]
        ck("v1 那篇稿回查得到素材 12、回查不到 112/113（证据与产物对得上）",
           any(x["id"] == done_v1["summary_id"] for x in src12)
           and not any(x["id"] == done_v1["summary_id"] for x in src112),
           "12 命中 %d / 112 命中 %d" % (len(src12), len(src112)))
        # 当前版是刚才保存时开出来的 v2（草稿，清单 [112,113]）。先把它跑成 done，
        # 才能测"在已开工的版本上换素材直接生成"这一刀。
        g2 = client.post("/api/plan/task/generate", json=payload(slot="iso1", vids=(112, 113)))
        drain(client, g2.get_json())
        cur2 = plan_get(db, g2.get_json().get("task_id") or 0) if g2.get_json().get("task_id") \
            else plan_tasks.find_task(db, "iso1", "toutiao", "guide")
        ck("v2 草稿跑成 done，用的是保存时那份清单（112+113）",
           cur2["version"] == 2 and cur2["status"] == "done" and cur2["video_ids"] == [112, 113],
           [cur2["version"], cur2["status"], cur2["video_ids"]])
        # done 之后带不同素材、没勾重新生成 → 拒 409，不悄悄改依据
        r = client.post("/api/plan/task/generate", json=payload(slot="iso1", vids=(12,)))
        ck("done 版上换素材直接生成 → 409 stale（要求先保存或显式重生成）",
           r.status_code == 409 and r.get_json().get("mode") == "stale",
           "%s / %s" % (r.status_code, r.get_json().get("mode")))
        ck("409 里把库里实际存着的清单回给人看",
           r.get_json().get("stored_video_ids") == [112, 113],
           r.get_json().get("stored_video_ids"))
        # T1 新规则：**换素材必须先保存再生成**，带着不一致的清单直接重生成也照样拒。
        r = client.post("/api/plan/task/generate",
                        json=payload(slot="iso1", vids=(12,), regenerate=True))
        ck("带着不一致清单显式重生成 → 仍 409 stale（不给静默换证据留后门）",
           r.status_code == 409 and r.get_json().get("mode") == "stale", r.status_code)
        saved = client.post("/api/plan/task", json=payload(slot="iso1", vids=(12,))).get_json()
        ck("先保存换素材 → 开出新版本草稿",
           saved.get("action") == "versioned" and saved["task"]["version"] == 3,
           [saved.get("action"), (saved.get("task") or {}).get("version")])
        r = client.post("/api/plan/task/generate",
                        json=payload(slot="iso1", vids=(12,), regenerate=False))
        j = r.get_json()
        v2 = plan_get(db, j["task_id"])
        ck("在新草稿上生成 → 第 3 版用的是刚保存的清单",
           v2["version"] == 3 and v2["video_ids"] == [12], [v2["version"], v2["video_ids"]])
        drain(client, j)
        v1_again = plan_get(db, tid_g)
        ck("v1 依旧指着素材 12 的稿（历史版本文针不动）",
           v1_again["video_ids"] == [12] and v1_again["summary_id"] == done_v1["summary_id"])

        print("== L R2：多版本并存时服务端标出当前版 ==")
        rows = client.get("/api/plan/tasks?slot_key=iso1").get_json()["tasks"]
        toutiao = [t for t in rows if t["channel"] == "toutiao"]
        ck("toutiao 三版本都在（1/2/3）",
           sorted(t["version"] for t in toutiao) == [1, 2, 3],
           [t["version"] for t in toutiao])
        cur = [t for t in toutiao if t.get("is_current")]
        ck("每组只有一条 is_current 且是 version 最大的",
           len(cur) == 1 and cur[0]["version"] == 3, [(t["version"], t.get("is_current")) for t in toutiao])
        mixed = client.get("/api/plan/tasks?slot_key=gift").get_json()["tasks"]
        ck("混合渠道：同题两档各有一条当前版，互不顶掉",
           len([t for t in mixed if t.get("is_current")]) == 2
           and {t["channel"] for t in mixed if t.get("is_current")} == {"toutiao", "wechat"},
           [(t["channel"], t["version"], t.get("is_current")) for t in mixed])

        print("== M R3：选题要求真进了模型请求（在 openai 客户端边界截获）==")
        import types

        captured = []

        class _Msg(object):
            def __init__(self, c):
                self.content = c

        class _Choice(object):
            def __init__(self, c):
                self.message = _Msg(c)

        class _Resp(object):
            def __init__(self, c):
                self.choices = [_Choice(c)]

        class _Comp(object):
            def create(self, model=None, messages=None, **kw):
                text = "".join(m.get("content", "") for m in (messages or [])
                               if m.get("role") == "user")
                captured.append(text)
                body = {"title": "桩稿", "outline": ["【标题候选】"],
                        "content": "## 小节\n桩正文", "keywords": ["桩"]}
                return _Resp(json.dumps(body, ensure_ascii=False))

        class _Chat(object):
            completions = _Comp()

        class _OpenAI(object):
            def __init__(self, *a2, **k2):
                self.chat = _Chat()

        mod = types.ModuleType("openai")
        mod.OpenAI = _OpenAI
        sys.modules["openai"] = mod
        cs.generate_summary = REAL["one"]                     # 还原真函数，只假传输层
        cs.generate_collection_summary = REAL["many"]
        app._fast_config = lambda: {"method": "openai", "api_key": "stub",
                                    "model": "stub-model", "api_base": "http://127.0.0.1:9/v1"}
        app._video_keyframe_markdown = lambda vs: ""
        for slot, hook in (("brief1", "必须答的是治疗视角够不够"),
                           ("brief2", "必须给一张能照着勾的清单")):
            # 同一素材、同一渠道、同一形式，**只有 hook 不同**——这才叫"角度参与生成"
            rr = client.post("/api/plan/task/generate",
                             json=payload(slot=slot, vids=(12,), title="同一素材同一渠道",
                                          hook=hook))
            drain(client, rr.get_json())
        ck("两次生成各发了一次真实请求构造", len(captured) == 2, len(captured))
        ck("两个 hook 产出的提示词不一样（角度真参与生成）",
           len(captured) == 2 and captured[0] != captured[1])
        ck("提示词里能读到本次的 hook 原文",
           len(captured) == 2 and all(h in c for h, c in zip(
               ("必须答的是治疗视角够不够", "必须给一张能照着勾的清单"), captured)))
        ck("提示词写明这份要求只用于取舍与角度、不是事实来源",
           len(captured) == 2 and all("不是事实来源" in c for c in captured))
        # 旧素材库那条路不带 brief：提示词里不该出现"本篇的选题要求"
        captured[:] = []
        st0 = {"running": False, "progress": "", "done": False, "error": "", "success": 0,
               "summary_ids": []}
        app._run_generation(["E2E:9001"], "game_guide", False, st0)
        ck("旧路由不带 brief 时提示词里没有选题要求段（兼容）",
           captured and "本篇的选题要求" not in captured[0],
           (captured[0][:40].replace("\n", " ") if captured else "没捕获"))
        # 恢复成"计数桩"：后面几段只关心调了几次，不关心提示词
        def fake_one2(video, summary_type, config, brief=None):
            CALLS.append((summary_type, [int(video["id"])]))
            _gate()
            return ({"title": "稿·%s·%s" % (summary_type, video["id"]),
                     "content": "## 小标题\n正文", "outline": "", "keywords": "",
                     "structured_data": ""}, "stub-model", "draft")

        def fake_many2(videos, summary_type, config, brief=None):
            CALLS.append((summary_type, [int(v["id"]) for v in videos]))
            _gate()
            return ({"title": "合并稿·%s" % summary_type, "content": "## 小标题\n合并正文",
                     "outline": "", "keywords": "", "structured_data": ""}, "stub-model", "draft")

        cs.generate_summary = fake_one2
        cs.generate_collection_summary = fake_many2

        print("== N R5：进程中断后不永久卡 running ==")
        con2 = sqlite3.connect(db)
        con2.execute("INSERT INTO plan_tasks (slot_key,channel,form,title,video_ids,sources,version,"
                     "status,error,created_at,updated_at) VALUES ('crash1','toutiao','guide','中断测试',"
                     "'[12]','[]',1,'running','','2026-10-20 00:00:00','2026-10-20 00:00:00')")
        con2.commit(); con2.close()
        plan_tasks._READY.clear()          # 模拟新进程：这个库在本进程里第一次被用
        plan_tasks.ensure_table(db)
        ck("重启核对把残留 running 标成 aborted",
           plan_tasks.find_task(db, "crash1", "toutiao", "guide")["status"] == "aborted")
        ck("aborted 的文案让人先核查产物再决定重试（不自动重付费）",
           "先回素材库看" in (plan_tasks.find_task(db, "crash1", "toutiao", "guide")["error"] or ""))
        r = client.post("/api/plan/task/generate", json=payload(slot="crash1", vids=(12,)))
        ck("aborted 之后允许人工显式重试", r.get_json().get("mode") == "started",
           r.get_json().get("mode"))
        drain(client, r.get_json())

        print("== O 旧 /api/content/generate 的兼容回归（抽取共用函数之后）==")
        del CALLS[:]
        r = client.post("/api/content/generate",
                        json={"videos": ["E2E:9001"], "summary_type": "game_guide"})
        j = r.get_json()
        ck("单条：老请求形状照旧接受", r.status_code == 200 and j.get("status") == "started", j)
        for _ in range(60):
            st = client.get("/api/status").get_json()
            if st.get("done"):
                break
            time.sleep(0.2)
        ck("单条：跑完 done 且 running 复位", st.get("done") and not st.get("running"), st.get("progress"))
        ck("单条：落了一篇稿", st.get("success") == 1 and len(st.get("summary_ids") or []) == 1,
           st.get("summary_ids"))
        r = client.post("/api/content/generate",
                        json={"videos": ["E2E:9001", "E2E:9002"], "summary_type": "game_guide"})
        for _ in range(60):
            st = client.get("/api/status").get_json()
            if st.get("done"):
                break
            time.sleep(0.2)
        ck("批量（不合并）：两条出两篇", st.get("success") == 2, st.get("summary_ids"))
        r = client.post("/api/content/generate",
                        json={"videos": ["E2E:9001", "E2E:9002"], "summary_type": "toutiao_mix",
                              "combine": True})
        for _ in range(60):
            st = client.get("/api/status").get_json()
            if st.get("done"):
                break
            time.sleep(0.2)
        sid = (st.get("summary_ids") or [None])[0]
        con2 = sqlite3.connect(db)
        sv = con2.execute("SELECT source_video_ids FROM ai_summaries WHERE id=?", (sid,)).fetchone()
        con2.close()
        ck("合并：一条稿带两条来源（老行为不变）",
           st.get("success") == 1 and sv and len(json.loads(sv[0])) == 2,
           sv and sv[0])
        cs.generate_summary = lambda *a3, **k3: (_ for _ in ()).throw(RuntimeError("stub 炸"))
        cs.generate_collection_summary = lambda *a3, **k3: (_ for _ in ()).throw(RuntimeError("stub 炸"))
        r = client.post("/api/content/generate",
                        json={"videos": ["E2E:9001"], "summary_type": "game_guide"})
        for _ in range(60):
            st = client.get("/api/status").get_json()
            if st.get("done"):
                break
            time.sleep(0.2)
        ck("异常：老路由把 traceback 记进 error 且 running 一定复位",
           bool(st.get("error")) and not st.get("running"),
           str(st.get("error"))[:50])
        cs.generate_summary = fake_one2
        cs.generate_collection_summary = fake_many2
        r = client.post("/api/content/generate",
                        json={"videos": ["E2E:9001"], "summary_type": "nope"})
        ck("非法 summary_type 仍 400（白名单没被抽取弄松）", r.status_code == 400, r.status_code)

        print("== P 并发与快速完成后的重复请求 ==")
        del CALLS[:]
        out, bodies = [], []

        cur_v = plan_tasks.find_task(db, "iso1", "toutiao", "guide")["version"]
        def shoot_regen():
            # 与真实界面一致：重生成带"我点的是哪一版"（ptGenerate 里就是 _pt.task.version）。
            # 不带这个字段时三次点击各自开一版是**正确**的（那是三次独立意图），
            # 号主双击/重放的是同一个 base_version，才是要被合并成一次的那类。
            rr = client.post("/api/plan/task/generate",
                             json=payload(slot="iso1", vids=(12,), regenerate=True,
                                          base_version=cur_v, base_task_id=None))
            out.append(rr.status_code)
            bodies.append(rr.get_json())

        th = [threading.Thread(target=shoot_regen) for _ in range(3)]
        [t.start() for t in th]; [t.join() for t in th]
        ck("三个并发重生成：只有一个起任务，其余 409，**没有 500**",
           out.count(200) == 1 and 409 in out and 500 not in out, out)
        for b in bodies:
            drain(client, b)
        ck("并发重生成只调了一次生成", len(CALLS) == 1, CALLS)
        vers = [t["version"] for t in client.get("/api/plan/tasks?slot_key=iso1").get_json()["tasks"]
                if t["channel"] == "toutiao"]
        ck("同一个 base_version 的重放只开一个新版本（不各开一版）",
           len(vers) == len(set(vers)) and max(vers) == cur_v + 1,
           "点前 v%s 之后 %s" % (cur_v, sorted(vers)))
        # 快速完成后立刻再点（不是 running 中）→ continue，不重复花钱
        del CALLS[:]
        r1 = client.post("/api/plan/task/generate", json=payload(slot="day2", vids=(12,)))
        drain(client, r1.get_json())
        n1 = len(CALLS)
        r2 = client.post("/api/plan/task/generate", json=payload(slot="day2", vids=(12,)))
        ck("快速跑完后立刻再点 → continue 且没有多调一次",
           r2.get_json().get("mode") == "continue" and len(CALLS) == n1,
           "%s / %d→%d" % (r2.get_json().get("mode"), n1, len(CALLS)))


        print("== Q 选题雷达已下线（2026-10-20 号主点头），共享配置与图标不能跟着掉 ==")
        r = client.get("/api/topics/radar")
        ck("雷达接口 404（不是返回空数据糊过去）", r.status_code == 404, r.status_code)
        r = client.get("/api/strategy/channels")
        j = r.get_json()
        ck("渠道策略下发口仍在且三渠道齐全（名字带 RADAR 不等于只服务雷达页）",
           r.status_code == 200 and {"toutiao", "wechat", "xhs"} <= set(j.get("channels") or {}),
           sorted((j.get("channels") or {}).keys()))
        ck("content_store 里雷达聚合没了、策略常量留着",
           not hasattr(cs, "get_topic_radar") and not hasattr(cs, "_radar_kw_hit")
           and bool(getattr(cs, "RADAR_CHANNEL_STRATEGY", None)))
        html = (ROOT / "web" / "templates" / "index.html").read_text(encoding="utf-8")
        ck("导航项与整页都删了，旧 #radar 有去处",
           'id="nav-radar"' not in html and 'id="page-radar"' not in html
           and "if (h === 'radar') return 'schedule'" in html
           and "'radar'" not in html.split("const PAGES = [")[1].split("]")[0])
        ck("前端不再有雷达接口与雷达渲染函数",
           "loadRadar" not in html and "topics/radar" not in html)
        # 这一条是本轮自己踩出来的：删雷达图标时把 9 个还在用的图标一起带走了，
        # node --check 查不出来（语法没问题），只有真打开页面才会看到一片空白图标。
        icons = __import__("re").findall(r"^  ([a-z0-9]+):'<", html, __import__("re").M)
        ck("图标表只少了 radar，其余一个没掉",
           "radar" not in icons and len(icons) == 22,
           "现 %d 个：%s" % (len(icons), " ".join(sorted(icons))[:70]))

        print("== R 最小验证补的四条：save 与 claim 交错 / 两条里一条失效 / 行号漂移 / token 围栏 ==")
        # R-1 交错：A 读完 pending 停在占锁之前，B 在这段空档把采用清单就地改掉。
        # A 恢复后必须按**锁住的那一行**生成——拿自己手上那份旧快照就会"记 112、用 12 生成"。
        # 用 Event 造确定性交错，不靠 sleep 碰运气；挂点是 claim_running（与 Codex 反例同一处）。
        del CALLS[:]
        client.post("/api/plan/task", json=payload(slot="save-claim", vids=(12,)))
        orig_claim = app.plan_tasks.claim_running
        at_claim, go, box = threading.Event(), threading.Event(), {}

        def hooked(db_path, task_id):
            if threading.current_thread().name == "r-A":
                at_claim.set()
                go.wait(10)
            return orig_claim(db_path, task_id)

        def a_request():
            with app.app.test_client() as other:
                box["a"] = other.post("/api/plan/task/generate",
                                      json=payload(slot="save-claim", vids=(12,))).get_json()

        app.plan_tasks.claim_running = hooked
        th = threading.Thread(target=a_request, name="r-A")
        try:
            th.start()
            if not at_claim.wait(10):
                raise RuntimeError("没停在占锁边界；占锁入口改了要同步改这里")
            box["b"] = client.post("/api/plan/task",
                                   json=payload(slot="save-claim", vids=(112,))).get_json()
            go.set()
            th.join(20)
            if th.is_alive():
                raise RuntimeError("请求 A 没收尾")
        finally:
            go.set()
            th.join(20)
            app.plan_tasks.claim_running = orig_claim
        ja, jb = box.get("a") or {}, box.get("b") or {}
        done_a = drain(client, ja)
        ta = done_a.get("task") or {}
        ck("B 在 pending 上就地改（不许多开一个版本）",
           jb.get("action") == "updated" and (jb.get("task") or {}).get("version") == 1,
           "%s / v%s" % (jb.get("action"), (jb.get("task") or {}).get("version")))
        ck("A 生成用的是被改过之后库里那一版（112），不是手上旧快照",
           len(CALLS) == 1 and CALLS[0][1] == [112], CALLS)
        ck("任务记的与稿子用的同源（都 112）",
           ta.get("video_ids") == [112]
           and json.loads((done_a.get("summary") or {}).get("source_video_ids") or "null") == [112],
           {"task": ta.get("video_ids"),
            "summary": (done_a.get("summary") or {}).get("source_video_ids")})

        # R-2 采用两条、其中一条没有正文：不许把缺的那条悄悄吞了，
        # 也不许"记两条、只用一条"——存下来的就得是能用的那一条。
        del CALLS[:]
        r = client.post("/api/plan/task/generate", json=payload(slot="one-dead", vids=(112, 120)))
        j = r.get_json()
        ck("一条没正文仍照跑能用那条，并把缺的那条报回界面",
           j.get("mode") == "started" and j.get("total") == 1 and j.get("combine") is False
           and any(p.get("code") == "no_transcript" for p in (j.get("problems") or [])),
           {"mode": j.get("mode"), "total": j.get("total"), "combine": j.get("combine"),
            "problems": [p.get("code") for p in (j.get("problems") or [])]})
        d = wait_done(client, j["task_id"])
        t = d.get("task") or {}
        ck("存下来的清单只有能用的那条（不是请求带的两条）", t.get("video_ids") == [112],
           t.get("video_ids"))
        ck("只调一次生成、只带 112", len(CALLS) == 1 and CALLS[0][1] == [112], CALLS)
        ck("稿子来源 = 任务清单 = 生成依据",
           json.loads((d.get("summary") or {}).get("source_video_ids") or "null") == [112],
           (d.get("summary") or {}).get("source_video_ids"))

        # R-3 行号漂移：素材行以后被改了 source_sheet/source_row（采集侧会重排行号），
        # 生成必须还认得当初钉下的那条 DB 主键，不能按 sheet:row 绕回另一条记录。
        del CALLS[:]
        client.post("/api/plan/task", json=payload(slot="row-drift", vids=(112,)))
        con = sqlite3.connect(db)
        con.execute("UPDATE videos SET source_sheet='E2E-MOVED', source_row=9999 WHERE id=112")
        con.commit()
        con.close()
        r = client.post("/api/plan/task/generate", json=payload(slot="row-drift", vids=(112,)))
        j = r.get_json()
        d = drain(client, j)
        t = d.get("task") or {}
        ck("行号被改后仍按 DB 主键取到那一条",
           len(CALLS) == 1 and CALLS[0][1] == [112] and t.get("video_ids") == [112],
           {"calls": CALLS, "task": t.get("video_ids")})
        snap = (t.get("sources") or [{}])[0]
        ck("证据快照钉的是当时看到的行号，不被事后改写",
           snap.get("db_id") == 112 and snap.get("sheet") == "E2E" and snap.get("row") == 9002,
           snap)

        # R-4 finish 的 token 围栏（T2 的后半）：一次尝试只有一个 token，
        # 迟到的旧 worker 跑完时必须**盖不掉**新一次的好结果。光靠"按钮 disabled"挡不住这个。
        del CALLS[:]
        ts = client.post("/api/plan/task",
                         json=payload(slot="token-fence", vids=(12,))).get_json()["task"]
        tid = int(ts["id"])
        tk1 = plan_tasks.claim_running(db, tid)
        ag = plan_tasks.get_task(db, tid)
        ck("占锁把本次 attempt 记数、token 落到行上",
           bool(tk1) and ag["attempt"] == 1 and ag["token"] == tk1,
           {"attempt": ag["attempt"], "same_token": ag["token"] == tk1})
        ck("running 中第二次占锁被拒（同题不许并发烧两次额度）",
           plan_tasks.claim_running(db, tid) is None)
        con = sqlite3.connect(db)
        con.execute("UPDATE plan_tasks SET status='failed', summary_id=NULL, token=''"
                    " WHERE id=?", (tid,))
        con.commit()
        con.close()
        tk2 = plan_tasks.claim_running(db, tid)
        ck("失败后人工重试算第 2 次尝试、换一个新 token",
           bool(tk2) and tk2 != tk1 and plan_tasks.get_task(db, tid)["attempt"] == 2,
           plan_tasks.get_task(db, tid)["attempt"])
        good = plan_tasks.finish(db, tid, "done", summary_id=12345, model="stub", token=tk2)
        late = plan_tasks.finish(db, tid, "failed", error="旧 worker 迟到的失败",
                                 model="stub", token=tk1)
        back = plan_tasks.get_task(db, tid)
        ck("迟到的旧 token 盖不掉新一次的结果",
           good and not late and back["status"] == "done" and back["summary_id"] == 12345
           and "迟到的失败" not in (back["error"] or ""),
           {"good": good, "late": late, "status": back["status"], "sid": back["summary_id"]})
        ck("不带 token 时不设围栏（老调用形状行为不变）",
           plan_tasks.finish(db, tid, "done", summary_id=12345) is True)

        print()
        if FAILS:
            print("FAIL %d 项：%s" % (len(FAILS), "、".join(FAILS)))
            return 1
        print("ALL_OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
