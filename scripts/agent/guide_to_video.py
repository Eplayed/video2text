# -*- coding: utf-8 -*-
"""把已经排好的图文攻略页（output/poster/<id>/poster_p*.png）拼成一条竖屏讲解视频。

为什么是"拿成品图做运动"而不是参考那条视频的做法：
  那条是把内容重建成 HTML 再逐帧渲染（HyperFrames），每页能自己动、能改；
  我们这套攻略页**本来就是 9:16 的成品图**（936×1664，等比放到 1080×1920 刚好），
  重建成 HTML 等于把排版层重写一遍，还要装新工具。所以这一版只做：静帧推拉 + 切页 + 口播 + 字幕条。

两条本机实测写死在代码里的前提（2026-10-14）：
  · 这台 Homebrew ffmpeg 8.1.1 **没有 libass / libfreetype**，`drawtext` 与 `subtitles` 两个滤镜都不存在
    → 中文字幕绝不能交给 ffmpeg 烧，必须由我们的排版层（PIL + 系统中文字体）画进画面；
  · `zoompan` / `overlay` / `concat` / `amix` 都在 → 运动用 zoompan，拼接用 concat。
  · 磁盘只剩十来个 G，所以**不逐帧导出 PNG 再合成**（2 分钟 30fps 就是 4500 张图），
    全程让 ffmpeg 在滤镜里处理。

用法：
  python3 scripts/agent/guide_to_video.py 82                       # 无声样片，先看节奏
  python3 scripts/agent/guide_to_video.py 82 --captions caps.json  # 字幕条（口播稿）
  python3 scripts/agent/guide_to_video.py 82 --audio-dir voice     # 每页一个 voice/1.mp3…，时长跟着音频走
产物：temp/guide_video_<id>/guide_<id>.mp4
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # scripts/agent/x.py → 仓库根
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402
from src import poster_typeset as ts  # noqa: E402

W, H = 1080, 1920
FPS = 30
PAGE_GLOB = "poster_p*.png"
CAP_H = 210            # 字幕带高度：整页缩到 H-CAP_H 以下，一条都不遮攻略内容
CAP_SIDE = 42          # 左右留边（整页居中后两侧是背景色，不是黑边）
CAP_FONT = 42
CAP_LINE = 58


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 失败：%s\n%s" % (" ".join(cmd[:8]), p.stderr[-800:]))
    return p


def check_ffmpeg():
    """开工前先确认那两个滤镜真的在——不在就直接停，别跑到一半产出垃圾。"""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         capture_output=True, text=True).stdout
    have = set()
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 2:
            have.add(parts[1])
    miss = [f for f in ("zoompan", "concat", "scale", "crop") if f not in have]
    if miss:
        raise RuntimeError("ffmpeg 缺滤镜 %s，这条路走不了" % "、".join(miss))
    if "drawtext" in have:
        print("注：这台 ffmpeg 有 drawtext，但字幕仍走排版层（口径只有一份）")


def wrap_caption(text, max_chars):
    """按字数断行，并把收尾标点拽回上一行末尾（中文行首出现「。」最难看）。"""
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    fixed = []
    for i, ln in enumerate(lines):
        if i and ln and ln[0] in ts._NO_HEAD and fixed:
            fixed[-1] += ln[0]
            ln = ln[1:]
        fixed.append(ln)
    return [l for l in fixed if l]


def _band_color(im, rows=6):
    """取页面最底下一行的平均色当字幕带底色——三套皮（米白/墨绿/暗蓝）自动跟上，不写死。"""
    return im.crop((0, im.height - rows, im.width, im.height)).resize((1, 1)).getpixel((0, 0))


def _ink_for(rgb):
    r, g, b = rgb[0], rgb[1], rgb[2]
    return (240, 240, 238) if (0.299 * r + 0.587 * g + 0.114 * b) / 255.0 < 0.45 else (30, 27, 24)


def _split_for(rgb, ratio):
    """同色系里推一档，用来画分隔线：亮底就压暗，暗底就提亮。"""
    return tuple(max(0, min(255, int(c + (255 - c) * ratio) if ratio > 0 else c * (1 + ratio)))
                 for c in rgb[:3])


def make_frame(png, captions):
    """一页攻略图 → 一帧 1080×1920 画面。

    没字幕时整页铺满（攻略页本来就是 9:16，等比放大刚好）；
    有字幕时**把整页缩小、底下空出一条带**，而不是把字压在页面上——
    攻略页的信息一直排到最底下，压上去就是盖内容（第一版实测盖住了"适合谁/先去试加点"两条）。
    """
    im = Image.open(png).convert("RGB")
    if not captions:
        return im.resize((W, H), Image.LANCZOS)
    band = _band_color(im)
    page_h = H - CAP_H
    scale = min((W - 2 * CAP_SIDE) / float(im.width), page_h / float(im.height))
    pw, ph = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    canvas = Image.new("RGB", (W, H), band)
    canvas.paste(im.resize((pw, ph), Image.LANCZOS), ((W - pw) // 2, 0))
    d = ImageDraw.Draw(canvas)
    ink = _ink_for(band)
    d.line([(CAP_SIDE, ph + 16), (W - CAP_SIDE, ph + 16)], fill=_split_for(band, 0.18 if ink[0] < 128 else -0.25), width=2)
    font = ts._font("sans_book", CAP_FONT)
    y = ph + 40
    for ln in wrap_caption("".join(captions), int((W - 2 * CAP_SIDE - 20) / CAP_FONT)):
        w = d.textlength(ln, font=font)
        d.text(((W - w) / 2.0, y), ln, font=font, fill=ink)
        y += CAP_LINE
    return canvas


def analyze_page(im, empty=12.0, min_gap=8, min_h=8, limit=None):
    """把一页拆成「一块一块」的横向内容带，并顺手做出一张"内容全擦掉、只留背景"的底图。

    `limit`：只在这个 y 之上找块。字幕带要在**整页揭示之前就一直在那儿**
    （观众是边听边看字幕，不是等页面全出完才看到字），所以它不参与揭示序列。

    为什么按**行内明度跨度**判空行，而不是"跟底色差多少"：攻略页的背景不是纯色
    （页眉有淡底、图片有模糊衬底、三套皮各不相同），拿一个底色去比会把整页都算成内容
    ——第一版实测就是这样，标题+图片被并成了一块 (0,926)。
    空行是"这一行从头到尾一个颜色"，跨度接近 0；有字的行跨度几十到两百，分得很开。

    擦除底图为什么不用纯色铺：那样盖出来的空白跟页面自身的渐变背景对不上，
    一眼就是"有条东西被涂掉了"。这里拿**紧邻该块上方那一行空行**整行纵向拉伸盖上去，
    背景延续不断，被擦掉的只有内容。
    """
    import numpy as np
    g = np.asarray(im.convert("L"), dtype=np.int16)
    # 一行的"内容量"＝这一行里偏离本行中位色的像素占比。
    # 为什么不用"跟底色差多少"，也不用"整行明度跨度"：攻略页顶部有一块通栏淡色面板、
    # 图片有模糊衬底，这两种行都"不跟底色一样"但**行内是平的**——按跨度判会把页眉到图片
    # 整段算成一块（实测第一块 (0,986)），按中位色偏离判就 correctly 分开。
    dev = np.abs(g - np.median(g, axis=1, keepdims=True)) > 26
    content = dev.mean(axis=1) > 0.003
    if limit is not None:
        content = content.copy()
        content[int(limit):] = False
    runs, start, last = [], None, -999
    for y in range(len(content)):
        if content[y]:
            if start is None:
                start = y
            last = y
        elif start is not None and y - last >= min_gap:
            runs.append((start, last + 1))
            start = None
    if start is not None:
        runs.append((start, last + 1))
    bands = []
    for y0, y1 in runs:                      # 太薄的是分隔细线之类，并进上一块
        if y1 - y0 < min_h and bands:
            bands[-1] = (bands[-1][0], y1)
        else:
            bands.append((y0, y1))
    # 往外扩几行：字的抗锯齿边缘比正文暗得少，不扩就会留一圈淡影（第一版实测有"竖线残影"）
    pad = 4
    grown = []
    for y0, y1 in bands:
        y0, y1 = max(0, y0 - pad), min(len(content), y1 + pad)
        if grown and y0 <= grown[-1][1]:
            grown[-1] = (grown[-1][0], max(grown[-1][1], y1))
        else:
            grown.append((y0, y1))
    bands = grown
    blank = im.copy()
    for y0, y1 in bands:
        cover = None
        for c in range(y0 - 1, -1, -1):      # 先往上找空行
            if not content[c]:
                cover = c
                break
        if cover is None:                    # 顶到边的块就往下找（往下还找不到就不擦，留原样）
            for c in range(y1, len(content)):
                if not content[c]:
                    cover = c
                    break
        if cover is None:
            continue
        strip = im.crop((0, int(cover), im.width, int(cover) + 1)).resize(
            (im.width, y1 - y0))
        blank.paste(strip, (0, y0))
    return bands, blank


def reveal_states(im, bands, blank, steps=4):
    """一页 → 一串"揭示到第几块"的画面。每一步只把那一块从原图抄过来，其余保持上一帧。

    为什么能直接整帧 blend：相邻两帧只有那一块不同，背景完全一致，
    所以 `Image.blend(上一帧, 目标帧, α)` 等价于"这一块淡入、其他一动不动"，
    不会闪黑、也不会整页糊一下。
    """
    states = [blank]
    cur = blank
    for y0, y1 in bands:
        nxt = cur.copy()
        nxt.paste(im.crop((0, y0, im.width, y1)), (0, y0))
        for s in range(1, steps + 1):
            states.append(Image.blend(cur, nxt, s / float(steps)))
        cur = nxt
    return states


def encode_stills(states, per_state, work, name, keep=False):
    """把一串静帧按每帧时长拼成视频。

    刻意**不逐帧导出 PNG**：这条片子 30fps，两分钟就是 3600 张图，这台机数据卷只剩十来个 G。
    这里只写"状态帧"（一页几十张），时长交给 concat 的 duration 指令，展开在 ffmpeg 内部做。

    两个踩过的坑（2026-10-14 实测）：
      · concat 清单里 `duration` 必须在 `file` **后面**（它修饰的是上一条），
        反过来写 ffmpeg 8 直接报 "Line 1: duration without file"；
      · 最后一张要再列一遍不带 duration，否则结尾那一帧不落地。
    """
    import shutil
    d = (work / ("states_" + name)).resolve()
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    names = []
    for i, st in enumerate(states):
        p = d / ("s_%03d.png" % i)
        st.save(p)
        names.append(str(p))
    for n in names:
        lines.append("file '%s'" % n)
        lines.append("duration %.3f" % per_state)
    lines.append("file '%s'" % names[-1])
    lst = d / "list.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = str(work / ("clip_%s.mp4" % name))
    try:
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-vf", "fps=%d,format=yuv420p" % FPS, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "21", out])
    finally:
        if not keep:
            shutil.rmtree(str(d), ignore_errors=True)
    return out


def clip(frame_png, sec, out, zoom_to=1.05):
    """一页 → 一段缓慢推近的视频（--mode slide 用；默认已经改成逐块揭示）。

    写法是这台 ffmpeg 8.1.1 实测能跑的那一种（2026-10-14）：
    `-loop 1` 喂静帧 + zoompan 里给 `d=<总帧数>` + `fps=30`，不写 x/y（默认就是居中推近）。
    踩过的坑：把 d 设成 1 再靠 `-t` 截长度，ffmpeg 直接报 "Failed to configure output pad"。

    推到 1.05 就收，不推到 1.12：zoompan 是居中裁切，推得越多上下被吃掉的越多，
    而底部那条字幕带正好在最下面——1.12 会把最后一行字裁掉半截。
    """
    frames = max(1, int(round(sec * FPS)))
    step = (zoom_to - 1.0) / frames
    zf = ("zoompan=z='min(zoom+%s,%.3f)':d=%d:s=%dx%d:fps=%d"
          % ("%.6f" % step, zoom_to, frames, W, H, FPS))
    run(["ffmpeg", "-y", "-loop", "1", "-i", frame_png, "-vf", zf,
         "-frames:v", str(frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", out])


def audio_dur(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def load_pages(pkg):
    """按 manifest 里 plan.pages 的页数取图，多的旧文件挑出来并警告。

    为什么不能直接 glob：这一层撞到一个已知遗留问题——一组重拆成更少页时，
    旧的 poster_pN.png 不会被清掉（output/poster/82 就是 manifest 2 页、目录里 3 张图）。
    直接 glob 会把上一版的最后一页当成本版结尾播出去。
    """
    mf = pkg / "manifest.json"
    pages = sorted(pkg.glob(PAGE_GLOB), key=lambda p: int(p.stem.rsplit("p", 1)[-1]))
    if not pages:
        return [], []
    want = None
    if mf.is_file():
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
            want = len((man.get("plan") or {}).get("pages") or [])
        except ValueError:
            want = None
    if want and 0 < want < len(pages):
        print("注意：目录里有 %d 张成品图，但 manifest 只记了 %d 页——多出来的按旧文件丢掉：%s"
              % (len(pages), want, ", ".join(p.name for p in pages[want:])))
        return pages[:want], pages[want:]
    return pages, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", help="海报 id（output/poster/<id>）")
    ap.add_argument("--sec", type=float, default=6.0, help="没有配音时每页停几秒")
    ap.add_argument("--captions", help="字幕 JSON：{\"1\": [\"第一句\",\"第二句\"], ...} 或 [\"...\",\"...\"]")
    ap.add_argument("--auto-captions", action="store_true",
                    help="没写口播稿时，先拿每页自己的大标题当字幕占位（只用于看节奏，别当成品）")
    ap.add_argument("--audio-dir", help="每页一个音频：1.mp3 / 2.mp3 …（给了就按音频时长定页长）")
    ap.add_argument("--mode", choices=("reveal", "slide"), default="reveal",
                    help="reveal＝一块一块从上往下出现（PPT 那种，默认）；slide＝整页缓慢推近")
    ap.add_argument("--steps", type=int, default=4, help="每块淡入分几步（越大越柔，状态帧越多）")
    ap.add_argument("--per-state", type=float, default=0.10,
                    help="没配音时每步多少秒（0.10 × 4 步 ≈ 每块 0.4 秒冒出来）")
    ap.add_argument("--hold", type=float, default=1.4, help="这一页全部出完之后停多久")
    ap.add_argument("--no-motion", action="store_true", help="不做推拉（纯切页，最快）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pkg = ROOT / "output" / "poster" / str(args.pid)
    pages, _stale = load_pages(pkg)
    if not pages:
        print("找不到成品页：%s/%s" % (pkg, PAGE_GLOB))
        return 1
    caps = {}
    if args.captions:
        raw = json.loads(Path(args.captions).read_text(encoding="utf-8"))
        caps = {int(k): (v if isinstance(v, list) else [v]) for k, v in raw.items()} \
            if isinstance(raw, dict) else {i + 1: [v] for i, v in enumerate(raw)}
    elif args.auto_captions:
        try:
            man = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
            for i, p in enumerate((man.get("plan") or {}).get("pages") or [], start=1):
                t = (p.get("title") or "").strip()
                if t:
                    caps[i] = [t]
        except (OSError, ValueError):
            pass
        if caps:
            print("字幕用的是每页自己的大标题（占位看节奏，真字幕等口播稿）")

    work = ROOT / "temp" / ("guide_video_%s" % args.pid)
    work.mkdir(parents=True, exist_ok=True)
    check_ffmpeg()
    print("页 %d 张：%s" % (len(pages), ", ".join(p.name for p in pages)))

    clips, durs = [], []
    for i, png in enumerate(pages, start=1):
        comp = make_frame(png, caps.get(i))
        frame = work / ("frame_%d.png" % i)
        comp.save(frame)
        mp3 = None
        if args.audio_dir:
            for ext in ("mp3", "m4a", "wav", "aac"):
                cand = Path(args.audio_dir) / ("%d.%s" % (i, ext))
                if cand.is_file():
                    mp3 = str(cand)
                    break
        sec = (audio_dur(mp3) + 0.35) if mp3 else args.sec
        if sec <= 0:
            print("第 %d 页音频时长读不出来，按 %.1f 秒算" % (i, args.sec))
            sec = args.sec
        out = str(work / ("clip_%d.mp4" % i))
        if args.mode == "reveal" and not args.no_motion:
            # 有字幕带时，字幕不参与揭示（它得一直在，观众边听边看），只揭示上面那块页面
            bands, blank = analyze_page(comp, limit=(H - CAP_H) if caps.get(i) else None)
            states = reveal_states(comp, bands, blank, steps=max(1, args.steps))
            # 有配音就按音频倒推每步多长（留 out.hold 收尾停一下）；没配音用固定步长
            per = args.per_state
            if mp3:
                budget = max(0.4, sec - args.hold)
                per = min(0.5, max(0.04, budget / float(len(states))))
            total = per * (len(states) - 1) + (args.hold if mp3 else 0.0)
            if not mp3:
                total = per * (len(states) - 1)
            out = encode_stills(states, per, work, str(i))
            # 全出完之后多停一会儿：给观众读完这页的时间，也接得上口播尾巴
            if args.hold > 0:
                held = str(work / ("clip_%d_h.mp4" % i))
                run(["ffmpeg", "-y", "-i", out, "-vf",
                     "tpad=stop_mode=clone:stop_duration=%.3f" % args.hold,
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                     "-pix_fmt", "yuv420p", held])
                out = held
            sec = total
            print("  第 %d 页 %d 块 / %d 状态 / %.1fs%s"
                  % (i, len(bands), len(states), sec, "（配音）" if mp3 else ""))
        else:
            if args.no_motion:
                run(["ffmpeg", "-y", "-loop", "1", "-i", str(frame), "-t", "%.3f" % sec,
                     "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                     "-pix_fmt", "yuv420p", out])
            else:
                clip(str(frame), sec, out)
            print("  第 %d 页 %.1fs%s" % (i, sec, "（配音）" if mp3 else ""))
        if mp3:
            mixed = str(work / ("clip_%d_a.mp4" % i))
            run(["ffmpeg", "-y", "-i", out, "-i", mp3, "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy", "-c:a", "aac", "-shortest", mixed])
            out = mixed
        clips.append(out)
        durs.append(sec)

    lst = work / "list.txt"
    lst.write_text("".join("file '%s'\n" % c for c in clips), encoding="utf-8")
    dest = args.out or str(work / ("guide_%s.mp4" % args.pid))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", dest]
        + (["-c:a", "aac"] if any(Path(c).with_suffix("").name.endswith("_a") for c in clips) else []))
    size = os.path.getsize(dest) / 1048576.0
    print("成片 %s｜%.1f 秒｜%.1f MB" % (dest, sum(durs), size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
