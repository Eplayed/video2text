# -*- coding: utf-8 -*-
"""frame_pool.py — 从素材视频里挑出「能当攻略证据」的干净帧

为什么要有这一层（2026-10-12 号主定的两条硬约束）：
  ① 攻略图里不能出现 up主 后期配上去的文字（口播字幕条、花字、贴纸、账号名）——
     那是纯搬运的铁证，读者一眼认得出来；
  ② 图必须和这一页的文案对得上，不能随便配一张。
实测过的那条路（"挑一张干净的帧"）走不通：一条 30 秒口播素材按 2 秒抽 15 帧，
**15/15 全挂着字幕条**。所以改成"逐帧定位横幅 → 裁掉 → 回读确认"，
实测这条素材裁完 7/7 干净。

三条判据按便宜在先排：
  1) 零成本：逐行数高饱和蓝像素，占比突起的行区间就是那条蓝色横幅；
  2) 花钱的兜底：第 1 条检不到但模型说"有后期字"时，问一次横幅的竖直区间
     （1 维区间比 bbox 靠谱得多，实测五帧与像素剖面逐帧吻合）；
  3) 自检：裁完再回读一次，模型说没有后期字才算干净——不然就是自己说干净。
判据都不成立的帧直接弃用，宁可不配图也不放一张带别人字幕的图出去。

图注由代码组（`caption_for`）：画面里是什么（模型看这张图说出来的）+ 这一帧的时间戳。
**不让出图模型编图注**，编出来的图注就是假信息。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import io
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL_DIR = ROOT / "output" / "assets" / "frames"

STEP_MIN = 1.6          # 短视频：每 1.6 秒一帧
STEP_MAX = 3.0          # 长视频最多松到 3 秒一帧
CAP_FRAMES = 18         # 候选帧上限（再多判图的钱不值）
FRAME_W = 900           # 抽帧宽度：排版那条带约 950px，再小就糊
BAND_MIN_FRAC = 0.30    # 一行里蓝像素占比到多少才算横幅


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
    而定距采样才保证每条口播都有对应画面可挑。"""
    os.makedirs(str(raw_dir), exist_ok=True)
    step = min(STEP_MAX, max(STEP_MIN, dur / float(CAP_FRAMES))) if dur else 2.0
    pat = os.path.join(str(raw_dir), "f_%03d.jpg")
    subprocess.run([_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(video_path), "-vf", "fps=1/%.2f,scale=%d:-1" % (step, FRAME_W),
                    "-frames:v", str(CAP_FRAMES), "-q:v", "3", pat],
                   capture_output=True, timeout=300)
    files = sorted(f for f in os.listdir(str(raw_dir)) if f.endswith(".jpg"))
    return [(os.path.join(str(raw_dir), f), "%02d:%02d" % (
        int(i * step) // 60, int(i * step) % 60)) for i, f in enumerate(files, 1)]


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


def crop_away(path, band, out_path):
    """把横幅之外、留得多的那一块当成品图（横幅每帧位置会变，所以只能逐帧裁）"""
    from PIL import Image
    im = Image.open(str(path)).convert("RGB")
    W, H = im.size
    top, bot = band[0] / 100.0, band[1] / 100.0
    box = (0, int(H * bot), W, H) if (1.0 - bot) > top else (0, 0, W, int(H * top))
    im.crop(box).save(str(out_path), quality=92)
    return out_path


Q_BAND = ('这一帧里，视频作者自己做后期压上去的字幕横幅（不属于游戏画面本身）占画面竖直方向的'
          '哪一段？只输出一行 JSON：{"band_top": 上沿百分比0-100, "band_bot": 下沿百分比0-100, '
          '"has_band": true/false}。游戏自己的面板、按钮、任务栏不要算。这一帧没有横幅就 '
          "has_band 给 false。")

Q_JUDGE = ('只输出一行 JSON：{"post_text": true/false, "post_words": ["图里后期压上来的中文，'
           '没有就空数组"], "subject": "一句话说清画面里是什么，不超过18字"}。'
           '"后期压上来"指视频作者自己加的字幕条/花字/贴纸/账号名/水印；'
           "游戏本身界面里的文字（技能名、面板、按钮、英文界面）不算。")


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
    for idx, (path, ts) in enumerate(frames, 1):
        band = band_rows(path)
        src = path
        if band:
            cut = clean_dir / ("c_%s" % os.path.basename(path))
            src = crop_away(path, band, cut)
        item = {"i": idx, "ts": ts, "file": str(src), "raw": str(path),
                "band": list(band) if band else None, "checked": False,
                "clean": None, "subject": "", "near": near_text(segments, ts)}
        if band and not (judge and ai_config and ai_config.get("api_key")):
            # 像素判据已经定位并裁掉了横幅 → 原帧就是中间产物，留着等于每条素材白占一倍盘子。
            # 但 judge 那一档要保留：万一模型说"还有后期字、位置在别处"，
            # 补第二刀必须从**没裁过的原帧**上量，拿已裁的图再裁一次位置就错了。
            _drop_raw(path)
            item["raw"] = ""
        if judge and ai_config and ai_config.get("api_key"):
            tok = _judge_item(item, ai_config)
            tokens += tok
            if item.get("checked"):
                # 判完了（含可能的第二刀）就不再需要原帧：补裁只在 _judge_item 内部发生
                _drop_raw(item.get("raw"))
                item["raw"] = ""
        pool.append(item)
        if progress_cb and judge and (idx % 5 == 0 or idx == len(frames)):
            progress_cb("判帧 %d/%d" % (idx, len(frames)))
    usable = [p for p in pool if p.get("clean") or (p.get("checked") is False and p.get("band"))]
    meta = {"aweme_id": str(aweme_id), "duration": round(dur, 1), "frames": pool,
            "usable": len(usable), "checked": bool(judge and ai_config
                                                   and ai_config.get("api_key")),
            "tokens": tokens, "engine": "frame_pool"}
    _dump(aweme_id, meta)
    return meta


def _drop_raw(path):
    """删掉裁切用的中间产物。失败不响——省盘子的事，不该把整条采集卡住。"""
    try:
        if path and os.path.exists(str(path)):
            os.remove(str(path))
    except OSError:
        pass


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


def _judge_item(item, ai_config):
    """一帧走完整判据：裁后回读 → 说还有后期字且像素没定位到 → 再问一次位置补一刀"""
    tok = 0
    try:
        got, t1 = _ask_vision(item["file"], Q_JUDGE, ai_config) or ({}, 0)
        tok += t1
        item["checked"] = True
        item["clean"] = not bool(got.get("post_text"))
        item["subject"] = str(got.get("subject") or "").strip()[:24]
        if got.get("post_text") and not item.get("band"):
            gb, t2 = _ask_vision(item["file"], Q_BAND, ai_config) or ({}, 0)
            tok += t2
            try:
                bt, bb = int(gb.get("band_top")), int(gb.get("band_bot"))
            except (TypeError, ValueError):
                bt = bb = 0
            if gb.get("has_band") and bb > bt:
                # 第二刀必须从**没裁过的原帧**上量；成品一律落 clean/，
                # 不然模型补裁的那张会留在 raw/ 里——盘子上"raw=中间产物"这条账就错了
                src = item.get("raw") or item["file"]
                cut_dir = Path(item["file"]).parent
                if cut_dir.name == "raw":
                    cut_dir = cut_dir.parent / "clean"
                    os.makedirs(str(cut_dir), exist_ok=True)
                cut = str(cut_dir / ("v_%s" % os.path.basename(item["file"])))
                item["file"] = str(crop_away(src, (bt, bb, 0.0), cut))
                item["band"] = [bt, bb, 0.0]
                g3, t3 = _ask_vision(item["file"], Q_JUDGE, ai_config) or ({}, 0)
                tok += t3
                item["clean"] = not bool(g3.get("post_text"))
                item["subject"] = str(g3.get("subject") or "").strip()[:24] or item["subject"]
    except Exception as e:
        item["error"] = str(e)[:80]
    return tok


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
    if len(todo) > limit:
        stride = max(1, len(todo) // limit)
        todo = todo[::stride][:limit]
    spent = 0
    for n, it in enumerate(todo, 1):
        spent += _judge_item(it, ai_config)
        # 判过就不再留原帧：补第二刀只在 _judge_item 这一次调用里发生，之后没人再读它。
        # 条件写成 checked 而不是 band——像素那条横幅检不出来、靠模型定位的帧（实测 8 张里 7 张）
        # 原来会把原帧留在盘上，一条素材白占一倍
        if it.get("checked"):
            _drop_raw(it.get("raw"))
            it["raw"] = ""
        if progress_cb:
            progress_cb("看图判帧 %d/%d" % (n, len(todo)))
    meta["tokens"] = int(meta.get("tokens") or 0) + spent
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


def caption_for(item):
    """图注由代码组，不让出图模型写：画面里是什么（模型看这张图说的）+ 时间戳"""
    subj = str(item.get("subject") or "").strip()
    ts = str(item.get("ts") or "").strip()
    if subj and ts:
        return subj + " · 素材画面 " + ts
    return subj or ("素材画面 " + ts if ts else "")


def index_text(items):
    """给拆解提示词用的帧清单：只给编号/时间/画面描述/那两句口播"""
    lines = []
    for it in items:
        lines.append("F%d｜%s｜画面：%s｜这一帧在说：%s" % (
            it.get("i", 0), it.get("ts", ""), it.get("subject") or "（未描述）",
            (it.get("near") or "（无）")[:46]))
    return "\n".join(lines)
