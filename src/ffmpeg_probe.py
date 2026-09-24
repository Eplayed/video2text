"""ffmpeg / ffprobe 可执行文件探测（带真实执行校验）。

为什么不能只用 shutil.which()：两类环境坑都在本机实测过。

  坑 1  PATH 不含 /usr/local/bin（WorkBuddy 沙盒 bash、launchd 精简环境）
        → which() 返回 None，裸命令名 FileNotFoundError，抽帧/ASR 静默失败。
        这是 2026-09-21 修过的那一类。

  坑 2  PATH 里的 ffmpeg 是坏的（TRAE SOLO 沙箱注入的二进制，架构不匹配）
        → which() 命中、路径存在、文件名正确，但执行报
          "[Errno 8] Exec format error"。
        这一类比坑 1 更难查：2026-09-24 实测，ASR 全流程只在这一步失败，
        报错信息看不出是二进制本身的问题。

所以：候选路径逐个真跑一次 `-version`，只认退出码为 0 的那个。

用法：
    from src.ffmpeg_probe import find_ffmpeg, find_ffprobe
    FFMPEG_BIN = find_ffmpeg()        # 找不到返回 None，不抛异常

显式指定（探测结果不满意时）：
    FFMPEG_BIN=/abs/path/to/ffmpeg FFPROBE_BIN=/abs/path/to/ffprobe

调试（打印每个候选的判定过程）：
    V2T_FFMPEG_DEBUG=1 python main.py ...
"""

import os
import shutil
import subprocess
import sys

# 已知安装位置。本机实测 /usr/local/bin/ffmpeg 为 Homebrew 8.1.1，可用。
_KNOWN_DIRS = (
    "/usr/local/bin",       # Homebrew on Intel
    "/opt/homebrew/bin",    # Homebrew on Apple Silicon
    "/usr/bin",
    "/opt/local/bin",       # MacPorts
)

_PROBE_TIMEOUT = 20         # 秒；坏二进制可能挂住，必须设超时
_cache = {}


def _debug(msg):
    if os.environ.get("V2T_FFMPEG_DEBUG"):
        print("[ffmpeg_probe] %s" % msg, file=sys.stderr, flush=True)


def _is_executable(path):
    """真跑一次 `-version`；只有退出码 0 才算可用（挡掉坏二进制）。"""
    if not path:
        return False
    if not os.path.exists(path):
        _debug("skip 文件不存在: %s" % path)
        return False
    try:
        proc = subprocess.run(
            [path, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_PROBE_TIMEOUT,
        )
    except Exception as exc:
        # Exec format error / Permission denied / 超时 都落到这里
        _debug("skip 无法执行: %s (%s: %s)" % (path, type(exc).__name__, exc))
        return False
    if proc.returncode == 0:
        _debug("ok   可用: %s" % path)
        return True
    _debug("skip 退出码 %s: %s" % (proc.returncode, path))
    return False


def _detect(tool):
    """按 环境变量 → PATH → 已知目录 顺序探测，返回第一个可执行路径或 None。"""
    if tool in _cache:
        return _cache[tool]

    candidates = []

    env_path = os.environ.get("%s_BIN" % tool.upper(), "").strip()
    if env_path:
        candidates.append(env_path)

    found = shutil.which(tool)
    if found:
        candidates.append(found)

    for known in _KNOWN_DIRS:
        candidates.append(os.path.join(known, tool))

    for cand in candidates:
        if _is_executable(cand):
            _cache[tool] = cand
            return cand

    _debug("未找到可用的 %s，候选：%s" % (tool, ", ".join(candidates)))
    _cache[tool] = None
    return None


def find_ffmpeg():
    """返回可执行的 ffmpeg 绝对路径；找不到返回 None。"""
    return _detect("ffmpeg")


def find_ffprobe():
    """返回可执行的 ffprobe 绝对路径；找不到返回 None。"""
    return _detect("ffprobe")


def require(tool="ffmpeg"):
    """返回可执行路径；找不到时抛出带排查指引的 RuntimeError。"""
    path = find_ffprobe() if tool == "ffprobe" else find_ffmpeg()
    if not path:
        raise RuntimeError(
            "%s 不可用：PATH 与 %s 下均未找到能执行的 %s。\n"
            "排查顺序：\n"
            "  1) brew install ffmpeg\n"
            "  2) 确认二进制架构与本机匹配 —— AI 工具沙箱注入的 ffmpeg\n"
            "     会报 Exec format error，此时 which(%s) 有值但不可用\n"
            "  3) 显式指定：%s_BIN=/abs/path/to/%s\n"
            "  4) 看探测细节：V2T_FFMPEG_DEBUG=1"
            % (tool, " / ".join(_KNOWN_DIRS), tool, tool, tool.upper(), tool)
        )
    return path
