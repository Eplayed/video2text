#!/usr/bin/env python3
"""课程录屏 -> 每集「转写稿 + 关键帧 + 时间轴对齐」素材包。

面向「界面演示型」课程视频：口播走 ASR，画面走场景切分抽帧，
最后按时间戳把两者对齐，产出一份可直接人工/AI 复核的分镜时间轴。

本脚本只做「取料」，不做归纳总结——总结由上层（Qoder 读关键帧 + 转写稿）完成。

用法：
    python3 scripts/course_analyze.py <视频文件或目录>
    python3 scripts/course_analyze.py ~/Movies/course --model small --scene 0.25
    python3 scripts/course_analyze.py xxx.mov --only-frames --interval 20

产物（每个输入视频一个子目录）：
    output/course/<片名>/
        transcript.txt      带时间戳的完整转写稿
        segments.json       转写片段结构化数据
        frames/f_0001.png   场景变化关键帧
        timeline.json       关键帧 <-> 该时段口播 的对齐结果
        episode.md          分镜时间轴骨架（给上层写总结用）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Intel Mac 上 faster-whisper/ctranslate2 与 torch、numpy 各带一份 libiomp5，
# 不设这个会在 model.transcribe() 处直接崩（OMP Error #15）。必须在 import 前设。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# 不设线程数时 ctranslate2 在这台 Intel 上会退化到 ~1.6x 实时，设 4 线程实测 5.5x。
os.environ.setdefault("OMP_NUM_THREADS", str(min(4, os.cpu_count() or 4)))

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
PTS_RE = re.compile(r"pts_time:(\d+\.?\d*)")


def run(cmd, quiet=True):
    """跑子进程，返回 (returncode, stdout+stderr)。"""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def probe(path):
    """取视频时长（秒）。"""
    _, out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def extract_audio(path, out_wav):
    """抽音轨 -> 16k 单声道 wav（whisper 的胃口）。"""
    rc, out = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(out_wav)])
    if rc != 0 or not out_wav.exists():
        raise RuntimeError("抽音轨失败：%s" % out[-400:])


ASR_TERMS = (
    "以下是影视飓风 AI 实战课的讲解内容。"
    "平台叫 TapNow，图片模型叫 Tap Nano 2 与 Tap Nano 2 Pro，还有 Midjourney。"
    "视频模型叫 Seedance 2.0、Seedance 2.0 Fast、Kling V3 Pro、Video 3.1。"
    "核心术语：文生图、图生图、文生视频、图生视频、视频生视频、"
    "提示词、首帧、尾帧、首尾帧、参考、全能参考、帧、画布、节点、素材库、"
    "相机控制、分辨率、比例、清晰度、生成时长、生成音频、积分、算力、跟练、AI 跟练室、"
    "学习助手、提示词板、一键填充、操作演示、工作空间。"
)


def transcribe(wav, model_size):
    """ASR 转写，返回 [(start, end, text), ...]。"""
    from faster_whisper import WhisperModel

    t0 = time.time()
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(wav), language="zh", vad_filter=True, beam_size=1,
        initial_prompt=ASR_TERMS,
    )
    rows = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            rows.append((round(seg.start, 2), round(seg.end, 2), text))
    el = time.time() - t0
    print("    转写完成：%d 段，耗时 %.0fs（%.2fx 实时）" % (len(rows), el, (info.duration / el) if el else 0))
    return rows


def extract_frames(path, frames_dir, scene_thr, interval, max_frames):
    """按场景变化抽关键帧，返回 [(序号, 时间点秒, 文件名), ...]。

    场景切分适合 PPT/界面这类离散画面；操作演示太密时可用 --interval 兜底，
    两者都开则取并集（按时间去重）。
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    picked = []

    if scene_thr > 0:
        rc, out = run(["ffmpeg", "-hide_banner", "-i", str(path),
                       "-vf", "select='gt(scene,%s)',showinfo,scale=1280:-1" % scene_thr,
                       "-vsync", "vfr", str(frames_dir / "s_%04d.png")])
        times = [float(m) for m in PTS_RE.findall(out)]
        pngs = sorted(frames_dir.glob("s_*.png"))
        for png, t in zip(pngs, times):
            picked.append((t, png.name))
        print("    场景切分：%d 帧（阈值 %s）" % (len(picked), scene_thr))

    if interval > 0:
        rc, out = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                       "-vf", "fps=1/%d,scale=1280:-1" % interval,
                       str(frames_dir / "i_%04d.png")])
        pngs = sorted(frames_dir.glob("i_*.png"))
        for idx, png in enumerate(pngs):
            picked.append((idx * float(interval), png.name))
        print("    定距采样：新增 %d 帧（每 %ds）" % (len(pngs), interval))

    # 按时间去重（间隔 <2s 的只留第一张），再统一改名
    picked.sort(key=lambda x: x[0])
    kept = []
    last_t = -10.0
    for t, name in picked:
        if t - last_t < 2.0:
            continue
        if max_frames and len(kept) >= max_frames:
            break
        kept.append((t, name))
        last_t = t
    return kept


