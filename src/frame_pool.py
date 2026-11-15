# -*- coding: utf-8 -*-
"""frame_pool.py — 从素材视频里挑出「能当攻略证据」的干净帧

为什么要有这一层（2026-10-12 号主定的两条硬约束）：
  ① 攻略图里不能出现 up主 后期配上去的文字（口播字幕条、花字、贴纸、账号名）——
     那是纯搬运的铁证，读者一眼认得出来；
  ② 图必须和这一页的文案对得上，不能随便配一张。
     2026-10-13 号主又划了一条边界：**作者后期画的圈、红框、箭头不算后期层**
     （原话"不用管红框，多留可用帧"）——挡的仍然只是字、贴纸、真人小窗、整帧卡这四类。
     这条只写代码注释不够，判帧那句提示词里也写明了，免得下次换模型又把这些帧扔掉。
实测过的那条路（"挑一张干净的帧"）走不通：一条 30 秒口播素材按 2 秒抽 15 帧，
**15/15 全挂着字幕条**。所以改成"逐帧定位后期层 → 能裁的裁掉 → 回读确认"。

判据按便宜在先排（**颜色不是判据**——六个号六种做法，只认蓝色那条一条素材 8 张里 7 张定不出来）：
  1) 零成本快路：逐行数高饱和蓝像素，占比突起且**贴顶/贴底**的行区间就是那条蓝色横幅，
     白捡一次定位（中部的一律不当横幅用，裁它等于把论点要用的画面切一半）；
  2) 零成本预筛 `light_share`：横贯整幅的**亮色**纯色行占比过高 = 整帧就是作者做的卡或表
     （实测尤安 18 帧里 17 帧、夏一可的对照表 2 帧），这种裁哪条边都救不回来，直接扔，
     连看图模型都不用问；黑边 letterbox 算暗的、单独走，不误伤横屏素材；
  3) 一次问齐 `_probe_item`：有没有后期字、**在哪**（top/bottom/middle/corner/full）、
     贴边的给竖直区间、画面主体是什么。贴顶贴底的才裁，裁完再回读一次（自己说干净不算干净）；
     压在中间、只在角落、整帧是卡的，一律弃用并记下原因（`dropped`）。
判据都不成立的帧直接弃用，宁可不配图也不放一张带别人字幕的图出去。

图注由代码组（`caption_for`）：只写"画面里是什么"（模型看这张图说出来的），不带时间戳、
不带"素材画面"这种来源字样——印在图上就是搬运自证。
**不让出图模型编图注**，编出来的图注就是假信息。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import io
import json
from math import isfinite
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL_DIR = ROOT / "output" / "assets" / "frames"

STEP_MIN = 1.6          # 短视频：每 1.6 秒一帧
CAP_FRAMES = 18         # 候选帧上限（再多判图的钱不值）
FRAME_W = 900           # 抽帧宽度：排版那条带约 950px，再小就糊
BAND_MIN_FRAC = 0.30    # 一行里蓝像素占比到多少才算横幅
CARD_MIN_LIGHT = 0.28   # 横贯整幅的**亮色**纯色行占比到多少，就判"整帧是作者做的卡/表"
BAND_EDGE_TOP = 35      # 贴顶的横幅：下沿不超过这个百分比才敢裁
BAND_EDGE_BOT = 60      # 贴底的横幅：上沿不低于这个百分比才敢裁


def dir_for(aweme_id):
    """一素材一目录：raw/ 是原帧，clean/ 是裁掉横幅后的成品帧，frames.json 是清单"""
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(aweme_id or "unknown"))
    return POOL_DIR / safe


def _ffbin(name):
    """ffmpeg/ffprobe 绝对路径兜底：服务进程 PATH 里没有 /usr/local/bin（实测踩过）"""
    try:
        from . import ffmpeg_probe
    except ImportError:
        import ffmpeg_probe
    f = getattr(ffmpeg_probe, "find_ffprobe" if name == "ffprobe" else "find_ffmpeg")()
    return f or name


def duration(video_path):
    try:
        r = subprocess.run([_ffbin("ffprobe"), "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", str(video_path)],
                           capture_output=True, text=True, timeout=40)
        return float((r.stdout or "0").strip().splitlines()[0])
    except Exception:
        return 0.0


def extract(video_path, raw_dir, dur):
    """定距抽帧。为什么不用场景切换为主：字幕条是**持续存在**的，换镜头不等于换掉字幕，
    定距采样至少覆盖视频全程，长视频的细节帧仍需按内容补抽。"""
    os.makedirs(str(raw_dir), exist_ok=True)
    step = max(STEP_MIN, dur / float(CAP_FRAMES)) if dur else 2.0
    pat = os.path.join(str(raw_dir), "f_%03d.jpg")
    subprocess.run([_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(video_path), "-vf", "fps=1/%.2f,scale=%d:-1" % (step, FRAME_W),
                    "-frames:v", str(CAP_FRAMES), "-q:v", "3", pat],
                   capture_output=True, timeout=300)
    files = sorted(f for f in os.listdir(str(raw_dir)) if f.endswith(".jpg"))
    return [(os.path.join(str(raw_dir), f), "%02d:%02d" % (
        int((i - 1) * step) // 60, int((i - 1) * step) % 60)) for i, f in enumerate(files, 1)]


def band_rows(path):
    """零成本判据：逐行统计高饱和蓝像素占比 → (上沿%, 下沿%, 峰值) 或 None"""
    try:
        from PIL import Image
        im = Image.open(str(path)).convert("RGB")
    except Exception:
        return None
    W, H = im.size
    small = im.resize((120, min(H, 480)))
    sw, sh = small.size
    px = small.load()
    rows = []
    for y in range(sh):
        n = 0
        for x in range(sw):
            r, g, b = px[x, y]
            if b > 150 and b - r > 55 and b - g > 35:
                n += 1
        rows.append(n / float(sw))
    peak = max(rows) if rows else 0.0
    if peak < BAND_MIN_FRAC:
        return None
    hi = [i for i, v in enumerate(rows) if v >= BAND_MIN_FRAC]
    pad = max(1, int(sh * 0.015))            # 横幅上下有渐隐阴影，卡太紧会留一道蓝边
    return (int(100.0 * max(0, hi[0] - pad) / sh),
            int(100.0 * min(sh, hi[-1] + pad + 1) / sh), round(peak, 2))


def light_share(path, width=170):
    """零成本预筛：横贯整幅的**亮色**纯色行占多少（作者做的卡/表这一项很高）。

    为什么只数亮的：真游戏画面不会有大片贯穿左右的纯色，而"整帧就是作者做的卡"
    （粉底 + 居中花字 + 账号名）和"作者叠的对照表"（白底黑字盖住画面）都是这样。
    黑边 letterbox 也是纯色但它是暗的，必须分开算——混进来会把横屏素材全误判成卡。
    判不准没关系：这条只用来**免费扔掉**明显救不回来的帧，剩下的照样问看图模型。
    没装 numpy 就返回 0.0（少一道免费预筛，不影响正确性）。
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return 0.0
    try:
        im = Image.open(str(path)).convert("RGB")
    except Exception:
        return 0.0
    W, H = im.size
    nw = min(width, W)
    nh = max(24, int(round(H * float(nw) / W)))
    a = np.asarray(im.resize((nw, nh), Image.BILINEAR)).astype(np.int16)
    med = np.median(a, axis=1)
    near = (np.abs(a - med[:, None, :]).sum(axis=2) < 30).mean(axis=1)
    row_mx = a.max(axis=2).mean(axis=1)
    return float(((near > 0.80) & (row_mx > 78)).mean())


