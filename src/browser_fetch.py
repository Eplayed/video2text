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

# 主页列表只有一份来源算数：页面自己发的 aweme/post 接口。
DOM_FALLBACK_NOTE = "列表来自 DOM 兜底（侧栏推荐位混在里面），这轮不算"


def verified_ids(data: dict) -> list:
    """脚本回传的 ids 里，只认接口那一份；DOM 兜底那份一律不当这个号的新视频。

    **Why:** `a[href*="/video/"]` 刮的是整页锚点，侧栏推荐位也是 /video/ 链接。
    2026-10-16 实测过一次（笨小孩 15 条里 8 条是别人的），2026-11-01 同一件事复发：
    订阅「尤安的魔兽世界」进来 7 条全是别人的老片子（荔枝新闻 2026、黑洞科普 2022…），
    每条白付一次下载 + ASR，还被盖上尤安的 author_sec_uid 进了素材库。
    DOM 那份只能证明"这个链接在页面上出现过"，证明不了"是这个号发的"。

    返回空不代表这个号没更新——调用方按"没覆盖到"处理，退回按 sec_uid 取列表的旧 API
    （那条路作者是锁死的），并把 DOM_UNTRUSTED 记在回参里，界面与日志要说清为什么慢。
    """
    ids = [str(i) for i in (data.get("ids") or [])]
    if ids and data.get("ids_from") != "api":
        return []
    return ids


def ids_from_dom_only(data: dict) -> bool:
    """这一轮是不是"只拿到 DOM 那份"（判据与 verified_ids 同一处，别两边各写一遍）。"""
    return bool(data.get("ids")) and data.get("ids_from") != "api"


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
                    ids = verified_ids(data)
                    if not ids and data.get("ids"):
                        print(f"[browser_fetch] {DOM_FALLBACK_NOTE}，退回旧 API", flush=True)
                    return ids
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
    notes_out: dict = None,
) -> dict:
    """批量拉取多个作者主页：一次 Chromium 启动跑完所有作者。

    订阅同步主路径——替代逐作者调 browser_fetch_user_videos（每次冷启动
    Chromium 5-15 秒，无新视频也要付这笔成本）。

    参数：
        user_items: [{"id": str, "url": str}]，id 用于回传结果对账
        max_videos: 每作者最多取多少条
        timeout: 单作者秒上限（看门狗按 timeout*len+30 兜底杀进程）
        on_progress: 回调 (done, total, item_id, ids, error)，每完成一个作者触发
        notes_out: 传一个 dict 就往里填 {作者id: [图文条目]}（aweme_type=68）。
            返回值刻意不变：调用方一堆按 ids 走的老逻辑不该被这次加字段牵连；
            纯图文的号 ids 是空列表，但 notes 有货，仍然算这个作者成功。

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
                ids = verified_ids(data)
                notes = data.get("notes") or []
                if data.get("ok") and ids_from_dom_only(data) and not notes:
                    # 只有 DOM 那一份：不写进结果，让调用方退回按 sec_uid 取的旧 API
                    # 这行日志是留给下次的证据——11-01 那次只能靠"这批 id 不可能是本人的"反推
                    print("[browser_fetch] 作者 %s %s，退回旧 API" % (item_id, DOM_FALLBACK_NOTE),
                          flush=True)
                    if on_progress:
                        on_progress(done, total, item_id, None, DOM_FALLBACK_NOTE)
                    continue
                if data.get("ok") and (ids or notes):
                    results[item_id] = ids
                    if notes_out is not None:
                        notes_out[item_id] = notes
                    if on_progress:
                        on_progress(done, total, item_id, ids, "")
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


def browser_fetch_author(work_url: str, timeout: int = 150) -> dict:
    """作品链接（视频 /video/、图文 /note/、/slides/）→ 作者主页 sec_uid 与昵称。

    为什么必须走浏览器：图文作品的分享链展开后是 douyin.com/note/<id>，链接里没有
    sec_uid，纯 requests 这条路无解；抖音 App 分享文案又是号主的常态输入。
    Node 侧会先取作品页预取的 sec_user_id（再补 DOM 里的 /user/ 锚点，排掉导航栏
    那个 /user/self 即登录者本人），然后**开一次候选主页**确认这条作品真的在列表里
    才算认对——侧栏推荐位也是 /user/ 链接，光取第一个会把订阅接到别人号上。

    成功：{"ok":true,"sec_uid":...,"nickname":...,"work_id":...,"confirmed":bool,
    "counts":{"video":n,"note":m}}；失败返回 {}（调用方负责给出可执行的下一步提示）。
    """
    data = _run_node_json(["author", work_url], timeout)
    return data if isinstance(data, dict) and data.get("ok") else {}