def build_timeline(frames, segs):
    """把关键帧和口播按时间轴咬合：每帧挂上「上一帧到本帧之间」说的话。"""
    rows = []
    prev_t = 0.0
    for i, (t, name) in enumerate(frames, start=1):
        spoken = [s for s in segs if prev_t <= s[0] < t]
        rows.append({
            "n": i,
            "t": round(t, 1),
            "frame": name,
            "speech": " ".join(s[2] for s in spoken),
        })
        prev_t = t
    tail = [s for s in segs if s[0] >= prev_t]
    if tail:
        rows.append({"n": len(rows) + 1, "t": round(prev_t, 1), "frame": None,
                     "speech": " ".join(s[2] for s in tail)})
    return rows


def write_outputs(ep_dir, src, duration, segs, timeline, model_size):
    (ep_dir / "transcript.txt").write_text(
        "\n".join("[%02d:%02d] %s" % (int(s // 60), int(s % 60), t) for s, _, t in segs),
        encoding="utf-8")
    (ep_dir / "segments.json").write_text(
        json.dumps([{"start": s, "end": e, "text": t} for s, e, t in segs],
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (ep_dir / "timeline.json").write_text(
        json.dumps({"source": str(src), "duration": duration, "model": model_size,
                    "frames": timeline}, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# %s" % src.stem, "",
          "- 源文件：`%s`" % src,
          "- 时长：%.1f 分钟" % (duration / 60),
          "- 关键帧：%d 张（`frames/`）" % len([r for r in timeline if r["frame"]]),
          "- 转写：%d 段" % len(segs), "",
          "## 分镜时间轴", "",
          "| 时间点 | 画面 | 这一段的口播 |", "|---|---|---|"]
    for row in timeline:
        stamp = "%02d:%02d" % (int(row["t"] // 60), int(row["t"] % 60))
        shot = "`%s`" % row["frame"] if row["frame"] else "—"
        md.append("| %s | %s | %s |" % (stamp, shot, row["speech"] or "（无口播）"))
    md += ["", "## 完整转写稿", ""]
    md += ["[%02d:%02d] %s" % (int(s // 60), int(s % 60), t) for s, _, t in segs]
    (ep_dir / "episode.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def one_file(src, out_root, args):
    ep_dir = out_root / re.sub(r"[^\w.-]+", "_", src.stem)[:60]
    ep_dir.mkdir(parents=True, exist_ok=True)
    print("==> %s" % src.name)

    duration = probe(src)
    print("    时长 %.1fs" % duration)

    segs = []
    if not args.only_frames:
        wav = ep_dir / "audio.wav"
        try:
            extract_audio(src, wav)
            segs = transcribe(wav, args.model)
            if not args.keep_audio:
                wav.unlink()
        except Exception as exc:
            print("    !! 音轨处理失败，只出画面：%s" % exc)

    frames = []
    if not args.only_audio:
        frames = extract_frames(src, ep_dir / "frames", args.scene, args.interval, args.max_frames)

    timeline = build_timeline(frames, segs)
    write_outputs(ep_dir, src, duration, segs, timeline, args.model)
    print("    -> %s" % (ep_dir / "episode.md"))
    return ep_dir


def main():
    ap = argparse.ArgumentParser(description="课程录屏取料：转写 + 关键帧 + 时间轴对齐")
    ap.add_argument("src", help="视频文件或目录")
    ap.add_argument("-o", "--out", default=None, help="输出根目录，默认 项目/output/course")
    ap.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large-v3"])
    ap.add_argument("--scene", type=float, default=0.25, help="场景变化阈值 0-1，越小抽帧越密；0 关闭")
    ap.add_argument("--interval", type=int, default=0, help="定距抽帧秒数，操作密集的课可设 20；0 关闭")
    ap.add_argument("--max-frames", type=int, default=150, help="单集关键帧上限")
    ap.add_argument("--only-frames", action="store_true", help="跳过 ASR 只抽帧")
    ap.add_argument("--only-audio", action="store_true", help="跳过抽帧只转写")
    ap.add_argument("--keep-audio", action="store_true", help="保留中间 wav")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    if src.is_dir():
        files = sorted(p for p in src.rglob("*") if p.suffix.lower() in VIDEO_EXT)
    elif src.is_file():
        files = [src]
    else:
        sys.exit("找不到输入：%s" % src)
    if not files:
        sys.exit("目录里没有视频文件：%s" % src)

    out_root = Path(args.out).expanduser() if args.out else \
        Path(__file__).resolve().parents[1] / "output" / "course"
    out_root.mkdir(parents=True, exist_ok=True)

    print("共 %d 个待处理，输出 -> %s" % (len(files), out_root))
    t0 = time.time()
    done = [one_file(f, out_root, args) for f in files]
    print("\n全部完成：%d 个，总耗时 %.1f 分钟" % (len(done), (time.time() - t0) / 60))
    for d in done:
        print("  %s" % (d / "episode.md"))


if __name__ == "__main__":
    main()
