# -*- coding: utf-8 -*-
"""判帧这一层的反向回归：**没判成**的帧一张都不许进版面。

为什么单独立一条线（2026-10-21 Codex 复核 P0）：`apply_probe` 原来写的是
`if not got.get("post_text")` —— 假值即算"这帧没有后期字"，而 `_probe_item` 里
`_ask_vision(...) or ({}, 0)` 会把「没配 key／openai 没装／请求超时／回来的不是 JSON」
全折成 `{}`。复核实测 `{}`、`{"where":"none"}`、`{"post_text":None}` 三种都判 `clean=True`，
`usable()` 又按 clean 收帧 → **"判不了"被当成"干净"**，带别人字幕条的图就是这么进版面的。
旧门禁（`e2e_poster_layouts.py` Q 段）只测了"模型给了结论"的那些分支，一条没覆盖这个。

四条纪律：
· **不联网、不花钱**：`_ask_vision` 一律换成内存桩；
· **不碰生产**：帧池 `POOL_DIR` 指到 `temp/_frame_judge`，跑完删掉，`output/` 一个字不写；
· 断言走**真链路**（`apply_probe` / `_probe_item` / `ensure_judged` / `usable` / `dropped`），
  不只看单个函数返回值——那次出事就出在两层之间（空结果被上层折成 `{}`）；
· 反向优先：先证明坏输入进不了版面，再证明好输入还能进（别让修闸变成把帧全扔掉）。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/e2e_frame_judge.py
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SANDBOX = ROOT / "temp" / "_frame_judge"

fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)


def fresh_item(path):
    return {"i": 1, "ts": "00:04", "file": str(path), "raw": str(path), "band": None,
            "checked": False, "clean": None, "subject": "", "why": ""}


def main():
    from src import ai_poster
    from src import frame_pool as fp

    if SANDBOX.exists():
        shutil.rmtree(str(SANDBOX))
    SANDBOX.mkdir(parents=True)
    # 一张"像真画面"的测试帧（噪声：亮色占比低，不会被 light_share 误判成作者做的卡）
    from PIL import Image
    scene = SANDBOX / "scene.jpg"
    Image.effect_noise((720, 1280), 90).convert("RGB").save(str(scene), quality=92)

    print("== A 空回答／缺字段／非布尔：一律「没判成」，不许判干净 ==")
    bad_inputs = [
        ("模型返回空对象 {}（复核点名的这一条）", {}),
        ("回答不是 dict（None / 字符串）", None),
        ("只有 where，没有 post_text", {"where": "none"}),
        ("post_text 显式为 None", {"post_text": None, "where": "none"}),
        ("post_text 写成字符串 false", {"post_text": "false", "where": "none"}),
        ("post_text 写成数字 0", {"post_text": 0, "where": "none"}),
        ("post_text 空字符串", {"post_text": "", "where": "none"}),
    ]
    for name, got in bad_inputs:
        it = fresh_item(scene)
        fp.apply_probe(it, got)
        ck("%s → 不许判成干净" % name,
           it.get("clean") is False, "clean=%r" % it.get("clean"))
        ck("%s → 记下「没判成」并留着可重判（checked=False）" % name,
           it.get("judge_failed") is True and it.get("checked") is False,
           "checked=%r judge_failed=%r" % (it.get("checked"), it.get("judge_failed")))
        ck("%s → 原因写给人看，不留空" % name, bool(str(it.get("why") or "").strip()),
           it.get("why") or "")
        ck("%s → 不许偷偷裁图" % name, it.get("crop_to") is None)

    print("== B 有效结论仍然照常工作（修闸不等于把帧全扔掉） ==")
    ok = fresh_item(scene)
    fp.apply_probe(ok, {"post_text": False, "where": "none", "subject": "角色站在城门口"})
    ck("post_text=false → 判干净、可进版面", ok["clean"] is True and ok["judge_failed"] is False
       and ok["checked"] is True and ok["why"] == "", str(ok["why"]))
    ck("判干净的帧 usable() 收（前提：文件真的在）", len(fp.usable({"frames": [ok]})) == 1)
    dirt = fresh_item(scene)
    fp.apply_probe(dirt, {"post_text": True, "where": "middle", "band_top": 40, "band_bot": 62})
    ck("post_text=true 且压在中间 → 不干净、不裁、写原因",
       dirt["clean"] is False and dirt["crop_to"] is None and "中间" in dirt["why"], dirt["why"])
    ck("文件不存在的帧即使标了 clean 也进不了 usable（宁缺毋滥）",
       len(fp.usable({"frames": [dict(ok, file=str(SANDBOX / "ghost.jpg"))]})) == 0)

    print("== C _probe_item 真链路：桩换在最外层问答，覆盖四种失败形状 ==")
    _old_ask = fp._ask_vision
    try:
        def stub_none(img_path, question, ai_config, **kw):
            return None                                     # 没配 key / openai 没装
        fp._ask_vision = stub_none
        it = fresh_item(scene)
        fp._probe_item(it, {"api_key": "sk-test"})
        ck("看图返回 None（没配 key／没装 openai）→ 这帧不许算干净",
           it["clean"] is False and it["judge_failed"] is True, "clean=%r" % it["clean"])
        ck("…也不许算判过（下次重出还会再问一次）", it["checked"] is False)
        ck("…原因写的是「没给出回答」，不是「有后期字」",
           "没给出" in it["why"] and "回答" in it["why"], it["why"])
        ck("…这种帧进不了版面", len(fp.usable({"frames": [it]})) == 0)

        def boom(img_path, question, ai_config, **kw):
            raise RuntimeError("连接超时")
        fp._ask_vision = boom
        it = fresh_item(scene)
        fp._probe_item(it, {"api_key": "sk-test"})
        ck("模型调用抛异常 → 不许沿用上一轮的 clean，也不许判干净",
           it["clean"] is False and it["checked"] is False and "异常" in it["why"], it["why"])
        ck("异常本身也留档（error 字段）", "超时" in (it.get("error") or ""), it.get("error") or "")

        calls = []

        def crop_then_empty(img_path, question, ai_config, **kw):
            calls.append(os.path.basename(str(img_path)))
            if "has_band" in question:
                return {"has_band": True, "band_top": 85, "band_bot": 95}, 31
            if str(img_path).count("v_") >= 1 or os.path.basename(str(img_path)).startswith("v_"):
                return {}, 50                                  # 裁完回读：空回答
            return {"post_text": True, "where": "bottom", "band_top": 0, "band_bot": 0,
                    "post_words": ["预计得打18层"], "subject": "带字幕的画面"}, 99
        fp._ask_vision = crop_then_empty
        it = fresh_item(scene)
        it["file"] = str(SANDBOX / "f_001.jpg")
        shutil.copyfile(str(scene), it["file"])
        it["raw"] = it["file"]
        fp._probe_item(it, {"api_key": "sk-test"})
        ck("裁完回读拿到空回答 → 这张已知有后期字，绝不能因为回读失败就放行",
           it["clean"] is False, "clean=%r why=%s" % (it.get("clean"), it.get("why")))
        ck("…原因写的是「回读没拿到有效判断」（和「仍有后期字」分开，人才知道该重跑）",
           "回读" in it["why"] and "没拿到" in it["why"], it["why"])
        ck("…补问过的那一帧确实裁出了图（说明确实走到回读那一步）",
           len([c for c in calls if c.startswith("v_")]) >= 1, " ".join(calls))

        def crop_then_clean(img_path, question, ai_config, **kw):
            if "has_band" in question:
                return {"has_band": True, "band_top": 85, "band_bot": 95}, 31
            if os.path.basename(str(img_path)).startswith("v_"):
                return {"post_text": False, "where": "none", "subject": "角色属性界面"}, 77
            return {"post_text": True, "where": "bottom", "band_top": 0, "band_bot": 0,
                    "post_words": ["预计得打18层"], "subject": "带字幕的画面"}, 99
        fp._ask_vision = crop_then_clean
        it = fresh_item(scene)
        it["file"] = str(SANDBOX / "f_002.jpg")
        shutil.copyfile(str(scene), it["file"])
        it["raw"] = it["file"]
        fp._probe_item(it, {"api_key": "sk-test"})
        ck("裁完回读结论是 false → 这帧才算干净（正路没被修闸堵死）",
           it["clean"] is True and it["subject"] == "角色属性界面", "clean=%r" % it["clean"])
    finally:
        fp._ask_vision = _old_ask

    print("== D ensure_judged：判失败的帧下次会重问，且不进 usable ==")
    pool = SANDBOX / "dj"
    (pool / "raw").mkdir(parents=True)
    (pool / "clean").mkdir(parents=True)
    frames = []
    for i in (1, 2, 3):
        rp = pool / "raw" / ("f_%03d.jpg" % i)
        shutil.copyfile(str(scene), str(rp))
        frames.append({"i": i, "ts": "00:0%d" % i, "file": str(rp), "raw": str(rp),
                       "band": None, "checked": False, "clean": None, "subject": "", "why": ""})
    json.dump({"aweme_id": "dj", "frames": frames, "usable": 0, "tokens": 0},
              open(str(pool / "frames.json"), "w", encoding="utf-8"), ensure_ascii=False)
    _old_pool = fp.POOL_DIR
    asked = []
    try:
        fp.POOL_DIR = SANDBOX

        def half_fail(img_path, question, ai_config, **kw):
            asked.append(os.path.basename(str(img_path)))
            base = os.path.basename(str(img_path))
            stem = base.split("_")[-1].split(".")[0]
            if base.startswith("v_"):                     # 裁完回读：干净了
                return {"post_text": False, "where": "none", "subject": "副本界面"}, 60
            if "has_band" in question:                    # 只补问数字那一次
                return {"has_band": True, "band_top": 86, "band_bot": 96}, 30
            if stem == "001":
                return {}, 40                              # 空回答 = 没判成
            if stem == "003":
                return {"post_text": True, "where": "bottom", "band_top": 0, "band_bot": 0}, 90
            return {"post_text": False, "where": "none", "subject": "游戏内面板"}, 60
        fp._ask_vision = half_fail
        meta = fp.ensure_judged("dj", {"api_key": "sk-test"}, limit=8)
        ck("一批里混着空回答时：clean 的收进来、没判成的一张都不进 usable",
           len(fp.usable(meta)) == 2, "usable=%s" % meta["usable"])
        by_i = dict((f.get("i"), f) for f in meta["frames"])
        ck("没横幅可裁的干净帧**文件还在**（反向回归抓到的第二条：原来那句无条件 _drop_raw "
           "把刚判干净的图删了，生产池 108 张 clean 里 6 张已成悬空路径）",
           by_i[2]["clean"] is True and os.path.exists(by_i[2]["file"]), by_i[2]["file"])
        ck("…这一帧的 raw 也还留着（它就是成品本身，不是中间产物）",
           bool(by_i[2].get("raw")) and os.path.exists(by_i[2]["raw"]), str(by_i[2].get("raw")))
        ck("裁过的那一帧：成品在 clean/、原帧当中间产物删掉（省盘子那条没被修坏）",
           by_i[3]["clean"] is True and os.path.basename(by_i[3]["file"]).startswith("v_")
           and str(pool / "clean") in by_i[3]["file"] and os.path.exists(by_i[3]["file"])
           and not by_i[3].get("raw") and not (pool / "raw" / "f_003.jpg").exists(),
           by_i[3]["file"])
        ck("frames.json 里的 usable 计数与 usable() 同源（不留两份账）",
           meta["usable"] == len(fp.usable(meta)), str(meta["usable"]))
        failed = [f for f in meta["frames"] if f.get("judge_failed")]
        ck("没判成的那帧标出来（judge_failed）并且 checked=False → 下次重出还会问",
           len(failed) == 1 and failed[0]["checked"] is False, str(len(failed)))
        ck("没判成的帧不删原帧（重判要从没裁过的图上量位置）",
           bool(failed[0].get("raw")) and os.path.exists(failed[0]["raw"]))
        n1 = len(asked)
        meta = fp.ensure_judged("dj", {"api_key": "sk-test"}, limit=8)
        ck("第二次跑只补问那一帧（判过的不重复花钱）",
           len(asked) - n1 == 1, "第一次 %d 次调用，第二次多 %d 次" % (n1, len(asked) - n1))
        drp = fp.dropped(meta)
        ck("判失败的帧也进「为什么不能用」清单（不然它两头不沾，看着像凭空少了）",
           sum(drp.values()) >= 1 and any("没给出" in str(k[0]) for k in drp), str(drp))
        ck("ask_failed() 能把这些帧单独列出来给界面用（第二次跑仍然失败 → 仍是 1 张）",
           len(fp.ask_failed(meta)) == 1, str(len(fp.ask_failed(meta))))
    finally:
        fp.POOL_DIR = _old_pool
        fp._ask_vision = _old_ask

    print("== E 手工选图这条路：绕过判帧就必须标出来 ==")
    fake_frames = [{"i": 7, "ts": "00:24", "file": str(scene), "subject": "角色站在城门口",
                    "clean": True, "checked": True}]
    pg = [{"title": "甲", "cards": [{"t": "a", "d": "b"}],
           "photos": [{"frame": "F7"}, {"path": "/tmp/不知道哪来的.jpg", "caption": "我自己挑的"}]}]
    notes = ai_poster.bind_frames(pg, fake_frames)
    got = pg[0]["photos"]
    ck("按编号取的帧带上 judged=True",
       any(p.get("frame") == "F7" and p.get("judged") is True for p in got), str(got))
    ck("手工给 path 的那张标 judged=False（程序确实不知道它判没判过）",
       any(p.get("judged") is False for p in got), str(got))
    ck("手工图同时留一句人话进审核清单",
       any("没走判帧" in n for n in notes), "；".join(notes)[:70])
    plan = {"pages": [dict(pg[0], layout_key="guide"), {"title": "乙", "cards": [{"t": "c", "d": "d"}]}]}
    plan["pages"][1]["photos"] = list(pg[0]["photos"])
    uj = ai_poster.unjudged_photos(plan)
    ck("unjudged_photos 只报手工那张、不报判过的帧（两页各带同一份 photos）",
       len(uj) == 2 and all("不知道哪来的" in x for x in uj), str(uj))
    only_frame = {"pages": [{"title": "甲", "cards": [{"t": "a", "d": "b"}],
                             "photos": [p for p in got if p.get("frame")]},
                  {"title": "乙", "cards": [{"t": "c", "d": "d"}],
                   "photos": [p for p in got if p.get("frame")]}]}
    ck("全是编号帧的 plan 一条都不报（不制造噪音）",
       ai_poster.unjudged_photos(only_frame) == [],
       str(ai_poster.unjudged_photos(only_frame)))

    shutil.rmtree(str(SANDBOX), ignore_errors=True)
    if fails:
        print("\nFRAME_JUDGE_FAIL %d 条" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("FRAME_JUDGE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
