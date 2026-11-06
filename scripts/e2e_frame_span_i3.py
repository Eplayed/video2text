# -*- coding: utf-8 -*-
"""T17：长素材抽帧必须覆盖全片，帧时间戳不能整体错后一格。"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import frame_pool  # noqa: E402


def seconds(stamp):
    minute, second = stamp.split(":")
    return int(minute) * 60 + int(second)


with tempfile.TemporaryDirectory(prefix="v2t-frame-span-") as d:
    root = Path(d)
    video = root / "long.mp4"
    subprocess.run([frame_pool._ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=32x32:r=1:d=90",
                    "-c:v", "mpeg4", str(video)], check=True)
    frames = frame_pool.extract(video, root / "raw", frame_pool.duration(video))
    assert len(frames) == frame_pool.CAP_FRAMES, len(frames)
    stamps = [seconds(stamp) for _, stamp in frames]
    assert stamps[0] == 0, stamps
    assert stamps[-1] >= 80, stamps
    assert all(next_stamp > stamp for stamp, next_stamp in zip(stamps, stamps[1:])), stamps
    assert all(Path(path).is_file() for path, _ in frames)
    print("PASS 90 秒素材 18 帧覆盖到末段，首帧时间戳为 00:00")

print("ALL_OK")
