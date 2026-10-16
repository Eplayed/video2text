#!/usr/bin/env python3
"""无人值守录屏：屏幕 + 系统声（静音路由）一起录，录完可直接接 course_analyze.py。

为什么路由到 Screenflick Loopback：
    系统输出切到该虚拟设备后，声音不再外放（整机静音，适合挂机/睡觉），
    同时它的输入口能拿到数字级干净音轨。实测 mean -35.7dB / max -10.7dB。
    退出时一定会把输出还原回原设备，异常退出也会走 atexit 还原。

用法：
    python3 scripts/course_record.py                       # 录到 Ctrl-C
    python3 scripts/course_record.py --max-minutes 240     # 最多 4 小时自动停
    python3 scripts/course_record.py --audio mic           # 改用内置麦克风录外放
    python3 scripts/course_record.py --analyze             # 停录后直接跑取料流水线

停止方式（三选一）：
    Ctrl-C / 创建 output/course/raw/STOP 文件 / 到达 --max-minutes
"""

import argparse
import atexit
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = "Screenflick Loopback"


def sh(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def list_av_devices():
    """返回 (video_devices, audio_devices)，形如 {名称: 索引}。"""
    _, out = sh(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", "dummy"])
    section = None
    video, audio = {}, {}
    for line in out.splitlines():
        if "video devices" in line:
            section = video
            continue
        if "audio devices" in line:
            section = audio
            continue
        if section is None:
            continue
        m = re.search(r"\[(\d+)\]\s+(.+)", line)
        if m:
            section[m.group(2).strip()] = m.group(1)
    return video, audio


def audio_source(name, prefer):
    """按偏好挑音频设备，返回 (设备索引, 说明)。"""
    _, audio = list_av_devices()
    if prefer == "mic":
        for key in audio:
            if "麦克风" in key or "Microphone" in key:
                return audio[key], "内置麦克风（注意：必须外放才录得到）"
    else:
        if LOOPBACK in audio:
            return audio[LOOPBACK], "Loopback 数字音轨（全程静音，推荐）"
        print("!! 没找到 %s，退回内置麦克风" % LOOPBACK)
        for key in audio:
            if "麦克风" in key or "Microphone" in key:
                return audio[key], "内置麦克风（必须外放）"
    if not audio:
        sys.exit("没有任何音频设备")
    first = next(iter(audio.values()))
    return first, "兜底设备"


def screen_source():
    """挑屏幕抓取设备，返回设备索引。"""
    video, _ = list_av_devices()
    for key in video:
        if "Capture screen" in key or "屏幕" in key:
            return video[key]
    sys.exit("找不到屏幕抓取设备，请检查「隐私与安全 -> 录屏与系统录音」里 Qoder CN 是否已授权")


def route_output(target):
    """切换系统输出设备，返回原设备名（供还原）。"""
    rc, cur = sh(["SwitchAudioSource", "-c", "-t", "output"])
    if rc != 0:
        print("!! SwitchAudioSource 不可用，跳过静音路由（会外放）")
        return None
    original = cur.strip()
    rc, out = sh(["SwitchAudioSource", "-s", target, "-t", "output"])
    if rc != 0:
        print("!! 切到 %s 失败：%s" % (target, out.strip()))
        return None
    print("   系统输出 -> %s（原：%s）" % (target, original))
    return original


def restore_output(name):
    if not name:
        return
    rc, out = sh(["SwitchAudioSource", "-s", name, "-t", "output"])
    print("   系统输出已还原 -> %s %s" % (name, "" if rc == 0 else "(失败: %s)" % out.strip()))


def main():
    ap = argparse.ArgumentParser(description="无人值守录制屏幕+系统声")
    ap.add_argument("-o", "--out", default=str(PROJECT_ROOT / "output" / "course" / "raw"))
    ap.add_argument("--name", default=None, help="输出文件名，默认 REC_<时间戳>.mp4")
    ap.add_argument("--fps", type=int, default=15, help="帧率，教程类 15 足够且省资源")
    ap.add_argument("--audio", default="loopback", choices=["loopback", "mic"], help="音源")
    ap.add_argument("--max-minutes", type=float, default=300, help="最长录制分钟数，到点自动停")
    ap.add_argument("--no-sleep", action="store_true", help="不做静音路由（会外放，自己戴耳机就别开）")
    ap.add_argument("--analyze", action="store_true", help="停录后立刻跑 course_analyze.py")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_file = out_dir / "STOP"
    if stop_file.exists():
        stop_file.unlink()

    vid = screen_source()
    aid, note = audio_source(args.audio, args.audio)
    print("屏幕设备 #%s | 音频设备 #%s（%s）" % (vid, aid, note))

    original_output = None
    if args.audio == "loopback" and not args.no_sleep:
        original_output = route_output(LOOPBACK)
    atexit.register(restore_output, original_output)

    dest = out_dir / (args.name or ("REC_%s.mp4" % datetime.now().strftime("%Y%m%d_%H%M%S")))
    # avfoundation 的输入格式是 "<视频索引>:<音频索引>"
    cmd = ["caffeinate", "-dis",
           "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
           "-f", "avfoundation", "-framerate", str(args.fps), "-i", "%s:%s" % (vid, aid),
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]

    print("开始录制 -> %s" % dest)
    print("停止：Ctrl-C，或 touch %s，或等 %g 分钟" % (stop_file, args.max_minutes))
    proc = subprocess.Popen(cmd)

    t0 = time.time()
    reason = "手动"
    try:
        while proc.poll() is None:
            elapsed = time.time() - t0
            if stop_file.exists():
                reason = "STOP 文件"
                break
            if elapsed > args.max_minutes * 60:
                reason = "到达时长上限"
                break
            if int(elapsed) % 300 < 1:
                sz = dest.stat().st_size / 1e6 if dest.exists() else 0
                print("   [%s] 已录 %.0f 分钟，%.0f MB" % (datetime.now().strftime("%H:%M:%S"), elapsed / 60, sz))
            time.sleep(1)
    except KeyboardInterrupt:
        reason = "Ctrl-C"

    print("停止录制（%s），正在收尾…" % reason)
    proc.send_signal(signal.SIGINT)   # 给 SIGINT 才会让 ffmpeg 正常写完 moov atom
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    restore_output(original_output)
    atexit.unregister(restore_output)

    if not dest.exists() or dest.stat().st_size < 10000:
        sys.exit("产物异常（太小或不存在），检查录屏/麦克风权限")
    print("完成：%s  %.1f MB  时长约 %.1f 分钟" % (dest, dest.stat().st_size / 1e6, (time.time() - t0) / 60))

    if args.analyze:
        print("\n接跑取料流水线…")
        subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "course_analyze.py"), str(dest)])


if __name__ == "__main__":
    main()
