#!/usr/bin/env python3
"""真帧攻略页 · 帧来源合并的离线回归（不花钱、不动生产库）。

抓的是 2026-10-22 真实试用那次暴露的洞：整合稿的 `aweme_id` 是**主素材**那一条，
而主视频常常抽完 ASR 就把原片删了 → 帧池是空的。旧代码只看主素材，于是同一篇里
另一条明明有 18 张帧也没人去看，提示词里整段「铁律六·攻略页」不出现，
模型只能给纯卡片，出图还花 1.0 元画了两张插画，玩家一行游戏画面都没看到。

跑法：cd 项目根 && env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_guide_frames.py
末行必须 ALL_OK。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

FAIL = []
CALLS = []


def ck(name, cond, extra=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, (" — " + str(extra)[:200]) if extra else ""))
    if not cond:
        FAIL.append(name)


VIDS = (990001, 990002, 990003)
AWE = {990001: "9900000000000000001", 990002: "9900000000000000002", 990003: "9900000000000000003"}


def build_db(path):
    """用正式建库逻辑创建三素材合集夹具，无需运行库。"""
    from src import content_store

    con = content_store.connect(path)
    content_store.init_db(con)
    for vid in VIDS:
        con.execute("INSERT OR REPLACE INTO videos (id, source_sheet, source_row, aweme_id, title,"
                    " transcript, status, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (vid, "抖音视频数据", 95000 + vid, AWE[vid], "E2E 素材 %d" % vid, "正文" * 50,
                     "已完成", "2026-10-22 10:00:00"))
    cur = con.execute("INSERT INTO ai_summaries (video_id, summary_type, title, content, status,"
                      " source_video_ids, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (VIDS[0], "toutiao_mix", "E2E 合集稿", "正文" * 80, "draft",
                       json.dumps(list(VIDS)),
                       "2026-10-22 10:00:00", "2026-10-22 10:00:00"))
    con.commit()
    sid = cur.lastrowid
    con.close()
    return sid


def fake_pool(specs):
    """造一个假 frame_pool：specs = {aweme_id: (总帧数, 判过几张, 其中几张能用, 是否已标判过)}。

    判帧那一步要花钱，所以 `ensure_judged` 做成"只记账、不改真模型"——
    测的是合并、编号连续与"不该花的钱没花"，不是模型准不准。
    """
    import types

    mod = types.ModuleType("frame_pool")

    def _meta(aweme_id, judged):
        spec = specs.get(str(aweme_id))
        if not spec:
            return {}
        total, n_judged, usable_n, already = (list(spec) + [False])[:4]
        frames = []
        for i in range(1, total + 1):
            it = {"i": i, "ts": "00:%02d" % i, "file": "raw/v_f_%03d.jpg" % i,
                  "subject": "画面%d" % i, "near_text": "口播%d" % i}
            if judged and i <= n_judged:
                it["checked"] = True
                it["usable"] = i <= usable_n
                if i <= usable_n:
                    it["clean"] = "clean/v_f_%03d.jpg" % i
            frames.append(it)
        return {"aweme_id": str(aweme_id), "duration": 120,
                "checked": bool(judged and (already or n_judged == total)), "frames": frames}

    def load(aweme_id):
        spec = specs.get(str(aweme_id))
        if not spec:
            return {}
        _total, _n, _u, already = (list(spec) + [False])[:4]
        return _meta(aweme_id, judged=bool(already))

    def usable(meta):
        return [f for f in ((meta or {}).get("frames") or []) if f.get("usable")]

    def dropped(meta):
        return {}

    def ensure_judged(aweme_id, cfg, limit=8, progress_cb=None):
        CALLS.append(("ensure_judged", str(aweme_id), limit))
        return _meta(aweme_id, judged=True)

    def index_text(items):
        return "\n".join("F%d｜%s｜%s" % (it["i"], it["ts"], it["subject"]) for it in items)

    def sharpness(path):
        return 3.0 if "soft" in str(path) else 99.0

    mod.load, mod.usable, mod.dropped = load, usable, dropped
    mod.ensure_judged, mod.index_text = ensure_judged, index_text
    mod.sharpness, mod.HARDEN_MIN_SHARP = sharpness, 14.0
    mod.caption_for = lambda it: (it or {}).get("subject") or ""
    return mod


def set_pool(pool):
    import src
    sys.modules["src.frame_pool"] = pool
    src.frame_pool = pool


def main():
    tmp = Path(tempfile.mkdtemp(prefix="guide_frames_"))
    db = str(tmp / "t.db")
    sid = build_db(db)
    src_frame_pool = sys.modules.get("src.frame_pool") or sys.modules.get("frame_pool")
    A, B, C = AWE[990001], AWE[990002], AWE[990003]
    try:
        import app
        import content_store
        from src import ai_poster
        app.DB_PATH = Path(db)
        app.OUTPUT_DIR = tmp / "output"
        app._sync_content_db = lambda: None

        summary = content_store.get_summary(db, sid)
        cfg = {"model": "stub"}

        print("== A 采用素材清单：主素材排最前，一条都不能漏 ==")
        mats = app._guide_materials(summary)
        ck("三条采用素材全在，且主素材在第一", [m["video_id"] for m in mats] == list(VIDS),
           [m["video_id"] for m in mats])
        ck("每条带自己的 aweme_id（没这个就找不到帧池）",
           all(m["aweme_id"] for m in mats), [m["aweme_id"] for m in mats])

        print("== B 帧来源扩到全部采用素材（旧行为：只看主素材＝0 张） ==")
        set_pool(fake_pool({B: (18, 6, 4), C: (10, 5, 3)}))
        CALLS[:] = []
        frames = app._guide_frames_for(summary, cfg)
        ck("主素材没帧池也能拿到别的素材的帧（这次修的正是这里）", len(frames) == 7, len(frames))
        ck("编号全局连续 F1…F7（模型只许点这个编号）",
           [f["i"] for f in frames] == list(range(1, len(frames) + 1)), [f["i"] for f in frames])
        ck("每张留着自己是哪条素材的（画面使用依据要能回读）",
           all(f.get("aweme_id") and f.get("video_id") for f in frames),
           [(f["video_id"], f["i"]) for f in frames][:3])
        ck("来自两条素材各有其数",
           [f["video_id"] for f in frames].count(990002) == 4
           and [f["video_id"] for f in frames].count(990003) == 3,
           [f["video_id"] for f in frames])
        ck("没帧池那条不去调判帧（判帧花钱）",
           [c[1] for c in CALLS] == [B, C], CALLS)
        ck("判帧张数受每条上限约束", all(c[2] == app.GUIDE_JUDGE_CAP for c in CALLS), CALLS)
        ck("真帧档跑通了：有可用帧时铁律六进得了提示词（旧代码在这里是空串）",
           "铁律六" in ai_poster.guide_rules_text(frames, 200))

        print("== B2 已经判过的素材不许再花一遍钱 ==")
        set_pool(fake_pool({B: (18, 6, 4, True), C: (10, 5, 3, True)}))
        CALLS[:] = []
        again = app._guide_frames_for(summary, cfg)
        ck("判过就用，不再调 ensure_judged", CALLS == [], CALLS)
        ck("第二次拿到的帧数与第一次一致", len(again) == len(frames), (len(again), len(frames)))

        print("== C 整组上限：帧再多也不整包塞进提示词 ==")
        set_pool(fake_pool({B: (30, 12, 12), C: (30, 12, 12)}))
        frames2 = app._guide_frames_for(summary, cfg)
        ck("合并后不超过 GUIDE_FRAME_TOTAL_CAP",
           len(frames2) <= app.GUIDE_FRAME_TOTAL_CAP, len(frames2))
        ck("每条素材各按自己的上限给（整组再被总上限截）",
           len(frames2) == min(app.GUIDE_FRAME_TOTAL_CAP, app.GUIDE_JUDGE_CAP * 2), len(frames2))
        ck("一页一张的前提下，帧清单要够 6 页用",
           app.GUIDE_JUDGE_CAP >= 6 or app.GUIDE_FRAME_TOTAL_CAP >= 6,
           (app.GUIDE_JUDGE_CAP, app.GUIDE_FRAME_TOTAL_CAP))

        print("== D 一条都没有帧时老实返回空（不许假装能用） ==")
        set_pool(fake_pool({}))
        frames3 = app._guide_frames_for(summary, cfg)
        ck("没有帧池就是空列表，不抛错", frames3 == [], frames3)

        print("== E 提示词：有帧才出现「铁律六」，且带页型轮换配方 ==")
        rules = ai_poster.guide_rules_text(frames2, 200)
        ck("有帧时「铁律六」在", "铁律六" in rules)
        ck("攻略零件按内容选，不能强制对照或互动",
           "不强制最后一页互动" in rules and "页与页不许同构" not in rules)
        ck("没有帧时整段不出现（不占预算也不误导模型）",
           ai_poster.guide_rules_text([], 200) == "")

        print("== F 页数上限放开到 6（信息多就摊页） ==")
        ck("PAGE_MAX ≥ 6：3-4 页不再是天花板", ai_poster.PAGE_MAX >= 6, ai_poster.PAGE_MAX)
        lim = ai_poster.limits_for(ai_poster.DEFAULT_TEXT_MODE)
        cards = [{"t": "t%d" % i, "d": "d"} for i in range(12)]
        two = [{"title": "A", "cards": cards[:6]}, {"title": "B", "cards": cards[6:]}]
        got, _note = ai_poster._resplit(two, 4, lim)
        ck("用户定 4 页时真摊成 4 页、要点一条不丢",
           len(got) == 4 and sum(len(p.get("cards") or []) for p in got) == 12,
           (len(got), sum(len(p.get("cards") or []) for p in got)))
        got6, _n6 = ai_poster._resplit(two, 6, lim)
        ck("上限放开到 6 页（以前定 4 是因为底图按页收费，攻略档画面用真帧不加钱）",
           len(got6) == 6, len(got6))

        print("== H 同一场景的两张帧不许同时上版（预演样张目视抓到的） ==")
        pool = [{"i": 1, "ts": "00:03", "file": "/x/a.jpg", "subject": "石廊"},
                {"i": 2, "ts": "00:06", "file": "/x/b.jpg", "subject": "石廊"},
                {"i": 3, "ts": "00:24", "file": "/x/c.jpg", "subject": "职业界面"},
                {"i": 4, "ts": "00:12", "file": "/x/d.jpg", "subject": "石廊"}]
        import types as _t
        fp_stub = _t.ModuleType("frame_pool")
        fp_stub.caption_for = lambda it: it.get("subject") or ""
        fp_stub.index_text = lambda items: "\n".join(
            "F%d｜%s｜%s" % (it["i"], it.get("ts") or "", it.get("subject") or "") for it in items)
        real_fp = sys.modules.get("src.frame_pool")
        set_pool(fp_stub)
        try:
            same = [{"title": "A", "photos": [{"frame": "F1"}, {"frame": "F2"}]},
                    {"title": "B", "photos": [{"frame": "F3"}]}]
            nt = ai_poster.bind_frames(same, pool)
            ck("一页超出 1 张时保留原选择供容量门禁阻断",
               len(same[0]["photos"]) == 2
               and any("图片 2 张" in x for x in ai_poster.lint_plan(dict(same[0], cards=[]))),
               same[0]["photos"])
            ck("超量图片提示拆页",
               any("超过一页 1 张上限" in s for s in nt), nt)
            ck("提示词里写的也是 1 张/页（规则与代码同源，不许一处 2 一处 1）",
               "一页只给 1 张" in ai_poster.guide_rules_text(pool, 200)
               and "最多 2 张" not in ai_poster.guide_rules_text(pool, 200))
            across = [{"title": "A", "photos": [{"frame": "F1"}]},
                      {"title": "B", "photos": [{"frame": "F4"}]}]
            nt2 = ai_poster.bind_frames(across, pool)
            ck("相近时刻不同操作帧可以保留",
               any(ph.get("frame") == "F4" for ph in (across[1].get("photos") or [])),
               [ph.get("frame") for ph in (across[1].get("photos") or [])])
            ck("不按时间距离自动换成另一张",
               [ph.get("frame") for ph in (across[1].get("photos") or [])] == ["F4"],
               [ph.get("frame") for ph in (across[1].get("photos") or [])])
            ck("相近时刻不误报同一画面", not any("同一个画面" in s for s in nt2), nt2)
            far = [{"title": "A", "photos": [{"frame": "F1"}]},
                   {"title": "B", "photos": [{"frame": "F3"}]}]
            ai_poster.bind_frames(far, pool)
            ck("隔 21 秒的两张照留（第 1 页 F1、第 2 页 F3）",
               len(far[0]["photos"]) == 1 and len(far[1]["photos"]) == 1)
            manual = [{"title": "A", "photos": [{"path": "/y/me.jpg", "caption": "我选的"}]}]
            nt3 = ai_poster.bind_frames(manual, pool)
            ck("手工选的图不受这个闸（它没有编号）",
               len(manual[0]["photos"]) == 1 and manual[0]["photos"][0].get("judged") is False,
               nt3)
            # 逐页填那条路：不告诉模型"哪些编号已被别的页占了"，它就会挑同一场景里挨着的那张，
            # 后端 12 秒闸一挡 → 这一页变成没图（2026-10-22 线上第 3 页就是这么空的）
            grp = [{"title": "P1", "photos": [{"frame": "F1", "ts": "00:03"}]},
                   {"title": "P2", "photos": []}]
            with_used = ai_poster.page_plan_prompt(2, grp, "主线", "guide_ink", False, "主题",
                                                   lim, "正文", pool)
            no_used = ai_poster.page_plan_prompt(1, grp, "主线", "guide_ink", False, "主题",
                                                 lim, "正文", pool)
            ck("逐页填时提示词点名「哪些编号已被别的页用掉」",
               "已经被别的页用掉了" in with_used and "F1(00:03)" in with_used)
            ck("这一页自己就是用了图的那一页时不该重复点名",
               "已经被别的页用掉了" not in no_used)
            ck("整组那条规则也写进铁律六（一个编号只用一次、跨页也要拉开）",
               "只许用一次" in ai_poster.guide_rules_text(pool, 200))
        finally:
            set_pool(real_fp)

        print("== I 出图前那道核对：虚的别印、后期字裁干净（号主 10-22 看图点名） ==")
        from PIL import Image, ImageDraw
        import frame_pool as fp_mod
        tmpimg = tmp / "imgs"
        tmpimg.mkdir(parents=True, exist_ok=True)

        def mk(name, sharp):
            im = Image.new("RGB", (900, 600), (30, 40, 35))
            d = ImageDraw.Draw(im)
            if sharp:
                for x in range(0, 900, 6):
                    d.line([(x, 0), (x + 3, 600)], fill=(250, 250, 250), width=1)
            else:
                for x in range(0, 900, 40):
                    d.line([(x, 0), (x + 20, 600)], fill=(60, 70, 65), width=22)
            path = tmpimg / (name + ".jpg")
            im.save(str(path), quality=95)
            return str(path)

        sharp_src = mk("sharp", True)
        soft_src = mk("soft", False)
        ck("度量本身分得开：清楚的 > 阈值 > 虚的",
           fp_mod.sharpness(sharp_src) >= fp_mod.HARDEN_MIN_SHARP > fp_mod.sharpness(soft_src),
           (round(fp_mod.sharpness(sharp_src), 1), round(fp_mod.sharpness(soft_src), 1)))
        ASKED = []

        def stub(question, answers):
            def f(path, q, cfg, *a, **k):
                ASKED.append(q[:12])
                return answers[len(ASKED) - 1], 10
            return f

        real_ask = fp_mod._ask_vision
        try:
            # 1) 虚的帧：一次模型都不该问（省那份钱），直接不许上版
            fp_mod._ask_vision = lambda path, q, cfg, *a, **k: ({}  , 0)
            ASKED[:] = []
            out1 = tmp / "w1"
            _p, r1 = fp_mod.harden_frame(soft_src, {"api_key": "x"}, out1)
            ck("虚帧不花钱问模型，直接判不许上版", r1.get("too_soft") and ASKED == [], (r1, ASKED))
            ck("为什么不上版要说人话（换清晰的或自己传一张）",
               "本身是虚的" in (r1.get("why") or ""), r1.get("why"))

            # 2) 底部字幕给了可用区间 → 裁一刀，复核通过
            #    （桩要"第一趟说有、裁完第二趟说没有"，否则等于模拟救不回来的那张）
            def once(yes, no):
                state = {"n": 0}

                def f(path, q, cfg, *a, **k):
                    state["n"] += 1
                    if "最下面" in q:
                        return (yes if state["n"] <= 1 else no), 10
                    if "最上面" in q:
                        return {"has_text": False}, 10
                    return {"box": [0, 0, 100, 100], "subject": "角色"}, 10
                return f
            fp_mod._ask_vision = once({"has_text": True, "band_top": 88, "band_bot": 100},
                                      {"has_text": False})
            p2, r2 = fp_mod.harden_frame(sharp_src, {"api_key": "x"}, tmp / "w2")
            ck("底部字幕裁掉了（成品比原帧矮）",
               Image.open(p2).size[1] < Image.open(sharp_src).size[1], Image.open(p2).size)
            ck("裁的位置带外扩余量，不是照着边界刚好切",
               r2["strip"][0] == 88.0 and r2["strip"][2] == "bottom" and Image.open(p2).size[1] <= 500,
               (r2["strip"], Image.open(p2).size))
            ck("复核过了就 ok", r2.get("ok") and not r2.get("still_text"), r2)

            # 3) 报了有字却圈 0-100 → 走兜底位置，不能整帧作废
            fp_mod._ask_vision = once({"has_text": True, "band_top": 0, "band_bot": 100},
                                      {"has_text": False})
            p3, r3 = fp_mod.harden_frame(sharp_src, {"api_key": "x"}, tmp / "w3")
            ck("模型圈不准时用兜底位置裁，而不是把整帧判死",
               r3.get("ok") and r3["strip"][2] == "bottom", r3.get("strip"))

            # 4) 顶部标题条 → 裁顶部
            def top_only(path, q, cfg, *a, **k):
                top_calls = getattr(top_only, "t", 0)
                if "最下面" in q:
                    return {"has_text": False}, 10
                if "最上面" in q:
                    top_only.t = top_calls + 1
                    return ({"has_text": True, "band_top": 0, "band_bot": 18}
                            if top_calls == 0 else {"has_text": False}), 10
                return {"box": [0, 0, 100, 100]}, 10
            fp_mod._ask_vision = top_only
            _p4, r4 = fp_mod.harden_frame(sharp_src, {"api_key": "x"}, tmp / "w4")
            ck("顶部后期标题条也裁", r4.get("ok") and r4["strip"][2] == "top", r4.get("strip"))

            # 5) 一直报有字 → 裁到上限就不许上版
            fp_mod._ask_vision = lambda path, q, cfg, *a, **k: (
                {"has_text": True, "band_top": 88, "band_bot": 100} if "最下面" in q
                else ({"has_text": False} if "最上面" in q else {"box": [0, 0, 100, 100]}), 10)
            _p5, r5 = fp_mod.harden_frame(sharp_src, {"api_key": "x"}, tmp / "w5")
            ck("裁到上限还剩字 → 这一帧不上版",
               r5.get("still_text") and not r5.get("ok"), r5.get("why"))

            # 6) 模型没答 → 原样用，但要如实写"没核过"
            fp_mod._ask_vision = lambda path, q, cfg, *a, **k: ({}, 0)
            _p6, r6 = fp_mod.harden_frame(sharp_src, {"api_key": "x"}, tmp / "w6")
            ck("没核过不许当成核过了", "没再核" in (r6.get("why") or "") and not r6.get("ok"), r6)

            # 7) 内容框两种口径都收（实测这个模型答像素、那个答百分比）
            W, H = 900, 600
            ck("像素口径能用", (fp_mod._box_of({"box": [100, 100, 880, 560]}, W, H) or [0, 0])[1] > 40,
               fp_mod._box_of({"box": [100, 100, 880, 560]}, W, H))
            ck("百分比口径能用", fp_mod._box_of({"box": [10, 10, 90, 90]}, W, H) is not None,
               fp_mod._box_of({"box": [10, 10, 90, 90]}, W, H))
            ck("只留 20% 面积算裁太狠 → 不裁", fp_mod._box_of({"box": [40, 40, 60, 60]}, W, H) is None)
            ck("等于没裁（保留 95%）→ 不折腾", fp_mod._box_of({"box": [1, 1, 99, 99]}, W, H) is None)

            # 8) 帧清单里标出清晰/偏虚，拆解层一开始就挑得对
            fp_mod._ask_vision = real_ask
            marked = fp_mod.index_text([{"i": 1, "ts": "00:03", "subject": "A", "file": sharp_src},
                                        {"i": 2, "ts": "00:06", "subject": "B", "file": soft_src}])
            ck("清单里写明哪张偏虚、叫模型别选",
               "清晰" in marked and "偏虚（别选这张）" in marked, marked.replace("\n", " ")[:120])
        finally:
            fp_mod._ask_vision = real_ask

        print("== J 无关画面不补图，允许文字页 ==")
        set_pool(fake_pool({}))
        poolJ = [{"i": 1, "ts": "00:03", "file": "/x/a.jpg", "subject": "大厅"},
                 {"i": 2, "ts": "00:06", "file": "/x/b.jpg", "subject": "大厅"},
                 {"i": 3, "ts": "00:40", "file": "/x/c.jpg", "subject": "副本门口"},
                 {"i": 4, "ts": "00:55", "file": "/x_soft.jpg", "subject": "模糊的一张"},
                 {"i": 5, "ts": "01:20", "file": "/x/e.jpg", "subject": "职业界面"}]
        four = [{"title": "P1", "photos": [{"frame": "F1"}]},
                {"title": "P2"},
                {"title": "P3"},
                {"title": "P4", "photos": [{"path": "/y/me.jpg"}]}]
        ntJ = ai_poster.bind_frames(four, poolJ, lim)
        ck("模型没配图的两页保持文字页",
           not four[1].get("photos") and not four[2].get("photos"),
           [[q.get("frame") for q in (p.get("photos") or [])] for p in four])
        ck("不自动补无关图", not any("已自动补" in x for x in ntJ), ntJ)
        ck("手工传过图的页不再补", four[3]["photos"][0].get("judged") is False)
        exhausted = [{"title": "A", "photos": [{"frame": "F1"}]}, {"title": "B"},
                     {"title": "C"}, {"title": "D"}, {"title": "E"}, {"title": "F"},
                     {"title": "G"}]
        ntJ2 = ai_poster.bind_frames(exhausted, poolJ[:1], lim)
        ck("帧池用完不编图", not exhausted[1].get("photos") and not ntJ2, ntJ2)
        ck("提示词允许无相关画面做文字页",
           "没有相关画面可以做文字页" in ai_poster.guide_rules_text(poolJ, 200))

        print("== G 攻略零件要出现在 JSON 示例里（只在末尾写铁律六，模型不认） ==")
        lim2 = ai_poster.limits_for(ai_poster.DEFAULT_TEXT_MODE)
        one_pg = [{"title": "A", "cards": [{"t": "a", "d": "b"}]}]
        with_fr = ai_poster.page_plan_prompt(1, one_pg, "主线", "guide_ink", False, "主题",
                                             lim2, "正文", frames)
        no_fr = ai_poster.page_plan_prompt(1, one_pg, "主线", "guide_ink", False, "主题",
                                           lim2, "正文", [])
        ck('有帧时示例里就有 "photos": [{"frame": "F1"}]',
           '"photos": [{"frame": "F1"}]' in with_fr)
        ck('没帧时示例逐字是旧形状（默认零变化）',
           '"cards": [{"t": "卡片标题", "d": "卡片说明", "v": "这一条画什么"}]}' in no_fr
           and '"photos"' not in no_fr)
        ck("占位符不许漏进提示词",
           "__GUIDE_SHAPE__" not in with_fr and "__GUIDE_SHAPE__" not in no_fr
           and "__SHAPE__" not in with_fr)
        ck("铁律六与示例同进同出", "铁律六" in with_fr and "铁律六" not in no_fr)
        ck("整组拆解那条路也带上示例（build_text_plan 与单页共用同一份替换）",
           "__GUIDE_SHAPE__" in ai_poster._PLAN_PROMPT
           and "__GUIDE_SHAPE__" in ai_poster._PAGE_PROMPT)

        print("== K 「＋ 用你的图」在攻略页上要真上版（2026-10-23 选项走查：以前被静默丢掉） ==")
        import io as _io
        from PIL import Image as _Im
        from src import poster_typeset as ts
        _buf = _io.BytesIO()
        _Im.new("RGB", (720, 1080), (235, 20, 20)).save(_buf, "PNG")
        red = _buf.getvalue()

        def _red(b):
            """整张图平均色里红压过绿蓝多少：>12 才算那块红真印上去了。"""
            im = _Im.open(_io.BytesIO(b)).convert("RGB").resize((1, 1)).getpixel((0, 0))
            return im[0] - max(im[1], im[2])

        guide_pg = {"kicker": "第 1 期", "title": "宏库这一页就讲一件事",
                    "layout_key": "guide", "style_key": "guide_ink",
                    "cards": [{"t": "先勾这一项", "d": "勾了自动跟随。"}],
                    "photos": [{"path": str(tmp / "这张不存在.jpg"), "frame": "F3",
                                "caption": "任务界面", "ts": "00:12"}]}
        g_no = ts.typeset(None, dict(guide_pg), "9:16")[0]
        g_yes = ts.typeset(None, dict(guide_pg), "9:16", photo=red)[0]
        ck("攻略页传了图 → 红真印在画面位上（不再是一句『这一档不用图』）", _red(g_yes) > 12,
           _red(g_yes))
        ck("没传图时那一格仍然空着（帧文件不在也不许报错、也不许自己编一张）", _red(g_no) <= 12)
        ck("传图与不传图是两种结果（说明这个选项不再是摆设）", g_yes != g_no)
        plain_pg = {"title": "这条坐骑值不值", "subtitle": "三个角度看", "layout_key": "top_art",
                    "cards": [{"t": "外观", "d": "翅膀展开有光效。"}]}
        w0, h0 = 936, 1664
        base_gray = ts.placeholder_base("minimal", w0, h0, 0.6)
        q_yes = ts.typeset(base_gray, dict(plain_pg), "9:16", photo=red)[0]
        ck("插画档那条路没被动坏：传图仍然盖住插画区", _red(q_yes) > 12, _red(q_yes))

        print("== L 模型把数组交成对象时不许整单炸（2026-10-23 那次拆文案白花的钱） ==")
        limL = ai_poster.limits_for(ai_poster.DEFAULT_TEXT_MODE)
        crashed = ""
        one = mapping = ask_i = ask_k = pgL = None
        try:
            one = ai_poster.guide_clip({"contrast": {"t": "已改", "d": "勾了自动跟随"}}, limL)
            mapping = ai_poster.guide_clip(
                {"contrast": {"已改": "勾了自动跟随", "没改": "每次进本要点一次"}}, limL)
            ask_i = ai_poster.guide_clip(
                {"ask": {"q": "你平时怎么开？", "options": {"1": "手动开", "2": "宏自动"}}}, limL)
            ask_k = ai_poster.guide_clip(
                {"ask": {"q": "你平时怎么开？",
                         "options": {"手动开": "每次点一下", "宏自动": "勾上就行"}}}, limL)
            pgL, _nt = ai_poster.normalize_page(
                {"title": "宏库这一页", "cards": {"t": "先勾这一项", "d": "勾了自动跟随"}},
                limL, "guide_ink", False, 1)
        except TypeError as e:
            crashed = "%s（%s）" % (e, type(e).__name__)
        ck("contrast 交成一条对象：不再 TypeError，且洗成一条对照",
           not crashed and len((one or {}).get("contrast") or []) == 1, crashed or one)
        ck("contrast 交成「标签→文字」那种对象：两条都留住、不丢内容",
           len((mapping or {}).get("contrast") or []) == 2, mapping)
        ck("ask.options 交成 {1:…}：取那句人话，不把序号当选项名",
           (ask_i or {}).get("ask", {}).get("options") == ["手动开", "宏自动"], ask_i)
        ck("ask.options 交成 {选项名: 说明}：取键当选项名",
           (ask_k or {}).get("ask", {}).get("options") == ["手动开", "宏自动"], ask_k)
        ck("cards 交成一条对象：normalize_page 不炸且留住那一条",
           len((pgL or {}).get("cards") or []) == 1, (pgL or {}).get("cards"))

        print("== M 出图前核对把某页那张拦掉之后，必须当场换一张（② 有图 ③ 没图那一次） ==")
        import types
        from PIL import Image as _Im2
        pool_dir = tmp / "poolM"
        pool_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for fid in ("F1", "F2", "F3", "F4"):
            fp = str(pool_dir / ("%s.jpg" % fid))
            _Im2.new("RGB", (640, 360), (90, 120, 90)).save(fp, "JPEG", quality=92)
            files[fid] = fp

        def _harden(path, cfg, out_dir, name=None, progress=None):
            fid = {v: k for k, v in files.items()}.get(str(path), "?")
            if fid == "F4":     # 这张就是号主点名的那种：整幅是虚的，裁边救不回来
                return path, {"ok": False, "too_soft": True, "sharp": 4.7,
                              "why": "探针：这张画面本身是虚的"}
            return path, {"ok": True, "sharp": 30.0}

        stub = types.ModuleType("frame_pool_stub")
        stub.harden_frame = _harden
        stub.caption_for = lambda it: "探针图注"
        stub.HARDEN_MIN_SHARP = 14.0
        stub.sharpness = lambda f: 30.0 if str(f).endswith("F3.jpg") else 4.7
        stub.index_text = lambda items, mark_sharp=True: ""
        real_fp = sys.modules.get("src.frame_pool")
        set_pool(stub)
        out_dir_m = tmp / "posterM"
        real_out = ai_poster.OUTPUT_DIR
        try:
            from pathlib import Path as _P
            ai_poster.OUTPUT_DIR = _P(str(out_dir_m))

            def pg(f):
                return {"path": files[f], "frame": f, "judged": True,
                        "caption": "探针图注", "ts": {"F1": "00:05", "F2": "00:20",
                                                     "F3": "01:40", "F4": "00:30"}[f]}
            planM = {"pages": [
                {"role": "封面", "layout_key": "guide", "style_key": "guide_ink",
                 "title": "三系开荒怎么选", "cards": [{"t": "先看这个", "d": "点数够不够。"}],
                 "photos": [pg("F1")]},
                {"role": "中间页", "layout_key": "guide", "style_key": "guide_ink",
                 "title": "核心先记牢", "cards": [{"t": "顺序别错", "d": "先被动后主动。"}],
                 "photos": [pg("F2")]},
                {"role": "末页", "layout_key": "guide", "style_key": "guide_ink",
                 "title": "先想清这几条", "footer": "数据口径：2026-10-22 实测",
                 "cards": [{"t": "别急着点", "d": "洗点要花金币。"}], "photos": [pg("F4")]}],
                "title": "三系开荒怎么选", "style_key": "guide_ink",
                "footer": "数据口径：2026-10-22 实测"}
            poolM = [{"i": 1, "ts": "00:05", "file": files["F1"]},
                     {"i": 2, "ts": "00:20", "file": files["F2"]},
                     {"i": 3, "ts": "01:40", "file": files["F3"]},
                     {"i": 4, "ts": "00:30", "file": files["F4"]}]
            summaryM = {"id": 990501, "video_id": 990501, "aweme_id": "probe", "status": "final",
                        "title": "探针稿", "source_video_ids": "[]",
                        "content": "【整理稿】\n1. 三系点数分配。\n【风险核查】\n（无）",
                        "transcript": "三系点数分配，洗点要花金币"}
            cfgM = {"api_key": "sk-FAKE", "api_base": "http://127.0.0.1:9/v1",
                    "model": "fake", "vision_model": "fake"}
            ai_poster.review_image = lambda png, c: {"score": 4, "full": 5}
            manM = ai_poster.generate_poster(summaryM, cfgM, ratio="9:16", plan=planM,
                                             text_mode="typeset", copies=1, do_review=False,
                                             frame_items=poolM)
            pagesM = manM["plan"]["pages"]
            got3 = [ph.get("frame") for ph in (pagesM[2].get("photos") or [])]
            ck("虚帧被拦后不自动换成不相关画面",
               not got3 and not manM.get("frames_refilled"), got3)
            ck("被拦的画面留核对记录",
               any(h.get("frame") == "F4" for h in manM.get("frames_hardened") or []),
               manM.get("frames_hardened"))
            # 帧池被占完时不许编图，要如实说这一页只排文字
            planM2 = json.loads(json.dumps(planM, default=str))
            for p in planM2["pages"]:
                p["photos"] = []
            manM2 = ai_poster.generate_poster(summaryM, cfgM, ratio="9:16", plan=planM2,
                                              text_mode="typeset", copies=1, do_review=False,
                                              frame_items=[])
            ck("一帧都没有时三页保持文字页",
               all(not (p.get("photos") or []) for p in manM2["plan"]["pages"])
               and not manM2.get("frames_refilled"),
               manM2.get("frames_refilled"))
        finally:
            ai_poster.OUTPUT_DIR = real_out
            if real_fp is not None:
                set_pool(real_fp)
    finally:
        if src_frame_pool is not None:
            set_pool(src_frame_pool)
        sys.modules.pop("frame_pool", None)
        shutil.rmtree(str(tmp), ignore_errors=True)
    if FAIL:
        print("\nFAIL %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("\nALL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
