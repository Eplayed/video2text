# -*- coding: utf-8 -*-
"""B 站这条链的回归：抓完 → 本地转写 → 抽关键帧 → 删临时片（纯本地，全桩，不联网不转写）。

为什么必须有这一份（号主 2026-11-01 原话）：
  「每天抓取素材库中的素材后，就开始本地转写，这应该是一系列的操作，不是分开的。
    注意抓取素材会有关键帧图片，这个还是要的。」
实测到的断点有两处：B 站订阅那条**只入元数据**（好赛雷切片 351 条里只有 2 条有正文），
而 `transcribe_bvid` 只下音频——**音频里抽不出关键帧**，所以那 40 条新料帧池是 0/40。
这条链每一环都要被钉住，断一环界面就静默少一半东西。

用法：python3 scripts/e2e_bili_pipeline.py      末行 ALL_OK 才算通过
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import main as collector  # noqa: E402
from src import bilibili_fetcher as bf  # noqa: E402
from src import frame_pool  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        FAILS.append(name)


# ── 1. transcribe_with_frames：顺序与失败面 ────────────────────────────
def test_frames_chain():
    print("\n== 1. 一条链的顺序：转写 → 抽帧（文件还在）→ 删片 ==")
    wd = tempfile.mkdtemp(prefix="bili_pipe_")
    fake = os.path.join(wd, "BV1fake.mp4")
    seen = {"dl": 0, "asr": 0, "build": 0, "alive_at_build": None, "key": "", "segs": None}

    orig_dl, orig_audio, orig_build = bf.download_video, collector.transcribe_audio, frame_pool.build

    def dl(bvid, workdir, cookie="", max_height=720):
        seen["dl"] += 1
        open(fake, "wb").write(b"x" * 8192)
        return fake, ""

    def audio(path, model_size="small", language="zh", tag=""):
        seen["asr"] += 1
        return "转写正文" * 30, [{"start": 1.0, "end": 2.0, "text": "无限服测试"}]

    def build(video_path, aweme_id, ai_config=None, segments=None, progress_cb=None,
              judge=False):
        seen["build"] += 1
        seen["alive_at_build"] = os.path.exists(video_path)
        seen["key"] = aweme_id
        seen["segs"] = segments
        seen["judge"] = judge
        return {"frames": [{"i": 1}, {"i": 2}], "candidates": 2, "usable": 0}

    bf.download_video = dl
    collector.transcribe_audio = audio
    frame_pool.build = build
    try:
        text, note, frames = bf.transcribe_with_frames("BV1fake", workdir=wd, pool_key="BV1fake")
        ck("正文拿到了", text.startswith("转写正文"), text[:12])
        ck("抽帧跑了一次，而且是在删片之前（顺序反了就只能重下）",
           seen["build"] == 1 and seen["alive_at_build"] is True, str(seen))
        ck("帧池的键是 BV 号（攻略页与素材详情都按 aweme_id 取池）",
           seen["key"] == "BV1fake", seen["key"])
        ck("分句时间戳传给了帧池（near 字段靠它对图文论点）",
           bool(seen["segs"]) and seen["segs"][0]["text"] == "无限服测试", str(seen["segs"])[:40])
        ck("采集侧不判帧（judge=False，0 元）", seen.get("judge") is False, str(seen.get("judge")))
        ck("临时片已删", not os.path.exists(fake), fake)
        ck("回执里数得出帧", frames == 2 and note == "", "frames=%s note=%s" % (frames, note))

        seen["dl"] = seen["asr"] = seen["build"] = 0
        bf.download_video = lambda *a, **k: ("", "取整片失败：会员专享")
        _t, e2, f2 = bf.transcribe_with_frames("BV1fake", workdir=wd)
        ck("下载失败时不转写不抽帧（别把错误报成「抽帧坏了」）",
           e2.startswith("取整片失败") and seen["asr"] == 0 and seen["build"] == 0, e2)

        bf.download_video = dl
        orig_build2 = frame_pool.build
        frame_pool.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg 挂了"))
        open(fake, "wb").write(b"x" * 8192)
        t3, n3, f3 = bf.transcribe_with_frames("BV1fake", workdir=wd)
        ck("抽帧坏了不许把已到手的正文一起撤掉",
           t3.startswith("转写正文") and f3 == 0 and "抽帧失败" in n3, "%s | %s" % (n3[:30], f3))
        frame_pool.build = orig_build2

        seen["build"] = 0
        collector.transcribe_audio = lambda *a, **k: ("[转写失败] 模型没装", [])
        t4, n4, f4 = bf.transcribe_with_frames("BV1fake", workdir=wd)
        ck("转写失败就不抽帧（也别返回半成品文本）",
           t4 == "" and n4.startswith("[转写失败]") and seen["build"] == 0, n4[:20])
    finally:
        (bf.download_video, collector.transcribe_audio,
         frame_pool.build) = (orig_dl, orig_audio, orig_build)


# ── 2. 作业层的两档分流 + 回执措辞 ─────────────────────────────────────
def test_job_split():
    print("\n== 2. 短素材下整片抽帧、长录像只下音频 ==")
    import app as webapp

    calls = {"video": [], "audio": []}
    written = []
    orig = (webapp.content_store.pending_bili_transcripts,
            webapp.content_store.set_video_transcript, bf.transcribe_with_frames,
            bf.transcribe_bvid, bf.free_gb, bf.sweep_stale_tmp,
            webapp.content_store.get_videos_by_ids)
    # 资源闸与残片清理在测试里必须桩掉：真实 sweep 会去动 output/bili_asr_tmp 里正在跑的转写文件
    bf.free_gb = lambda p="": 28.0
    bf.sweep_stale_tmp = lambda *a, **k: (0, 0)
    rows = [{"id": 1, "aweme_id": "BVshort", "duration_sec": 240, "title": "无限服快讯"},
            {"id": 2, "aweme_id": "BVlong", "duration_sec": 40 * 60, "title": "四小时录像"}]

    def frames(bvid, cookie="", model_size="small", workdir="", pool_key=""):
        calls["video"].append(bvid)
        return "正文" * 100, "", 18

    def only(bvid, cookie="", model_size="base", workdir=""):
        calls["audio"].append(bvid)
        return "正文" * 100, ""

    webapp.content_store.pending_bili_transcripts = lambda *a, **k: rows
    webapp.content_store.set_video_transcript = lambda db, vid, t: written.append(vid) or True
    webapp.content_store.get_videos_by_ids = lambda db, ids: [r for r in rows if r["id"] in ids]
    bf.transcribe_with_frames = frames
    bf.transcribe_bvid = only
    try:
        got = webapp._bili_transcribe_job(lambda m: None, "某号", 2, "small", 45, 20, "")
        ck("短的走整片+抽帧，长的走音频-only（分档判据是条长，不是拍脑袋）",
           calls["video"] == ["BVshort"] and calls["audio"] == ["BVlong"], str(calls))
        ck("两条正文都落库", written == [1, 2], str(written))
        ck("回执念得出关键帧数与「太长没抽帧」（不许只报转写完成）",
           "关键帧 1 条 / 18 张" in got["note"] and "另有 1 条超 20 分钟只转写没抽帧" in got["note"],
           got["note"][:120])
        ck("0 元这句话写进回执（号主最在意哪一步花钱）", "0 元" in got["note"], "")

        written[:] = []
        calls["video"] = []
        bf.transcribe_with_frames = lambda bvid, **k: ("正文" * 100, "", 0)
        got2 = webapp._bili_transcribe_job(lambda m: None, "某号", 2, "small", 45, 20, "")
        ck("抽了 0 帧要进「问题」清单，不许报成顺利",
           any("一帧都没抽出" in x for x in got2["failed"]), str(got2["failed"])[:60])

        webapp.content_store.pending_bili_transcripts = lambda *a, **k: []
        got3 = webapp._bili_transcribe_job(lambda m: None, "某号", 2, "small", 45, 20, "")
        ck("没有待转写的就如实说，不谎报跑了多少条",
           "没有可转写" in got3["note"] and got3.get("done") is None, got3["note"][:40])
    finally:
        (webapp.content_store.pending_bili_transcripts,
         webapp.content_store.set_video_transcript, bf.transcribe_with_frames,
         bf.transcribe_bvid, bf.free_gb, bf.sweep_stale_tmp,
         webapp.content_store.get_videos_by_ids) = orig


# ── 3. 订阅抓完之后自动接续（攒进队列，收尾一次起一个作业）───────────────
def test_auto_chain():
    print("\n== 3. 抓完自动接上：一轮一个作业，第二个号不许撞槽排到明天 ==")
    import datetime as dt
    import app as webapp

    # 队列现在落在文件里，测试必须指到临时路径，否则会把真 config 写脏
    state = os.path.join(tempfile.mkdtemp(prefix="bili_q_"), "state.json")
    started = []
    orig = (webapp._bili_job_start, dict(webapp._bili_job), webapp.BILI_CHAIN_STATE,
            webapp._bili_chain_backlog)
    webapp.BILI_CHAIN_STATE = state
    webapp._bili_job_start = lambda kind, fn: (started.append((kind, fn)), True)[1]
    # 这一节测的是"同步抓完 → 记账 → 收尾起一个作业"，积压补啃另开一节测；
    # 不桩掉它就会去查生产库，生产库有没有积压会把这条断言变成随机数
    webapp._bili_chain_backlog = lambda: ([], {})
    webapp._bili_chain_save([])
    NIGHT = dt.datetime(2026, 11, 3, 2, 20)          # 窗口内
    try:
        q1 = webapp._bili_chain_queue("Tony兔兔鸡", 20)
        q2 = webapp._bili_chain_queue("漫步魔兽", 20)
        ck("每个号先记账、不当场抢作业槽（11-02 实测：第二个号撞槽，整天没转写）",
           "排进" in q1 and "排进" in q2 and not started, str(started))
        ck("本轮 0 条不记账、也不给回执添噪音",
           webapp._bili_chain_queue("漫步魔兽", 0) == ""
           and len(webapp._bili_chain_load()) == 2, str(webapp._bili_chain_load()))
        s = webapp._bili_chain_flush(NIGHT)
        ck("夜里窗口内收尾：起**一个**作业，两个号都带上",
           [k for k, _f in started] == ["asr-auto"] and "Tony兔兔鸡 20 条" in s
           and "漫步魔兽 20 条" in s, s)
        ck("交出去才清账（下一轮不许把上一轮的号再跑一遍）",
           webapp._bili_chain_load() == [] and webapp._bili_chain_flush(NIGHT) == "",
           str(webapp._bili_chain_load()))
        orig_job = webapp._bili_transcribe_job
        seen = []
        webapp._bili_transcribe_job = (
            lambda p, a, l, m, mm, fm, c, mode="asr": (seen.append((a, mode)),
                                                       {"note": "N-%s-%s" % (a, mode),
                                                        "done": 3, "frames": 5})[1])
        try:
            out = started[0][1](lambda m: None)
        finally:
            webapp._bili_transcribe_job = orig_job
        ck("这一趟作业里每个号都是先转写后补帧，两个号按顺序跑",
           seen == [("Tony兔兔鸡", "asr"), ("Tony兔兔鸡", "frames"),
                    ("漫步魔兽", "asr"), ("漫步魔兽", "frames")], str(seen))
        ck("回执分段点得出是哪个号（40 条混一行没法对账）",
           "【Tony兔兔鸡】" in out["note"] and "【漫步魔兽】" in out["note"]
           and out["accounts"] == 2 and out["done"] == 6, out["note"][:70])
        webapp._bili_chain_queue("Tony兔兔鸡", 8)
        webapp._bili_job["running"] = True
        webapp._bili_job["kind"] = "asr"
        s2 = webapp._bili_chain_flush(NIGHT)
        ck("槽真被占时不许静默丢：点名排在下一轮，账也留着",
           "没起来" in s2 and "8 条" in s2 and len(webapp._bili_chain_load()) == 1, s2)
    finally:
        webapp.BILI_CHAIN_STATE = orig[2]
        webapp._bili_job_start = orig[0]
        webapp._bili_chain_backlog = orig[3]
        webapp._bili_job["running"] = orig[1].get("running", False)
        webapp._bili_job["kind"] = orig[1].get("kind", "")

    src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    ck("B 站那条分支真的记了账（在写回执之前，否则界面看不到这句）",
       src.index("auto_note = _bili_chain_queue")
       < src.index('note = (f"新增 {len(inserted)} 条'), "")
    ck("记账用的是本轮真实入库数，不是写死的 20",
       "_bili_chain_queue(shown_author, len(inserted))" in src, "")
    ck("同步收尾真的 flush（攒了不 flush 就重演今天那条号没跑的样子）",
       "_chain_note = _bili_chain_flush()" in src
       and src.index("_chain_note = _bili_chain_flush()")
       < src.index('_sub_status["result"] = summary'), "")


# ── 4. 只补关键帧那一档（已经转写过、当年只下了音频的那些）──────────────
def test_frames_only():
    print("\n== 4. 已有正文的素材补关键帧（不重跑转写）==")
    wd = tempfile.mkdtemp(prefix="bili_fr_")
    fake = os.path.join(wd, "BV1fr.mp4")
    seen = {"dl": 0, "build": 0, "alive": None, "segs": "unset", "key": ""}

    orig_dl, orig_build = bf.download_video, frame_pool.build

    def dl(bvid, workdir, cookie="", max_height=720):
        seen["dl"] += 1
        open(fake, "wb").write(b"x" * 8192)
        return fake, ""

    def build(video_path, aweme_id, ai_config=None, segments=None, progress_cb=None,
              judge=False):
        seen["build"] += 1
        seen["alive"] = os.path.exists(video_path)
        seen["key"] = aweme_id
        seen["segs"] = segments
        return {"frames": [{"i": 1}], "candidates": 1}

    bf.download_video = dl
    frame_pool.build = build
    try:
        n, note = bf.extract_frames_only("BV1fr", workdir=wd)
        ck("抽到帧并落在 BV 号名下", n == 1 and seen["key"] == "BV1fr" and note == "",
           "n=%s key=%s" % (n, seen["key"]))
        ck("抽帧在删片之前", seen["alive"] is True and not os.path.exists(fake), str(seen["alive"]))
        ck("这一档没有分句时间戳（near 只能是空，不许假装完整）",
           seen["segs"] is None, str(seen["segs"]))
        seen["dl"] = seen["build"] = 0
        bf.download_video = lambda *a, **k: ("", "整片被删了")
        n2, note2 = bf.extract_frames_only("BV1fr", workdir=wd)
        ck("下不到片就不抽帧、也不抛异常", n2 == 0 and "整片被删了" in note2
           and seen["build"] == 0, note2)
        bf.download_video = dl
        frame_pool.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("磁盘满"))
        open(fake, "wb").write(b"x" * 8192)
        n3, note3 = bf.extract_frames_only("BV1fr", workdir=wd)
        ck("抽帧崩了也要把临时片删掉（不能把盘子留在原地）",
           n3 == 0 and "抽帧失败" in note3 and not os.path.exists(fake), note3)
    finally:
        (bf.download_video, frame_pool.build) = (orig_dl, orig_build)

    import app as webapp
    calls = {"audio": 0, "chain": 0, "extract": []}
    written = []
    orig2 = (webapp.content_store.pending_bili_frames,
             webapp.content_store.pending_bili_transcripts,
             webapp.content_store.set_video_transcript,
             bf.extract_frames_only, frame_pool.load, bf.free_gb, bf.sweep_stale_tmp)
    bf.free_gb = lambda p="": 28.0
    bf.sweep_stale_tmp = lambda *a, **k: (0, 0)
    rows = [{"id": 7, "aweme_id": "BVhas", "duration_sec": 300},
            {"id": 8, "aweme_id": "BVnone", "duration_sec": 300}]
    webapp.content_store.pending_bili_frames = lambda *a, **k: rows
    webapp.content_store.pending_bili_transcripts = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("补帧档不许去问待转写的行"))
    webapp.content_store.set_video_transcript = lambda *a, **k: written.append(a) or True
    bf.extract_frames_only = lambda bvid, **k: (calls["extract"].append(bvid), (18, ""))[1]
    frame_pool.load = lambda key: {"frames": [{"i": 1}]} if key == "BVhas" else None
    try:
        got = webapp._bili_transcribe_job(lambda m: None, "某号", 2, "small", 45, 20, "",
                                          mode="frames")
        ck("只给没帧池的那条补（已经有池的不重下整片）",
           calls["extract"] == ["BVnone"] and got["had_pool"] == 1, str(calls["extract"]))
        ck("补帧档一条都不重跑转写、不写正文",
           written == [] and calls["audio"] == 0 and calls["chain"] == 0, str(written))
        ck("回执数得出抽了几张、跳过几条",
           "补关键帧：1/1 条抽出 18 张" in got["note"] and "1 条已有帧池" in got["note"],
           got["note"][:120])
        ck("说清这一档的帧没有时间戳锚（别让人当成完整链产物）",
           "不重跑转写" in got["note"], got["note"][:60])
        # 11-02 早上真跑出来的坑：补帧档按"最新 N 条有正文的"取窗口，新料都在池子里，
        # 于是回执写着「0/0 条抽出 0 张；20 条已有帧池」——看着像跑过，其实一条没补。
        calls["extract"] = []
        frame_pool.load = lambda key: {"frames": [{"i": 1}]}
        got2 = webapp._bili_transcribe_job(lambda m: None, "某号", 2, "small", 45, 20, "",
                                           mode="frames")
        ck("整个窗口都已有帧池时要直说查了几条，别报成 0/0 像没活干",
           "都已有帧池" in got2["note"] and "查了 2 条" in got2["note"]
           and calls["extract"] == [], got2["note"][:70])
    finally:
        (webapp.content_store.pending_bili_frames,
         webapp.content_store.pending_bili_transcripts,
         webapp.content_store.set_video_transcript, bf.extract_frames_only,
         frame_pool.load, bf.free_gb, bf.sweep_stale_tmp) = orig2


# ── 5. 资源策略：下整片之前先问盘子，崩了留下的残片要收 ──────────────────
def test_resource_policy():
    print("\n== 5. 硬盘资源闸（本机 11-01 实测只剩 28GB／已用 93%）==")
    ck("空闲低于底线就不许下整片，并把数说出口",
       bf.may_download_video(3.0, 300)[:2] == (False, "空闲只剩 3.0GB（低于 6GB 底线）"),
       str(bf.may_download_video(3.0, 300)))
    ck("空闲勉强过线但这一条太大也不许下（预估要乘头寸，别把盘子顶满）",
       bf.may_download_video(6.0, 10 * 3600)[0] is False, str(bf.may_download_video(6.0, 36000)))
    ck("反面：5 分钟一条、空闲 28GB → 放行",
       bf.may_download_video(28.0, 300)[0] is True, str(bf.may_download_video(28.0, 300)))
    ck("读不到空闲就不下（宁可少抽帧，不许赌）",
       bf.may_download_video(-1.0, 300)[0] is False, "")
    ck("估不出条长（0）按最大处理，直接不抽帧",
       bf.est_download_mb(0) > 1e8 and bf.est_download_mb(300) == 30.0,
       "%.0f / %.0f" % (bf.est_download_mb(0), bf.est_download_mb(300)))
    ck("系数是实测出来的：480p 约 2-3MB/分钟，这里给两倍余量",
       bf.MB_PER_MIN == 6.0, "MB_PER_MIN=%s" % bf.MB_PER_MIN)

    d = tempfile.mkdtemp(prefix="bili_sweep_")
    fresh = os.path.join(d, "BVfresh.wav")
    stale = os.path.join(d, "BVstale.mp4")
    part = os.path.join(d, "BVx.part")
    for p, size in ((fresh, 5000), (stale, 8000), (part, 3000)):
        open(p, "wb").write(b"x" * size)
    old = __import__("time").time() - 8 * 3600
    os.utime(stale, (old, old))
    os.utime(part, (old, old))
    n, freed = bf.sweep_stale_tmp(d)
    ck("只删过老化线的，正在下的那条不许碰（反面样本）",
       n == 2 and freed == 11000 and os.path.exists(fresh), "n=%s freed=%s" % (n, freed))
    ck("目录不存在返回 0，不抛（省盘子的事不该把整条链卡住）",
       bf.sweep_stale_tmp(os.path.join(d, "没有这个目录")) == (0, 0), "")

    # 作业层：盘子不够时不许调下载，而且要在回执里点名
    import app as webapp
    calls = {"chain": 0, "audio": 0, "extract": 0, "sweep": 0}
    orig = (webapp.content_store.pending_bili_transcripts,
            webapp.content_store.pending_bili_frames, bf.transcribe_with_frames,
            bf.transcribe_bvid, bf.extract_frames_only, bf.free_gb, bf.sweep_stale_tmp,
            webapp.content_store.get_videos_by_ids, webapp.content_store.set_video_transcript)
    webapp.content_store.pending_bili_transcripts = lambda *a, **k: [
        {"id": 1, "aweme_id": "BV1", "duration_sec": 120}]
    webapp.content_store.pending_bili_frames = lambda *a, **k: [
        {"id": 1, "aweme_id": "BV1", "duration_sec": 120}]
    webapp.content_store.get_videos_by_ids = lambda db, ids: []
    webapp.content_store.set_video_transcript = lambda db, vid, text: True
    bf.transcribe_with_frames = lambda *a, **k: (calls.__setitem__("chain", calls["chain"] + 1),
                                                 ("正文" * 100, "", 18))[1]
    bf.transcribe_bvid = lambda *a, **k: (calls.__setitem__("audio", calls["audio"] + 1),
                                          ("正文" * 100, ""))[1]
    bf.extract_frames_only = lambda *a, **k: (calls.__setitem__("extract", calls["extract"] + 1),
                                              (18, ""))[1]
    bf.sweep_stale_tmp = lambda *a, **k: (calls.__setitem__("sweep", calls["sweep"] + 1),
                                          (2, 149000000))[1]
    try:
        bf.free_gb = lambda p="": 1.0
        got = webapp._bili_transcribe_job(lambda m: None, "某号", 1, "small", 45, 20, "")
        ck("转写照常做，但不去下整片（闸在下载之前，不是在失败之后）",
           calls["chain"] == 0 and calls["audio"] == 1, str(calls))
        ck("回执点名是空闲不够、并给出条数（不含糊说「没抽帧」）",
           "1 条因为空闲不够没下整片" in got["note"], got["note"][:130])
        ck("顺手清了残片也要说，并给出释放了多少（那是上一轮被 kill 留下的）",
           "残片 2 个" in got["note"] and "149MB" in got["note"], got["note"][-80:])
        got2 = webapp._bili_transcribe_job(lambda m: None, "某号", 1, "small", 45, 20, "",
                                           mode="frames")
        ck("补帧档盘子不够就明说「没下整片」，不静默跳条",
           got2["failed"] and "盘子不够" in got2["failed"][0], str(got2["failed"])[:60])
        calls["chain"] = calls["audio"] = calls["extract"] = 0
        bf.free_gb = lambda p="": 28.0
        got3 = webapp._bili_transcribe_job(lambda m: None, "某号", 1, "small", 45, 20, "")
        ck("反面：空闲够就走完整链（不许把闸写成永远拦）",
           calls["chain"] == 1 and calls["audio"] == 0, str(calls))
    finally:
        (webapp.content_store.pending_bili_transcripts,
         webapp.content_store.pending_bili_frames, bf.transcribe_with_frames,
         bf.transcribe_bvid, bf.extract_frames_only, bf.free_gb,
         bf.sweep_stale_tmp, webapp.content_store.get_videos_by_ids,
         webapp.content_store.set_video_transcript) = orig

    print("\n== 6. 整片格式串必须是 DASH 合流（11-01 实跑 40 条全挂在这一行）==")
    src = (ROOT / "src" / "bilibili_fetcher.py").read_text(encoding="utf-8")
    body = src.split("def download_video", 1)[1].split("\ndef ", 1)[0]
    ck("要「视频轨+音频轨」两档都要，不能只写单文件档位",
       "bv*[" in body and "+ba" in body, body[:80].replace("\n", " "))
    ck("合流成 mp4（ffmpeg 有则成，没合流就抽不到帧）", "--merge-output-format" in body, "")
    ck("反面：download_video 里不许有 `-x`（那是只下音频，抽不出画面）",
       '"-x"' not in body, "")
    ck("高度上限仍然由参数控制（不许写死）", "max_height" in body and "%d" in body, "")


# ── 7. 自动链的窗口、上限与落盘队列（号主 11-02：抓取与转写都放后半夜）──────
def test_chain_window():
    print("\n== 7. 只在后半夜跑；排队的号重启不许蒸发 ==")
    import datetime as dt
    import app as webapp

    tmp_state = os.path.join(tempfile.mkdtemp(prefix="bili_state_"), "state.json")
    orig = (webapp.BILI_CHAIN_STATE, webapp._bili_job_start, dict(webapp._bili_job),
            webapp._bili_chain_backlog)
    webapp.BILI_CHAIN_STATE = tmp_state
    started = []
    webapp._bili_job_start = lambda kind, fn: (started.append((kind, fn)), True)[1]
    # 积压从生产库里查，这条线只测"额度怎么分、什么时候起作业"，所以桩掉；
    # 真要测积压补啃，下面 test_chain_drain 自己换一份桩
    webapp._bili_chain_backlog = lambda: ([], {})
    webapp._bili_chain_save([])
    try:
        ck("窗口内（02:00 / 06:59）允许起作业",
           webapp._bili_in_window(dt.datetime(2026, 11, 2, 2, 0)) is True
           and webapp._bili_in_window(dt.datetime(2026, 11, 2, 6, 59)) is True, "")
        ck("反面：01:49 与 07:00 与 18:30 都不许起（白天不抢他的 CPU）",
           webapp._bili_in_window(dt.datetime(2026, 11, 2, 1, 49)) is False
           and webapp._bili_in_window(dt.datetime(2026, 11, 2, 7, 0)) is False
           and webapp._bili_in_window(dt.datetime(2026, 11, 2, 18, 30)) is False, "")

        del started[:]
        webapp._bili_chain_queue("Tony兔兔鸡", 20)
        webapp._bili_chain_queue("漫步魔兽", 20)
        ck("排队落到文件里（重启进程也不会丢）",
           os.path.exists(tmp_state) and "Tony兔兔鸡" in open(
               tmp_state, encoding="utf-8").read(), "")
        note = webapp._bili_chain_flush(dt.datetime(2026, 11, 2, 18, 40))
        ck("傍晚那轮只攒不跑：不起作业、队列留着、话说清",
           not started and "不在窗口" in note and len(webapp._bili_chain_load()) == 2, note)

        # 同一个号再来一轮：条数是上限不是增量，叠加会让一个号吃光一晚的额度
        webapp._bili_chain_queue("Tony兔兔鸡", 12)
        q = dict(webapp._bili_chain_load())
        ck("同一个号第二轮取较大值而不是相加（20 与 12 → 20）",
           q.get("Tony兔兔鸡") == 20, str(q))

        pend = {"Tony兔兔鸡": 20, "漫步魔兽": 20}
        lim = webapp._bili_chain_limits(webapp._bili_chain_load(), [], pend, 60)
        ck("新料按真实待转写条数给，不虚占额度（两个号各 20，剩的额度留给积压）",
           lim == {"Tony兔兔鸡": 20, "漫步魔兽": 20}, str(lim))
        ck("反面：只按本轮新增给，一个号也不许被平摊成整晚额度（那是虚账）",
           webapp._bili_chain_limits([("Tony兔兔鸡", 20)], {}, pend, 60) == {"Tony兔兔鸡": 20},
           "")
        ck("同一个号积压厚时能把这一轮的额度吃满（新料 20 + 按库里积压补 40）",
           webapp._bili_chain_limits([("Tony兔兔鸡", 20)], {}, {"Tony兔兔鸡": 300}, 60)
           == {"Tony兔兔鸡": 60}, "")

        orig_job = webapp._bili_transcribe_job
        seen = []
        webapp._bili_transcribe_job = (
            lambda p, a, l, m, mm, fm, c, mode="asr": (seen.append((a, l, mode)),
                                                       {"note": "N", "done": 1, "frames": 2})[1])
        try:
            note2 = webapp._bili_chain_flush(dt.datetime(2026, 11, 2, 2, 20))
            ck("夜里窗口内起作业：回执报号数与上限，队列清空",
               [k for k, _f in started] == ["asr-auto"] and "已起作业" in note2
               and ("上限 %d 条" % webapp.BILI_CHAIN_DAILY_CAP) in note2
               and webapp._bili_chain_load() == [], note2)
            ck("转写档的条长上限来自常量（45 分钟），不写死第二份",
               "BILI_ASR_MAX_MIN" in (ROOT / "web" / "app.py").read_text(encoding="utf-8"), "")
            out = started[0][1](lambda m: None)
            ck("作业按号排队、每号先转写后补帧，用的条数是分摊后的上限",
               seen == [("Tony兔兔鸡", 20, "asr"), ("Tony兔兔鸡", 20, "frames"),
                        ("漫步魔兽", 20, "asr"), ("漫步魔兽", 20, "frames")], str(seen))
        finally:
            webapp._bili_transcribe_job = orig_job

        del started[:]
        webapp._bili_chain_queue("Tony兔兔鸡", 20)
        webapp._bili_job["running"] = True
        webapp._bili_job["kind"] = "asr"
        note3 = webapp._bili_chain_flush(dt.datetime(2026, 11, 2, 3, 0))
        ck("槽被占时不清账：号留在队列里并点名",
           not started and "槽被占" in note3 and len(webapp._bili_chain_load()) == 1, note3)
        webapp._bili_job["running"] = False
        webapp._bili_job_start = lambda kind, fn: False
        note4 = webapp._bili_chain_flush(dt.datetime(2026, 11, 2, 3, 0))
        ck("抢不到槽也不清账（只有真起成功才交出去）",
           "抢不到" in note4 and len(webapp._bili_chain_load()) == 1, note4)

        webapp._bili_chain_write({"queue": [], "night": "2026-11-02",
                                  "spent": webapp.BILI_CHAIN_DAILY_CAP})
        webapp._bili_chain_queue("Tony兔兔鸡", 20)
        note5 = webapp._bili_chain_flush(dt.datetime(2026, 11, 2, 22, 0))
        ck("不在窗口时先说「等夜里」，别拿「额度用完」糊弄人，也要点得出排着的是谁",
           "不在窗口" in note5 and "额度用完" not in note5
           and "Tony兔兔鸡 20 条（新）" in note5, note5)
        webapp._bili_chain_write({"queue": [["Tony兔兔鸡", 20]], "night": "2026-11-03",
                                  "spent": webapp.BILI_CHAIN_DAILY_CAP})
        note6 = webapp._bili_chain_flush(dt.datetime(2026, 11, 3, 3, 0))
        ck("今晚额度真用完时也要说出口，并且**不清账**（新料等明晚，不许就地蒸发）",
           "额度用完" in note6 and len(webapp._bili_chain_load()) == 1, note6)

        with open(tmp_state, "w", encoding="utf-8") as f:
            f.write("{这不是 JSON")
        ck("队列文件坏了当空队列（不许把同步带崩）",
           webapp._bili_chain_load() == [] and webapp._bili_chain_flush(
               dt.datetime(2026, 11, 2, 3, 0)) == "", "")
    finally:
        webapp.BILI_CHAIN_STATE = orig[0]
        webapp._bili_job_start = orig[1]
        webapp._bili_chain_backlog = orig[3]
        webapp._bili_job["running"] = orig[2].get("running", False)
        webapp._bili_job["kind"] = orig[2].get("kind", "")

    src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    ck("工作台启动时会续跑队列（重启吃掉转写这件事 11-02 真发生过）",
       "_resume = _bili_chain_flush()" in src
       and src.index("_resume = _bili_chain_flush()") > src.index("_start_autosync()"), "")


# ── 8. 积压补啃：当天没新料的号也要轮到（只在窗口尾段、给新料留预备量、一晚一轮）──
def test_chain_drain():
    print("\n== 8. 当天没新料的号，积压也啃得动（尾段起、留预备量、一晚一轮）==")
    import datetime as dt
    import app as webapp

    tmp_state = os.path.join(tempfile.mkdtemp(prefix="bili_drain_"), "state.json")
    orig = (webapp.BILI_CHAIN_STATE, webapp._bili_job_start, dict(webapp._bili_job),
            webapp._bili_chain_backlog, webapp._bili_transcribe_job)
    webapp.BILI_CHAIN_STATE = tmp_state
    webapp._bili_chain_write({"queue": []})
    started = []
    webapp._bili_job_start = lambda kind, fn: (started.append((kind, fn)), True)[1]
    # 11-02 的真实形状：两个号各 20 条待转，另一个号压着 324 条积压
    backlog = [("Tony兔兔鸡", 20), ("漫步魔兽", 20), ("好赛雷切片", 324)]
    pend = {a: n for a, n in backlog}
    webapp._bili_chain_backlog = lambda: (list(backlog), dict(pend))
    seen = []
    webapp._bili_transcribe_job = (
        lambda p, a, l, m, mm, fm, c, mode="asr": (seen.append((a, l, mode)),
                                                   {"note": "N", "done": 1, "frames": 2})[1])

    def night(day, spent):
        webapp._bili_chain_write({"queue": [], "night": day, "spent": spent})

    CAP = webapp.BILI_CHAIN_DAILY_CAP
    try:
        webapp._bili_chain_queue("Tony兔兔鸡", 20)
        webapp._bili_chain_queue("漫步魔兽", 20)
        lim = webapp._bili_chain_limits(webapp._bili_chain_load(),
                                        [("好赛雷切片", 324)], pend, 60)
        ck("新料先给够，剩下的额度落到积压号头上（20+20+20，这一轮不超交出去的额度）",
           lim == {"Tony兔兔鸡": 20, "漫步魔兽": 20, "好赛雷切片": 20}, str(lim))
        ck("积压号已在队列里就不许再排一遍（排两遍会把同一批条数记两次）",
           webapp._bili_chain_limits([("好赛雷切片", 20)],
                                     [("漫步魔兽", 20), ("好赛雷切片", 324)], pend, 60)
           == {"好赛雷切片": 60}, "")

        night("2026-11-03", 0)
        ck("一晚一条没派过时，积压也只能拿「上限减新料预备量」和「尾段跑得完的条数」里小的那个",
           webapp._bili_chain_drain_budget(dt.datetime(2026, 11, 3, 5, 30)) == 30, "")
        night("2026-11-03", CAP - webapp.BILI_FRESH_RESERVE + 1)
        ck("新料已经把预备量以外的用完了 → 积压一条也不许动（新料优先不是口号）",
           webapp._bili_chain_drain_budget(dt.datetime(2026, 11, 3, 5, 30)) == 0, "")
        night("2026-11-03", 0)
        ck("06:50 才想起来啃 → 离窗口关闭只剩 10 分钟，按每条实测分钟最多派 3 条",
           webapp._bili_chain_drain_budget(dt.datetime(2026, 11, 3, 6, 50)) == 3, "")
        del started[:]
        ck("反面：05:30 之前那一眼不啃（02:00 那轮新料还没进来，先给它留位置）",
           webapp._bili_chain_tick(dt.datetime(2026, 11, 3, 5, 0)) == "" and not started, "")
        note = webapp._bili_chain_tick(dt.datetime(2026, 11, 3, 5, 30))
        ck("05:30 那一眼替积压起作业，回执点得出这一档是补啃",
           "已起作业" in note and "积压补啃" in note and len(started) == 1, note)
        del seen[:]
        started[0][1](lambda m: None)
        ck("按「积压最年轻」的号逐个给满，下一个号拿剩下的（一轮 30 条，不超这一档额度）",
           [x[0] for x in seen] == ["Tony兔兔鸡", "Tony兔兔鸡", "漫步魔兽", "漫步魔兽"]
           and [x[1] for x in seen] == [20, 20, 10, 10], str(seen))
        ck("一晚只啃一轮：05:31 那一眼不会再起第二单（否则一晚上限是摆设）",
           webapp._bili_chain_tick(dt.datetime(2026, 11, 3, 5, 31)) == "", "")
        ck("跨作业累计今晚已派条数（不是每一单都从上限重新数一遍）",
           webapp._bili_chain_spent(dt.datetime(2026, 11, 3, 5, 40)) == 30
           and webapp._bili_chain_budget(dt.datetime(2026, 11, 3, 5, 40)) == CAP - 30, "")

        night("2026-11-04", 0)
        del started[:]
        ck("白天（窗口外）那一眼绝不起作业",
           webapp._bili_chain_tick(dt.datetime(2026, 11, 4, 10, 0)) == ""
           and not started, "")
        webapp._bili_job["running"] = True
        webapp._bili_job["kind"] = "asr"
        ck("槽被占时那一眼不起（手动/同步的作业优先，积压留在库里不会丢）",
           webapp._bili_chain_tick(dt.datetime(2026, 11, 4, 5, 40)) == ""
           and not started, "")
        webapp._bili_job["running"] = False

        webapp._bili_chain_write({"queue": [["Tony兔兔鸡", 20]], "night": "2026-11-05",
                                  "spent": 0})
        del started[:]
        note2 = webapp._bili_chain_flush(dt.datetime(2026, 11, 5, 2, 30))
        want_drain = webapp.BILI_CHAIN_DAILY_CAP - 40
        ck("同步收尾那一单就把积压一起带上（新料 20 + 漫步 20 + 剩下的额度当场分给积压号）",
           ("好赛雷切片 %d 条（积压）" % want_drain) in note2
           and ("本轮 %d 条" % webapp.BILI_CHAIN_DAILY_CAP) in note2, note2)
        ck("这一单已经啃过积压，05:30 那一眼就不该再起第二单",
           webapp._bili_chain_tick(dt.datetime(2026, 11, 5, 5, 30)) == "", "")
    finally:
        webapp.BILI_CHAIN_STATE = orig[0]
        webapp._bili_job_start = orig[1]
        webapp._bili_chain_backlog = orig[3]
        webapp._bili_transcribe_job = orig[4]
        webapp._bili_job["running"] = orig[2].get("running", False)
        webapp._bili_job["kind"] = orig[2].get("kind", "")

    src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    loop_body = src[src.index("def _autosync_loop()"):src.index("def _start_autosync()")]
    ck("窗口尾段那一眼真挂在自动同步的定时器上（不挂就等于没写）",
       "_bili_chain_tick()" in loop_body, loop_body[:50])


# ── 9. 积压查询：按号统计"正文还空着"的条数（补啃排队的依据）────────────────
def test_pending_authors():
    print("\n== 9. 积压按号统计：只算转得动的，最新的号排前面 ==")
    from src import content_store as cs

    wd = tempfile.mkdtemp(prefix="bili_pending_")
    db = os.path.join(wd, "pending.db")
    conn = cs.connect(db)      # 建表走它自己的口，别在外面复制一份 schema
    box = {"row": 0}

    def add(sheet, author, bvid, tr, dur, pub):
        box["row"] += 1
        conn.execute("INSERT INTO videos (source_sheet, source_row, aweme_id, title, author,"
                     " transcript, duration_sec, published_at, updated_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (sheet, box["row"], bvid, "标题", author, tr, dur, pub,
                      "2026-11-02 00:00:00"))

    try:
        add("B站视频", "好赛雷切片", "BV1a", "", 300, "2026-10-12")
        add("B站视频", "好赛雷切片", "BV1b", "", 300, "2026-10-11")
        add("B站视频", "好赛雷切片", "BV1c", "已有正文的正文", 300, "2026-10-10")
        add("B站视频", "好赛雷切片", "BV1d", "", 15000, "2026-10-09")   # 4 小时直播录像
        add("B站视频", "漫步魔兽", "BV1e", "", 300, "2026-10-31")
        add("B站视频", "", "BV1f", "", 300, "2026-11-01")               # 作者空的行没法按号排队
        add("抖音视频数据", "别的号", "BV1g", "", 300, "2026-11-02")     # 不是这条链的表
        conn.commit()
        conn.close()

        rows = cs.bili_pending_authors(db, max_seconds=45 * 60)
        got = {r["author"]: r["n"] for r in rows}
        ck("按号统计只数 B 站表里正文空着的（好赛雷 2 条，不是 3 条）",
           got == {"好赛雷切片": 2, "漫步魔兽": 1}, str(rows))
        ck("超条长上限的根本转不动，不许记进积压账（那条 4 小时的不算）",
           all(r["n"] == 2 for r in rows if r["author"] == "好赛雷切片"), str(rows))
        ck("反面：不设上限时那条长录像会被数进来（证明过滤真在起作用）",
           {r["author"]: r["n"] for r in cs.bili_pending_authors(db)}
           == {"好赛雷切片": 3, "漫步魔兽": 1}, "")
        ck("作者为空的行不进排队（否则一个空号名占一份额度）",
           "" not in got, str(rows))
        ck("按最新积压排：最近还在更新的号在前（排产表吃新鲜料）",
           [r["author"] for r in rows] == ["漫步魔兽", "好赛雷切片"], str(rows))
        ck("上限按秒传：只给 4 分钟上限时 5 分钟的一条也不算积压",
           cs.bili_pending_authors(db, max_seconds=240) == [], "")
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    test_frames_chain()
    test_job_split()
    test_auto_chain()
    test_frames_only()
    test_resource_policy()
    test_chain_window()
    test_chain_drain()
    test_pending_authors()
    if FAILS:
        print("\nFAILED %d 条：%s" % (len(FAILS), "、".join(FAILS)))
        sys.exit(1)
    print("\nALL_OK")
