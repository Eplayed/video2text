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
import tempfile
import threading
import time
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


def browser_fetch_users_batch(
    user_items: list,
    max_videos: int = 20,
    timeout: int = 75,
    on_progress=None,
) -> dict:
    """批量拉取多个作者主页：一次 Chromium 启动跑完所有作者。

    订阅同步主路径——替代逐作者调 browser_fetch_user_videos（每次冷启动
    Chromium 5-15 秒，无新视频也要付这笔成本）。

    参数：
        user_items: [{"id": str, "url": str}]，id 用于回传结果对账
        max_videos: 每作者最多取多少条
        timeout: 单作者秒上限（看门狗按 timeout*len+30 兜底杀进程）
        on_progress: 回调 (done, total, item_id, ids, error)，每完成一个作者触发

    返回：{id: [aweme_id, ...]}，失败的作者不在结果里。
    """
    if not user_items or not _SCRIPT.exists():
        return {}

    node = next((n for n in _NODE_CANDIDATES if n and os.path.exists(n)), "")
    if not node:
        return {}

    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(user_items, tmp, ensure_ascii=False)
        tmp.close()

        results: dict = {}
        total = len(user_items)
        done = 0
        proc = subprocess.Popen(
            [node, str(_SCRIPT), "users", tmp.name, str(max_videos)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=_ENV, cwd=str(_PROJECT_ROOT),
        )
        # 看门狗：页面级超时已由脚本内部控制，此处只兜底进程级挂死
        killer = threading.Timer(timeout * total + 30, proc.kill)
        killer.start()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                item_id = str(data.get("id", ""))
                done += 1
                if data.get("ok") and data.get("ids"):
                    results[item_id] = [str(i) for i in data["ids"]]
                    if on_progress:
                        on_progress(done, total, item_id, results[item_id], "")
                elif on_progress:
                    on_progress(done, total, item_id, None,
                               str(data.get("error", ""))[:120])
        finally:
            killer.cancel()
            proc.stdout.close()
            proc.wait()
        return results
    except Exception as e:
        print(f"[browser_fetch] 批量拉取异常: {e}", flush=True)
        return {}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
