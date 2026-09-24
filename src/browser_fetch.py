# browser_fetch.py — 浏览器路径拉取抖音作者视频列表（兜底）
#
# 背景（2026-09-21）：抖音 Argus 风控升级，纯 API 请求即使带完整 Cookie +
# A-Bogus 签名也被拦（Blocked by ArgusSecurityPlugin Signature Not Found）。
# 本模块改为驱动真实 Chromium 打开作者主页，让页面自己签名，从 DOM 抓视频 ID。
#
# 依赖：playwright-core（装在 managed node workspace）+ ms-playwright 缓存的 Chromium。
# 若两者缺失，browser_fetch_user_videos 返回空列表，调用方自动退回旧 API 路径。

import json
import os
import shutil
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _PROJECT_ROOT / "tools" / "douyin_browser_fetch.js"

_NODE_CANDIDATES = [
    "/Users/zhangyajun/.workbuddy/binaries/node/versions/22.12.0/bin/node",
    "/Users/zhangyajun/.workbuddy/binaries/node/versions/22.22.2-3/bin/node",
    shutil.which("node") or "",
]

_ENV = dict(os.environ)
_ENV["NODE_PATH"] = "/Users/zhangyajun/.workbuddy/binaries/node/workspace/node_modules"


def browser_fetch_user_videos(user_url: str, max_videos: int = 20, timeout: int = 90) -> list:
    """返回 aweme_id 列表（按主页展示顺序，最新在前）；失败返回 []。

    与 fetch_user_videos 的契约对齐：调用方负责后续去重/过滤/写 Excel。
    """
    if not _SCRIPT.exists():
        return []

    for node in _NODE_CANDIDATES:
        if not node or not os.path.exists(node):
            continue
        try:
            proc = subprocess.run(
                [node, str(_SCRIPT), "user", user_url, str(max_videos)],
                capture_output=True, text=True, timeout=timeout, env=_ENV,
                cwd=str(_PROJECT_ROOT),
            )
            # 取 stdout 最后一行 JSON（脚本可能输出调试信息）
            for line in reversed((proc.stdout or "").strip().splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("ok"):
                    return [str(i) for i in (data.get("ids") or [])]
                # 明确失败：打印原因便于排查，尝试下一个 node 候选
                print(f"[browser_fetch] {Path(node).name} 失败: {data.get('error', '')[:120]}", flush=True)
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[browser_fetch] {Path(node).name} 异常: {e}", flush=True)
            continue

    return []


def _run_node_json(args: list, timeout: int) -> dict:
    """跑 Node 脚本并解析最后一行 JSON 输出；失败返回 {}"""
    for node in _NODE_CANDIDATES:
        if not node or not os.path.exists(node):
            continue
        try:
            proc = subprocess.run(
                [node, str(_SCRIPT)] + args,
                capture_output=True, text=True, timeout=timeout, env=_ENV,
                cwd=str(_PROJECT_ROOT),
            )
            for line in reversed((proc.stdout or "").strip().splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("ok"):
                    return data
                print(f"[browser_fetch] {Path(node).name} 失败: {data.get('error', '')[:120]}", flush=True)
                break
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[browser_fetch] {Path(node).name} 异常: {e}", flush=True)
            continue
    return {}


def browser_fetch_video_detail(video_url: str, timeout: int = 90) -> dict:
    """抓单视频详情，结构与 douyin_parse 的 parser.parse_video 对齐；失败返回 {}。

    覆盖字段：aweme_id / desc / create_time / author_nickname / author_sec_uid /
    cover_url / nwm_url / video.duration / statistics.*（main.fetch_video_info 消费）
    """
    data = _run_node_json(["detail", video_url], timeout)
    info = data.get("info")
    return info if isinstance(info, dict) else {}