def _is_edge_band(band):
    """只有贴顶或贴底的横幅才许裁：中部那种一裁就把论点要用的画面切一半"""
    try:
        return bool(band) and (band[1] <= BAND_EDGE_TOP or band[0] >= BAND_EDGE_BOT)
    except (TypeError, IndexError):
        return False


def crop_away(path, band, out_path):
    """把横幅之外、留得多的那一块当成品图（横幅每帧位置会变，所以只能逐帧裁）"""
    from PIL import Image
    im = Image.open(str(path)).convert("RGB")
    W, H = im.size
    top, bot = band[0] / 100.0, band[1] / 100.0
    box = (0, int(H * bot), W, H) if (1.0 - bot) > top else (0, 0, W, int(H * top))
    im.crop(box).save(str(out_path), quality=92)
    return out_path


# 一次问齐"有没有后期字 / 在哪 / 能不能裁一条边救回来 / 画面是什么"。
# 为什么不再分两问（先判干净、再问横幅位置）：实测六个号有六种后期层——
# 蓝色横幅条、底部白字黑边字幕、左上角小字免责声明、整帧作者做的卡（粉底+花字+账号名）、
# 盖住画面的对照表、角落真人出镜小窗。**颜色与位置都没有固定规律**，只认蓝色的像素判据
# 一条素材 8 张里 7 张定不出来；而"中部/角落/整帧"这三种裁一条边根本救不回来，
# 以前的代码却会照裁（把画面切一半还当成品）。所以位置判断交给模型，代码只管裁不裁。
# 形状那行只放**示例值**，字段说明写在下面：把说明塞进 JSON 模板里，模型会照抄进答案
# （实测 post_words 里原样返回了"后期压上来的中文，最多3条，没有就空数组"这句提示词）
Q_PROBE = ('这一帧是游戏视频里抽出来的一帧画面。只输出一行 JSON，形状照抄：'
           '{"post_text": false, "post_words": [], "where": "none", '
           '"band_top": 0, "band_bot": 0, "subject": ""}。'
           '字段怎么填：post_text＝画面里有没有视频作者自己做后期压上去的东西'
           '（字幕条、花字、标题条、账号名、水印、贴纸、真人出镜小窗），有填 true；'
           '游戏自身界面的文字（技能名、面板、按钮、英文界面、聊天框）一律不算。'
           '**作者后期画的圈、红框、箭头、指示线也不算**——那不是字也不是人脸，'
           '这一档要的是尽量多留下能当证据的画面（号主 2026-10-13 定：不用管红框，多留可用帧）。'
           'post_words＝那些后期文字，最多三条，没有就给空数组。'
           'where＝这些后期东西主要在画面哪里：none / top（贴上边）/ bottom（贴下边）/ '
           'middle（压在画面中间）/ corner（只在某个角落）/ full（整帧就是作者做的卡或表格，'
           '大面积纯色底、几乎没有真实游戏画面）。band_top、band_bot＝这块后期字占画面竖直'
           '方向的百分比（0-100，宁可多算一点也要包全）；**where 填 top 或 bottom 时这两个数'
           '必须给，两个都填 0 等于没回答**，其余情况才填 0。'
           'subject＝一句话说清画面里是什么，不超过 18 字。')

# 只补数字、不重判有无：模型说了 bottom 却不给区间时单独问一次（实测新提示词下 8 张里 7 张
# 把 band 留成 0，光靠那一次合并问答会白扔掉本可以裁的帧）。一维区间比 bbox 靠谱得多
Q_BAND = ('这一帧里，视频作者自己做后期压上去的字幕横幅（不属于游戏画面本身）占画面竖直方向的'
          '哪一段？只输出一行 JSON：{"has_band": true/false, "band_top": 0, "band_bot": 0}。'
          '游戏自己的面板、按钮、任务栏、聊天框不要算。这一帧没有那种横幅就 has_band 给 false。')

_WHY_MID = {"middle": "后期字压在画面中间，裁一条边救不回来",
            "corner": "角落有真人出镜或贴纸，裁横条切不掉",
            "full": "整帧是作者做的卡或表，不是游戏画面"}

# 模型把提示词里的字段说明抄回答案时，一定带这几个词（实测 post_words 回显过
# "后期压上来的中文，最多3条，没有就空数组"）。宁可少记一条文字线索，也不把说明当证据
_ECHO_WORDS = ("空数组", "最多3", "没有就", "不超过", "其余情况", "照抄", "填 0")


def _is_echo(s):
    return any(k in s for k in _ECHO_WORDS)


def _crop_ok(where, bt, bb):
    """能不能裁这一条：**只裁真的贴着边的**。
    模型说 bottom 却给个 40-70、或者两个数都留 0，照裁等于把画面切一半当成品"""
    try:
        bt, bb = int(bt), int(bb)
    except (TypeError, ValueError):
        return False
    if not (0 <= bt < bb <= 100) or (bb - bt) > 34:
        return False
    return (where == "top" and bb <= BAND_EDGE_TOP) or (where == "bottom" and bt >= BAND_EDGE_BOT)


def _no_verdict(item, why):
    """模型这一句**没给出结论**时的处置：不许沿用上一轮的 clean，也不许算"判过了"。

    checked=False 是故意的——`ensure_judged` 靠它挑待判的帧，下次重出还会再问一次；
    要是写成 True，一次网络抖动就把这帧永久标成"有后期字"，而实际上谁都没看过它。
    """
    item["clean"] = False
    item["checked"] = False
    item["judge_failed"] = True
    item["why"] = why
    item["crop_to"] = None
    return item


def apply_probe(item, got):
    """把模型那一句 JSON 落成这一帧的处置。纯函数，不联网，好测。

    三种落法（2026-10-21 Codex P0 之后定的，别再改回去）：
      1) 有结论、`post_text` 是 false → clean=True，能进版面；
      2) 有结论、`post_text` 是 true → clean=False，贴着边的记下 crop_to，裁不动的写 why；
      3) **没结论**（回答是空 / 不是 dict / 缺 post_text / post_text 不是布尔）
         → 一律不可用并写明原因。
    以前第 3 种走的是 `if not got.get("post_text")` —— 假值即算"没有后期字"，
    而 `_probe_item` 里 `_ask_vision(...) or ({}, 0)` 会把「没配 key、openai 没装、
    请求超时、返回的不是 JSON」全折成 `{}`：实测 `{}`、`{"where":"none"}`、
    `{"post_text":None}` 三种都判成 clean=True。**判不了 ≠ 干净**，
    带别人字幕条的图就是这么进版面的。
    """
    item["judge_failed"] = False
    if not isinstance(got, dict):
        return _no_verdict(item, "看图模型没给出回答（没配 key／超时／回来的不是 JSON），"
                                 "不能当成这帧干净")
    if not got:
        # `_ask_vision(...) or ({}, 0)` 把「没答上来」折成空 dict，这是最常见的一种失败形状
        return _no_verdict(item, "看图模型没给出可解析的回答（超时／被截断／不是 JSON），"
                                 "不能当成这帧干净")
    if "post_text" not in got:
        return _no_verdict(item, "看图回答里没有 post_text 这个字段，不能当成这帧干净")
    post_text = got.get("post_text")
    if not isinstance(post_text, bool):
        return _no_verdict(item, "看图回答的 post_text 不是 true/false（写的是「%s」），"
                                 "不能当成这帧干净" % str(post_text)[:12])
    where = str(got.get("where") or "").strip().lower()
    subj = str(got.get("subject") or "").strip()[:24]
    if subj:
        item["subject"] = subj
    words = got.get("post_words") or []
    if isinstance(words, list):
        # 防一手提示词回显：模型偶尔把字段说明原样抄回来（实测过 "…没有就空数组" 这种）
        item["words"] = [w for w in (str(x).strip()[:20] for x in words)
                         if w and not _is_echo(w)][:3]
    item["where"] = where
    item["checked"] = True
    if post_text is False:
        item["clean"] = True
        item["why"] = ""
        item["crop_to"] = None
        return item
    item["clean"] = False
    try:
        bt, bb = int(got.get("band_top")), int(got.get("band_bot"))
    except (TypeError, ValueError):
        bt = bb = -1
    if _crop_ok(where, bt, bb):
        item["crop_to"] = (bt, bb)
        item["why"] = ""
    else:
        item["crop_to"] = None
        item["why"] = _WHY_MID.get(where, "有后期字，但它不贴着边上，裁不动")
    return item


def _probe_item(item, ai_config):
    """一帧走完整判据：问一次 → 若是贴边的一条就裁掉再回读一次。返回花掉的 token。"""
    tok = 0
    try:
        got, t1 = _ask_vision(item["file"], Q_PROBE, ai_config) or ({}, 0)
        tok += t1
        apply_probe(item, got)
        if item.get("judge_failed"):
            # 第一问就没拿到结论：不补裁、不回读，留着 checked=False 让下次重问
            item.pop("crop_to", None)
            return tok
        if not item.get("crop_to") and str(item.get("where") or "") in ("top", "bottom"):
            # 模型说了贴边却没给数（新提示词下实测 8 张里 7 张把 band 留成 0）：
            # 只补问一次数字，不重判有无。不补这一问，本可以裁的帧会被整批白扔掉
            gb, t2 = _ask_vision(item["file"], Q_BAND, ai_config) or ({}, 0)
            tok += t2
            if gb.get("has_band") and _crop_ok(item["where"], gb.get("band_top"),
                                               gb.get("band_bot")):
                item["crop_to"] = (int(gb["band_top"]), int(gb["band_bot"]))
                item["why"] = ""
        if item.get("crop_to"):
            src = item.get("raw") or item["file"]
            bt, bb = item["crop_to"]
            cut_dir = Path(item["file"]).parent
            if cut_dir.name == "raw":
                cut_dir = cut_dir.parent / "clean"
                os.makedirs(str(cut_dir), exist_ok=True)
            cut = str(cut_dir / ("v_%s" % os.path.basename(item["file"])))
            item["file"] = str(crop_away(src, (bt, bb, 0.0), cut))
            item["band"] = [bt, bb, 0.0]
            got2, t3 = _ask_vision(item["file"], Q_PROBE, ai_config) or ({}, 0)
            tok += t3
            apply_probe(item, got2)
            if item.get("judge_failed"):
                # 裁完回读这一问失败：这张**已知**有后期字，绝不能因为"回读没回答"就放行
                item["clean"] = False
                item["why"] = "裁掉一条横幅后回读没拿到有效判断，这张不能用"
            elif not item.get("clean") and not item.get("why"):
                item["why"] = "裁掉一条字幕条之后回读，仍有后期字"
    except Exception as e:
        item["error"] = str(e)[:80]
        if item.get("checked"):
            # 结论已经有了，是裁切/回读这一步挂的：留着结论，但把"没走完"写进原因
            if not item.get("clean") and not item.get("why"):
                item["why"] = "裁切或回读这一步失败，这张先不用（%s）" % str(e)[:40]
        else:
            item["clean"] = False
            item["checked"] = False
            item["judge_failed"] = True
            item["why"] = "判帧调用异常，没拿到结论（%s）" % str(e)[:40]
    item.pop("crop_to", None)
    return tok


def _ask_vision(img_path, question, ai_config, max_tokens=200, timeout=90):
    """问一次看图模型。没配 key 就返回 None，调用方按"判不了"处理，不抛异常。"""
    if not ai_config or not ai_config.get("api_key") or ai_config.get("method") == "skip":
        return None
    import base64
    try:
        import openai
    except ImportError:
        return None
    try:
        from . import ai_poster
    except ImportError:
        import ai_poster
    key, base, _ = ai_poster._require_ai(ai_config)
    client = openai.OpenAI(api_key=key, base_url=base or "https://api.openai.com/v1",
                           timeout=timeout, max_retries=1)
    with open(str(img_path), "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    resp = client.chat.completions.create(
        model=ai_poster._ocr_model(ai_config),
        messages=[{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text", "text": question}]}],
        max_tokens=max_tokens)
    txt = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", txt, re.S)
    usage = getattr(resp, "usage", None)
    return (json.loads(m.group()) if m else {}), (getattr(usage, "total_tokens", 0) or 0)


def near_text(segments, ts):
    """这一帧前后 2 秒里在说什么——给拆解层判断"图和这句话对不对得上"用"""
    if not segments or not ts:
        return ""
    try:
        mm, ss = str(ts).split(":")
        t = int(mm) * 60 + int(ss)
    except Exception:
        return ""
    out = [str(s.get("text") or "").strip() for s in segments
           if float(s.get("start", -9)) <= t + 2 and float(s.get("end", -9)) >= t - 2]
    return "".join(out)[:120]


_ACTION_CUE = re.compile(r"点击|勾选|输入|打开|切换|连按|坐标|(?:一键|拉怪|输出)宏|/console")


def _evidence_frames(video_path, out_dir, duration_sec, segments, regular):
    """在原片可用时补采操作口播附近的帧，单独用于核对，不进入正式配图池。"""
    regular_times = [60 * int(ts.split(":")[0]) + int(ts.split(":")[1]) for _, ts in regular]
    targets = []
    for seg in segments or ():
        if not _ACTION_CUE.search(str(seg.get("text") or "")):
            continue
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(start) or not isfinite(end) or start < 0 or end < start:
            continue
        timestamp = round(start + min(1.0, (end - start) / 2))
        if not 0 <= timestamp < duration_sec:
            continue
        if min((abs(timestamp - value) for value in regular_times + targets), default=9) > 1:
            targets.append(timestamp)
    if not targets:
        return []
    evidence_dir = out_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, timestamp in enumerate(_even_sample(targets, 4), len(regular) + 1):
        file = evidence_dir / ("t_%06d.jpg" % timestamp)
        try:
            result = subprocess.run(
                [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", str(timestamp), "-i", str(video_path), "-vf", "scale=%d:-1" % FRAME_W,
                 "-frames:v", "1", "-q:v", "3", str(file)],
                capture_output=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode or not file.is_file():
            continue
        ts = "%02d:%02d" % divmod(timestamp, 60)
        results.append({"i": index, "ts": ts, "file": str(file),
                        "near": near_text(segments, ts), "evidence_only": True})
    return results


def build(video_path, aweme_id, ai_config=None, segments=None, progress_cb=None,
          judge=False):
    """抽帧 + 逐帧裁掉后期横幅，落 frames.json，返回清单 dict。

    judge=False（默认，也是采集层该用的那档）：**完全不花钱**——只抽、只裁、只记时间戳。
    判帧花钱且不必每条素材都判：批量同步 20 条就是 38 万 token，而出图一次只要 8 张。
    所以判帧推迟到出图那一刻按需要用（见 ensure_judged），采集侧保持零成本。
    """
    out_dir = dir_for(aweme_id)
    raw_dir = out_dir / "raw"
    clean_dir = out_dir / "clean"
    for d in (raw_dir, clean_dir):
        os.makedirs(str(d), exist_ok=True)
    dur = duration(video_path)
    frames = extract(video_path, raw_dir, dur)
    if progress_cb:
        progress_cb("抽帧 %d 张（%.0f 秒视频）" % (len(frames), dur))
    pool, tokens = [], 0
    judging = bool(judge and ai_config and ai_config.get("api_key"))
    for idx, (path, ts) in enumerate(frames, 1):
        band = band_rows(path)
        if band and not _is_edge_band(band):
            # 蓝色判据在别的号身上会乱指（实测尤安那种"卡中卡"它指到 25-28%），
            # 中部的一律不当横幅用——裁它等于把论点要用的画面切一半
            band = None
        src = path
        if band:
            cut = clean_dir / ("c_%s" % os.path.basename(path))
            src = crop_away(path, band, cut)
        item = {"i": idx, "ts": ts, "file": str(src), "raw": str(path),
                "band": list(band) if band else None, "checked": False,
                "clean": None, "subject": "", "why": "", "near": near_text(segments, ts)}
        if judging and light_share(path) >= CARD_MIN_LIGHT:
            # 免费的先扔：整帧是作者做的卡/表，裁哪条边都救不回来，没必要花一次看图的钱
            item.update(checked=True, clean=False, why="整帧是作者做的卡或表（大面积亮色横贯整幅）")
            _drop_raw(path)
            item["raw"] = ""
        else:
            if band and not judging:
                # 像素判据已经定位并裁掉了横幅 → 原帧就是中间产物，留着等于每条素材白占一倍盘子。
                # 但 judge 那一档要保留：万一模型说"还有后期字、位置在别处"，
                # 补第二刀必须从**没裁过的原帧**上量，拿已裁的图再裁一次位置就错了。
                _drop_raw(path)
                item["raw"] = ""
            if judging:
                tokens += _probe_item(item, ai_config)
                if item.get("checked"):
                    # 判完了（含裁一条再回读）就不再需要原帧：补裁只在 _probe_item 内部发生
                    _drop_spent_raw(item)
        pool.append(item)
        if progress_cb and judging and (idx % 5 == 0 or idx == len(frames)):
            progress_cb("判帧 %d/%d" % (idx, len(frames)))
    usable = [p for p in pool if p.get("clean") or (p.get("checked") is False and p.get("band"))]
    meta = {"aweme_id": str(aweme_id), "duration": round(dur, 1), "frames": pool,
            # usable = 已确认能进版面的（判过且干净）；candidates = 裁过或还没判、值得再看一眼的。
            # 以前只有笼统一个 usable，判完帧后没人重算，卡/表素材的清单上会留着"usable=17"
            # 这种和界面（0 张）对不上的数——下一个读这个文件的人就得猜哪个是真的
            "usable": len([p for p in pool if p.get("clean")]),
            "candidates": len(usable),
            "checked": bool(judge and ai_config and ai_config.get("api_key")),
            "tokens": tokens, "engine": "frame_pool"}
    extra = _evidence_frames(video_path, out_dir, dur, segments, frames)
    if extra:
        meta["evidence_frames"] = extra
    _dump(aweme_id, meta)
    return meta


def _drop_raw(path):
    """删掉裁切用的中间产物。失败不响——省盘子的事，不该把整条采集卡住。"""
    try:
        if path and os.path.exists(str(path)):
            os.remove(str(path))
    except OSError:
        pass


def _drop_spent_raw(item):
    """判完之后清中间产物：**只有成品帧和原帧不是同一个文件时才删**。

    2026-10-21 反向回归（`scripts/e2e_frame_judge.py` D 段）抓到：没定位到横幅的帧
    `file` 就是 `raw` 本身，原来 `build` / `ensure_judged` 里那句无条件
    `_drop_raw(it["raw"])` 等于把**刚判成干净、正在被版面引用的那张图删掉**。
    生产帧池实测 108 张 clean 里有 6 张已经是悬空路径（frames.json 写着 clean=true，
    文件没了 → `usable()` 按"文件在"筛，这一帧永远进不了版面，还看不出为什么没图）。
    """
    raw = item.get("raw") or ""
    if not raw:
        return
    try:
        if os.path.abspath(raw) == os.path.abspath(item.get("file") or ""):
            return          # 这就是成品本身，删了等于把证据删了
    except (TypeError, ValueError):
        return
    _drop_raw(raw)
    item["raw"] = ""


def prune_raw(keep_unjudged=True, progress_cb=None):
    """给已经落盘的旧帧池补一刀：把没用了的 raw/ 删掉。

    判过帧（checked）的池子说明"要不要补第二刀"这件事已经有结论了，raw 就可以删；
    没判过的默认留着（keep_unjudged）——那些还没定位横幅的帧，将来判的时候
    得从原帧上量位置。返回 {池子数, 删掉的帧数, 省下的字节}。
    """
    out = {"pools": 0, "files": 0, "bytes": 0}
    if not POOL_DIR.is_dir():
        return out
    for name in sorted(os.listdir(str(POOL_DIR))):
        fp = POOL_DIR / name / "frames.json"
        if not fp.exists():
            continue
        try:
            with open(str(fp), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        changed = False
        for it in meta.get("frames") or []:
            raw = it.get("raw") or ""
            if not raw or not os.path.exists(raw):
                continue
            if keep_unjudged and not it.get("checked"):
                continue
            if not it.get("band"):
                continue      # 没定位到横幅的池子，原帧还是唯一可用来源，留着
            try:
                out["bytes"] += os.path.getsize(raw)
            except OSError:
                pass
            _drop_raw(raw)
            it["raw"] = ""
            changed = True
            out["files"] += 1
        if changed:
            with open(str(fp), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            out["pools"] += 1
            rd = fp.parent / "raw"
            try:
                if rd.is_dir() and not os.listdir(str(rd)):
                    os.rmdir(str(rd))     # 空壳目录留着只会让人以为还有原帧
            except OSError:
                pass
    if progress_cb:
        progress_cb("清掉 %d 个池子的 %d 张中间帧，省 %.1f MB" % (
            out["pools"], out["files"], out["bytes"] / 1048576.0))
    return out


def _even_sample(items, limit):
    """从候选中均匀取样，包含首尾。"""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / float(limit - 1))]
            for i in range(limit)]


def ensure_judged(aweme_id, ai_config, limit=8, progress_cb=None):
    """出图时才判帧：只判 limit 张、按时间均匀取，钱花在真正会进版面的那几张上。

    判过的帧不重复判（frames.json 里 checked=true 就跳过），所以同一素材改主意
    重出第二次几乎不再产生看图费用。没配 key 就直接返回原清单，下游按"没判过"处理。
    """
    meta = load(aweme_id)
    if not meta or not ai_config or not ai_config.get("api_key"):
        return meta
    frames = meta.get("frames") or []
    todo = [f for f in frames if not f.get("checked")]
    if not todo:
        return meta
    # 旧的整数步长在 18 取 8 时只取到第 15 帧，末尾关键操作永远不判。
    todo = _even_sample(todo, limit)
    spent = 0
    for n, it in enumerate(todo, 1):
        if light_share(it.get("raw") or it.get("file") or "") >= CARD_MIN_LIGHT:
            # 同 build 里那道免费预筛：整帧是作者做的卡/表，裁哪条边都救不回来
            it.update(checked=True, clean=False, why="整帧是作者做的卡或表（大面积亮色横贯整幅）")
        else:
            spent += _probe_item(it, ai_config)
        # 判过就不再留原帧：补第二刀只在 _probe_item 这一次调用里发生，之后没人再读它。
        # 条件写成 checked 而不是 band——像素那条横幅检不出来、靠模型定位的帧（实测 8 张里 7 张）
        # 原来会把原帧留在盘上，一条素材白占一倍。**但 file 就是 raw 的那些不能删**（见 _drop_spent_raw）
        if it.get("checked"):
            _drop_spent_raw(it)
        if progress_cb:
            progress_cb("看图判帧 %d/%d" % (n, len(todo)))
    meta["tokens"] = int(meta.get("tokens") or 0) + spent
    # 判完必须把两个数重算一遍：只留旧值的话，"整帧都是卡"这种素材的 frames.json
    # 会一直写着 usable=17，而界面显示 0 张——同一件事两份账，谁先信谁都得错一次
    meta["usable"] = len(usable(meta))
    meta["candidates"] = len([f for f in frames
                              if f.get("clean") or (not f.get("checked") and f.get("band"))])
    _dump(aweme_id, meta)
    return meta


def _dump(aweme_id, meta):
    p = dir_for(aweme_id) / "frames.json"
    with open(str(p), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def load(aweme_id):
    p = dir_for(aweme_id) / "frames.json"
    if not os.path.exists(str(p)):
        return {}
    try:
        with open(str(p), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def usable(pool):
    """能进版面的帧：文件在、判过且判为干净（没判过的不进来——宁缺毋滥）"""
    out = []
    for it in (pool or {}).get("frames") or []:
        if not it.get("clean"):
            continue
        fp = it.get("file") or ""
        if fp and os.path.exists(fp):
            out.append(it)
    return out


def dropped(pool):
    """进不了版面的帧，按原因归堆：{(原因, 有没有原帧可裁): 张数}

    为什么要把原因摆出来：一条素材抽 18 帧可能只留 3 张——号主看到"18 张里 15 张被扔"
    会以为程序坏了。其实那 15 张是作者自己做的卡、盖了张对照表、或者花字压在画面正中，
    裁一条边救不回来。原因不摆出来，"宁缺毋滥"就长得像 bug。
    2026-10-21 起**判失败的帧也归到这里**：那种帧既不是"干净"也不是"有后期字"，
    而是"没判成"，必须出现在界面上（原因写的是"看图没给出回答"这一类），
    否则它就两头不沾——usable 里没有、dropped 里也没有，看着像凭空少了 8 张。
    """
    out = {}
    for it in (pool or {}).get("frames") or []:
        if it.get("clean"):
            continue
        if not it.get("checked") and not it.get("judge_failed"):
            continue      # 还没判过的不算"扔"，它们是 candidates
        why = str(it.get("why") or "").strip() or "回读后仍有后期字"
        key = (why, bool(it.get("band")))
        out[key] = out.get(key, 0) + 1
    return out


def counts_for(aweme_ids):
    """一批素材的画面账：`{aweme_id: {"total","judged","usable"}}`，**只读盘、不花钱**。

    为什么数在帧池这一层：排产格与海报弹窗都要念同一句"这条有没有图"，判据必须就是
    `usable()` 那一份（文件在、判过、判为干净），不能让调用方各自去数 JSON——
    11-02 号主问"B 站素材没有关键帧图片吗"，实际是 18 张都在盘上、判过的 8 张全不合格，
    而这句话以前只有海报弹窗（要先有整合稿）才看得到。
    没抽过帧的 key 不出现在结果里：**"没这一条"和"有池子但一张没用"是两句话**。
    """
    out = {}
    for aid in sorted({str(a or "").strip() for a in (aweme_ids or [])}):
        if not aid:
            continue
        pool = load(aid)
        frames = (pool or {}).get("frames") or []
        if not frames:
            continue
        out[aid] = {"total": len(frames),
                    "judged": sum(1 for f in frames
                                  if f.get("checked") or f.get("judge_failed")),
                    "usable": len(usable(pool))}
    return out


def ask_failed(pool):
    """判过但没拿到结论的帧（空回答／模型异常／裁后回读失败）：要人眼看，不许当干净。"""
    return [it for it in (pool or {}).get("frames") or [] if it.get("judge_failed")]


def caption_for(item):
    """图注＝画面里是什么（模型看这张图说的），**不带时间戳**。

    以前缀一句「· 素材画面 00:12」，本意是"这条有出处、不是编的"；但那是印在图上的，
    发到小红书就等于自证"这帧是从别人视频里抽的"（2026-10-13 复盘：判的就是"非真人分享"）。
    时间戳没丢——它留在 frames.json 和 manifest 里，我们核对"图和这句话对不对得上"要用，
    读者不需要看见。
    """
    return str(item.get("subject") or "").strip()


def index_text(items, mark_sharp=True):
    """给拆解提示词用的帧清单：编号/时间/画面描述/那两句口播，外加一句"这张清不清晰"。

    标这个是因为号主 10-22 看着成品说"图片发虚"——那两张本来就是作者用虚化背景贴的界面卡，
    模型不知道，照挑不误。清晰度免费量，偏虚的直接在清单里写明，让它优先挑清楚的那几张。
    """
    lines = []
    for it in items:
        tag = ""
        if mark_sharp:
            f = it.get("file") or ""
            if f and os.path.exists(f):
                sh = sharpness(f)
                tag = "｜清晰" if sh >= HARDEN_MIN_SHARP else "｜偏虚（别选这张）"
        lines.append("F%d｜%s｜画面：%s｜这一帧在说：%s%s" % (
            it.get("i", 0), it.get("ts", ""), it.get("subject") or "（未描述）",
            (it.get("near") or "（无）")[:46], tag))
    return "\n".join(lines)


# ── 出图前把真要印的那几张再收拾一遍（号主 2026-10-22 看图点名的两件事）──────────
# 1「图片字幕出现了，底部的字幕不要」：宽问题（一次问齐有无/位置/能不能裁/画面是什么）
#   实测会漏——同一帧池里 f_010/f_012/f_013 明明底部有一行白字黑边字幕，模型却答 where=none、
#   post_words=[]，于是被判成干净帧印上去了。**问题越窄答得越准**，所以印之前只问这一件。
# 2「图片发虚，没有精准截取到帧」：这类帧是"中间一块界面 + 四周一大片虚化背景"，
#   整幅塞进版面等于把虚化背景也放大印出来。让模型给一个"最该保留的矩形"，按它裁掉虚边。
# 两条都**只在成品目录里动文件**：生产帧池的 frames.json 与原帧/clean 帧一律不改，
# 裁错了顶多这一版样张重来，不会把素材账搞坏。
# 底部、顶部分开两问，各问各的那一条边。为什么不合并成一问：
# 2026-10-22 实测合并问会收到 {"bottom_text": true, "top_text": true, "band_top": 0, "band_bot": 100}
# ——模型懒得定位，直接把整幅报成"有字"，代码就没法裁，只能整帧作废（明明只是底部一行字幕）。
# 问"只看最下面那块"这种**限定区域**的窄问题，它才给得出可用的区间；给不出还有兜底位置。
Q_BOTTOM = ('这一帧要印在攻略页上。只看画面**最下面三分之一**那一块区域：'
            '那里有没有一行视频作者做后期压上去的字幕（白字黑边那种；'
            '游戏自己的面板、按钮、任务栏、聊天框不算）？'
            '只输出一行 JSON：{"has_text": false, "band_top": 0, "band_bot": 0}。'
            'has_text 为 true 时，band_top、band_bot 给这一行字幕在**整幅画面**竖直方向的'
            '百分比区间（0-100）；这种字幕一般只占 8-18%，别把整幅都圈进去。')
# 中间那一块单独问：裁顶/裁底之后，原本靠中的字幕会挪进"中间"，两条边的检查就漏了它
# （实测 F12 裁掉顶部标题后，「团本人数：≥3人」正落在画面中间，还是印出去了）。
Q_MID = ('这一帧要印在攻略页上。只看画面**中间那一半**（上下各留四分之一不看）：'
         '那里有没有视频作者做后期压上去的字幕或花字？'
         '游戏自己的面板文字、按钮名、职业名（防护/神圣/惩戒这种）不算。'
         '只输出一行 JSON：{"has_text": false}。')

Q_TOP = ('这一帧要印在攻略页上。只看画面**最上面五分之一**那一块区域：'
         '那里有没有视频作者做后期压上去的标题条或花字（游戏自己的界面标题不算）？'
         '只输出一行 JSON：{"has_text": false, "band_top": 0, "band_bot": 0}。'
         'has_text 为 true 时，band_top、band_bot 给这块字在**整幅画面**竖直方向的'
         '百分比区间（0-100）；这种标题条一般只占 10-25%，别把整幅都圈进去。')

# 模型说"有字"却不给可用区间时的兜底裁切位置（抖音这类字幕的实际分布）
FALLBACK_BOTTOM = (88.0, 100.0)
FALLBACK_TOP = (0.0, 12.0)
HARDEN_MAX_CUTS = 3         # 一帧最多动几刀（超过就当它救不回来）
STRIP_PAD = 6.0             # 模型报的字幕区间往外多切 6 个百分点：它报的边界偏小，留半截字比多裁一条边更难看
BAND_USABLE_SPAN = 32.0       # 一条字幕/标题条报超过这么多百分比就当它没定位准，走兜底

# 内容框单独一问：跟字幕那一问混在一起时，模型会只答前一个字段、box 留空。
# 而且**它给的是像素坐标**（实测 [182,165,843,660]），不是百分比——两种都要能收。
Q_BOX = ('这是要印在攻略页上的一帧游戏画面。只输出一行 JSON：'
         '{"box": [0, 0, 100, 100], "subject": ""}。'
         'box＝这一帧里**有内容的那块矩形**（左上、右下两个点）：四周那圈虚化的、'
         '看不清在什么的背景不要，只圈住真正能当证据的部分（界面面板、角色、卡片）。'
         '整幅都是有效内容才填 [0, 0, 100, 100]。subject＝一句话说清这块内容是什么，不超过 18 字。')

HARDEN_MIN_KEEP = 40.0    # 内容框至少要保留这么多百分比面积，低于它当作没给（裁太狠就没画面了）
HARDEN_DISCARD = 12.0     # 保留 88% 以上就别裁，白折腾一次
HARDEN_MAX_STRIP = 40.0   # 底部字幕条超过 40% 高，裁完剩不了什么，宁可这一帧不用
HARDEN_TOP_MAX = 35.0     # 顶部标题条允许切到 35%：那种大标题本来就占得高（实测 30% 会卡掉一张好帧）
HARDEN_MIN_SHARP = 14.0   # 中心区域边缘能量低于它=这张本来就虚，别印（号主 10-22 点名"发虚"）


def sharpness(path):
    """免费清晰度：中间 60% 区域的边缘能量均值。

    为什么不用拉普拉斯方差那套老指标：这条产线的"虚"不是失焦，是**作者自己把游戏画面
    放大虚化当背景、中间贴两块界面卡**——整帧方差被那两块卡拉高，只有看中心区域才分得开。
    实测同一帧池：走廊/角色那几张 21-24，职业选择那两张 11.7 / 4.7（正是号主说发虚的两张）。
    读不到图返回 999（当清晰），不许因为量不出来就把好帧判死。
    """
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except ImportError:
        return 999.0
    try:
        im = Image.open(str(path)).convert("L")
        W, H = im.size
        nw = min(360, W)
        im = im.resize((nw, max(1, int(nw * H / float(W)))))
        e = np.asarray(im.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
        h, w = e.shape
        core = e[int(h * 0.2):int(h * 0.8) or 1, int(w * 0.2):int(w * 0.8) or 1]
        return float(core.mean())
    except Exception:
        return 999.0


def _parse_json_line(txt):
    import json as _json
    s = str(txt or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        d = _json.loads(s[a:b + 1])
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _box_of(d, W, H):
    """模型给的内容矩形 → 像素框；不合理就返回 None（宁可不裁）。

    实测这个模型答的是**像素**、另一个答的是百分比，所以两种都收：
    任一值 >100 就按像素解释（并按画面尺寸夹住），否则按百分比换算。
    """
    box = d.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        v = [float(x) for x in box]
    except (TypeError, ValueError):
        return None
    if max(v) > 100.0:                     # 像素口径
        x0, y0, x1, y1 = v
    else:                                  # 百分比口径
        x0, y0 = W * v[0] / 100.0, H * v[1] / 100.0
        x1, y1 = W * v[2] / 100.0, H * v[3] / 100.0
    x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1))
    px = (max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1)))
    if px[2] - px[0] < 8 or px[3] - px[1] < 8:
        return None
    keep = float((px[2] - px[0]) * (px[3] - px[1])) / float(max(1, W * H)) * 100.0
    if keep < HARDEN_MIN_KEEP or keep > 100.0 - HARDEN_DISCARD:
        return None
    return px, round(keep, 1)


def harden_frame(path, ai_config, out_dir, name=None, progress=None):
    """把真要印上版的那一帧再核一遍并裁干净，返回 (成品路径, 记录)。

    两件事分开问（问题越窄答得越准，实测混在一起问时 box 那个字段模型直接不答）：
    ① 底部有没有作者压的字幕——有就裁掉那条边，裁完再核一次；还裁不干净就**不许上版**；
    ② 有内容的矩形是哪块——按它裁掉四周虚化的边（前景仍然不许切到面板本体，
       所以保留面积低于 40% 就当它没给）。
    只往 out_dir（成品包）里写文件，生产帧池一个字节都不动；判不了就原样用并如实写 why，
    不许把"没核过"报成"核过了"。
    """
    from PIL import Image
    rec = {"src": str(path), "strip": None, "box": None, "still_text": None,
           "subject": "", "ok": False, "why": "", "tokens": 0, "sharp": None,
           "too_soft": False}
    src = str(path)
    if not os.path.exists(src):
        rec["why"] = "帧文件不存在"
        return src, rec
    sh = sharpness(src)
    rec["sharp"] = round(sh, 1)
    if sh < HARDEN_MIN_SHARP:
        # 这一帧本身就是虚的（作者拿虚化背景贴卡片），再怎么裁也不会变清楚——别印
        rec["too_soft"] = True
        rec["why"] = ("这张画面本身是虚的（中心边缘能量 %.1f，低于 %g），"
                      "换一张清晰的或你自己传一张截图" % (sh, HARDEN_MIN_SHARP))
        return src, rec
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = out_dir / (name or ("harden_%s" % os.path.basename(src)))
    if str(cur) != src:
        shutil.copyfile(src, str(cur))
    def _ask_band(question, fallback):
        """问一条边。返回 (有没有, 区间或 None)；模型没答上返回 (None, None)。"""
        got, spent = _ask_vision(cur, question, ai_config) or ({}, 0)
        rec["tokens"] += int(spent or 0)
        if not isinstance(got, dict) or not got:
            return None, None
        if not got.get("has_text"):
            return False, None
        try:
            bt, bb = float(got.get("band_top") or 0), float(got.get("band_bot") or 0)
        except (TypeError, ValueError):
            bt = bb = 0.0
        if bb <= bt or (bb - bt) > BAND_USABLE_SPAN:
            bt, bb = fallback       # 报了有字却圈不准（实测会整幅圈 0-100）→ 用兜底位置
        return True, (bt, bb)

    cut = None
    # 一条帧上实测最多叠三层后期字（顶部标题 + 中间一行 + 底部字幕）。多留一趟只做复核不动刀，
    # 免得裁完第 3 层还没检查就被判成还剩字。
    for attempt in range(1, HARDEN_MAX_CUTS + 2):
        cut = None
        has, band = _ask_band(Q_BOTTOM, FALLBACK_BOTTOM)
        if has is None:
            rec["why"] = rec["why"] or "看图模型没回答，这一帧没再核就直接用了"
            return str(cur), rec
        if has:
            bt, bb = band
            edge = max(0.0, bt - STRIP_PAD)        # 底部那条：往上多切一点，别留半截
            if (100.0 - edge) <= HARDEN_MAX_STRIP:
                cut = ("bottom", edge, (bt, bb))
        if cut is None:
            has2, band2 = _ask_band(Q_TOP, FALLBACK_TOP)
            if has2 is None:
                rec["why"] = rec["why"] or "看图模型没回答，这一帧没再核就直接用了"
                return str(cur), rec
            if has2:
                bt2, bb2 = band2
                edge2 = min(100.0, bb2 + STRIP_PAD)     # 顶部那条：往下多切一点
                if edge2 <= HARDEN_TOP_MAX + STRIP_PAD:
                    cut = ("top", edge2, (bt2, bb2))
        if cut is None:
            # 两条边都干净了，最后问一次中间：压在中间的后期字裁不掉（一刀就把证据本体切两半），
            # 只能整帧作废——判帧那一步本来就是这个口径，别在出图这一步放水。
            mid, spent3 = _ask_vision(cur, Q_MID, ai_config) or ({}, 0)
            rec["tokens"] += int(spent3 or 0)
            if isinstance(mid, dict) and mid.get("has_text"):
                rec["still_text"] = True
                rec["why"] = "后期字压在画面中间，裁一刀就把证据本体切两半，这一帧不上版"
                return str(cur), rec
            rec["why"] = ""
            break
        if attempt > HARDEN_MAX_CUTS:      # 这一趟只许复核，不许再裁
            break
        side, edge, span = cut
        im = Image.open(str(cur)).convert("RGB")
        W, H = im.size
        box = ((0, 0, W, max(1, int(H * edge / 100.0))) if side == "bottom"
               else (0, min(H - 1, int(H * edge / 100.0)), W, H))
        if progress:
            progress("裁掉%s的后期字条（画面 %.0f%%-%.0f%%）" % ("底部" if side == "bottom" else "顶部",
                                                            span[0], span[1]))
        nxt = out_dir / ("strip_%s" % os.path.basename(str(cur)))
        im.crop(box).save(str(nxt), quality=92)
        cur = nxt
        rec["strip"] = [round(span[0], 1), round(span[1], 1), side]
    if cut is not None:
        # 两趟各裁一条还剩着字：这一帧不许上版（宁可这页没图，也不把别人的字幕带出去）
        rec["still_text"] = True
        rec["why"] = "后期字叠了 %d 层还裁不干净，这一帧不上版" % HARDEN_MAX_CUTS
        return str(cur), rec
    got2, spent2 = _ask_vision(cur, Q_BOX, ai_config) or ({}, 0)
    rec["tokens"] += int(spent2 or 0)
    if isinstance(got2, dict) and got2:
        rec["subject"] = str(got2.get("subject") or "").strip()[:24]
        im = Image.open(str(cur)).convert("RGB")
        parsed = _box_of(got2, *im.size)
        if parsed:
            px, keep = parsed
            nxt = out_dir / ("tight_%s" % os.path.basename(str(cur)))
            im.crop(px).save(str(nxt), quality=92)
            cur = nxt
            rec["box"] = {"px": list(px), "keep": keep}
            if progress:
                progress("按内容框裁掉虚化边（保留 %.0f%%）" % keep)
    rec["ok"] = True
    return str(cur), rec
