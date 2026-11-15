"""B 站（bilibili）UP 主投稿采集。

链路：订阅 UP 主空间 → 拉投稿列表（增量）→ 逐条取视频详情拿 cid → 取 CC/AI 字幕当转写
→ 返回结构化 dict，交给 content_store.insert_bilibili_videos 落 videos 表
（source_sheet='B站视频'，DB 直写、不进 Excel，与微信文章同一套落点）。

风控现实（2026-10-08 本机两轮实测，写在这里免得下一个人重踩）：
  匿名可用   x/web-interface/view（单视频详情，元数据全靠它）、x/player/v2
  匿名被拦   x/space/wbi/arc/search（投稿列表）→ HTTP 412；x/web-interface/card（UP 主资料）
             时好时坏，同一请求一会儿 code=0 一会儿 code=-352，不能当依赖
  带 cookie 仍被拦   投稿列表换成有效登录 cookie（nav 接口 isLogin=True、Lv5）后，
             从 requests 打过去稳定回 code=-403「访问权限不足」（HTTP 200、响应头
             bili-status-code:-403）。照抄真实浏览器那一条请求的全部参数与 dm_img_* 指纹
             仍然 -403，换任何 mid 都一样 —— 闸在握手层：浏览器要先过
             bapis/…/GenWebTicket + gaia-gateway/ExClimbCongLing 才拿得到列表。
  ⇒ 结论：投稿清单走 yt-dlp --cookies（它内部处理了这套握手），其余接口继续直连。
    cookie 从 config/config.env.local 或 config/config.env 读 BILIBILI_COOKIE 整段
    cookie 头（与 toutiao_stats.load_cookie 同构），必须含 SESSDATA。

字幕口径：优先人工 CC 轨，其次 AI 轨（lan 以 'ai-' 开头）；两条都没有就按调用方约定留空，
并把 subs_status 标成「无字幕」，此时正文回落视频简介（≥40 字才用，标「无字幕（仅简介）」）。
实测对照：于谦 BV1dYaJ6PE2z 我们判「无字幕」，yt-dlp --list-subs 也只列出 danmaku，两边一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_CANDIDATES = (
    ROOT / "config" / "config.env.local",
    ROOT / "config" / "config.env",
)

API = "https://api.bilibili.com"
VIDEO_URL = "https://www.bilibili.com/video/%s"
SPACE_URL = "https://space.bilibili.com/%s/video"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
PAGE_SIZE = 30
MAX_PAGES = 10
REQUEST_INTERVAL = 1.2       # 秒；连打列表/详情/字幕最容易触发风控，串行加间隔
TIMEOUT = 20
YTDLP_BIN = "yt-dlp"         # 投稿清单的唯一可用来源，见 list_bvids 上方说明
YTDLP_TIMEOUT = 180
# 为什么不能只 shutil.which("yt-dlp")：工作台是被脚本拉起来的，实测它的 PATH 只有
# ~/.qoder-cn/bin 那几个目录，而 pip 装的控制台脚本在 ~/Library/Python/3.9/bin，
# 于是 which 找不到、B 站采集在界面上表现成"清单取不到"，而人在终端里手跑却一切正常。
# 所以按三条路兜底，最后一条直接用当前解释器跑模块——pip 装过就一定在。
YTDLP_FALLBACKS = [
    os.path.expanduser("~/Library/Python/3.9/bin/yt-dlp"),
    "/usr/local/bin/yt-dlp",
    "/opt/homebrew/bin/yt-dlp",
]


def ytdlp_cmd() -> list:
    """调用 yt-dlp 的命令前缀（列表）；三种途径都没有才返回 []。"""
    found = shutil.which(YTDLP_BIN)
    if found:
        return [found]
    for p in YTDLP_FALLBACKS:
        if os.path.exists(p):
            return [p]
    try:
        import yt_dlp  # noqa: F401 —— 这里只验证模块装没装，不调用它
        return [(sys.executable or "python3"), "-m", "yt_dlp"]
    except ImportError:
        return []

# 风控与登录态相关的返回码，必须分开报——"cookie 过期"和"被风控"用户的处理动作完全不同
RISK_CODES = (-352, -403, -509)
LOGIN_CODES = (-101, -104)

# wbi 签名混淆表（来自 B 站前端 JS）。表里有大于 63 的下标，JS 取越界得到 undefined 会被跳过，
# Python 必须同样跳过——照抄不判界会直接 IndexError。
MIXIN_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 67, 11,
    44, 65, 20, 31, 38, 35, 38, 48, 42, 48, 42, 48, 19, 18, 43, 51,
    16, 15, 54, 43, 15, 44, 50, 57, 51, 55, 59, 54, 53, 16, 53, 55,
)

_wbi_cache = {"key": "", "ts": 0.0}


# ── cookie ──

def load_cookie() -> tuple:
    # type: () -> tuple
    """返回 (cookie 文本, 来源说明)。两处都没有就抛错，让人去配，不要静默抓到空列表。"""
    for path in CONFIG_CANDIDATES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("BILIBILI_COOKIE") or "=" not in line:
                continue
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if "SESSDATA" in val and len(val) > 60:
                return val, "config/%s:BILIBILI_COOKIE" % path.name
    raise RuntimeError(
        "没找到 B 站 cookie：浏览器登录 bilibili.com 后，在 DevTools 里任选一个 "
        "api.bilibili.com 请求，把整段 cookie 头复制进 config/config.env.local 新起一行 "
        "BILIBILI_COOKIE=（必须包含 SESSDATA；投稿列表接口匿名访问会被风控 412/-352 拦死，没有绕过办法）"
    )


def parse_mid(raw: str) -> str:
    """从用户输入提取 mid（数字空间号）。支持纯数字、space.bilibili.com 链接、带 /video 后缀。

    位数上限给到 19：B 站新账号的 mid 已经是 16 位（如 3546944530025226），
    早先写 {2,12} 会把这种号**截断**成前 12 位，表现为订阅建得起来但昵称永远取不到、
    同步永远 0 条——查的人只会去怀疑 cookie，实际是正则吃掉了位数。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{2,19}", s):
        return s
    m = re.search(r"space\.bilibili\.com/?(\d{2,19})", s)
    if m:
        return m.group(1)
    return ""


# ── HTTP ──

def _headers(cookie: str, referer: str = "") -> dict:
    head = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if cookie:
        head["Cookie"] = cookie
    head["Referer"] = referer or "https://www.bilibili.com/"
    return head


def _get(url: str, cookie: str = "", referer: str = "", params=None) -> dict:
    """GET 并解析 JSON；HTTP 412 与业务 code 都归一化成 error 字段，不抛裸异常。"""
    resp = requests.get(url, params=params, headers=_headers(cookie, referer), timeout=TIMEOUT)
    if resp.status_code == 412:
        return {"code": -412, "message": "Precondition Failed", "data": None, "risk": True}
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"code": -1, "message": "返回不是 JSON（可能被网关拦截）", "data": None, "risk": True}


def _fail_reason(code: Any, message: str = "") -> str:
    """把返回码翻译成用户能照做的中文，不要让界面只显示一个 -352。"""
    try:
        num = int(code)
    except (TypeError, ValueError):
        return ""
    if num == 0:
        return ""
    if num == -412 or num in RISK_CODES:
        return ("B 站风控拦截（code=%s）：稍后再试；连续出现就降低同步频率，"
                "或在浏览器里重新登录一次再复制 cookie" % num)
    if num in LOGIN_CODES:
        return "B 站 cookie 已失效或未登录（code=%s）：重新复制含 SESSDATA 的 cookie 到 config/config.env.local" % num
    return "B 站接口返回 code=%s %s" % (num, (message or "").strip()[:40])


# ── wbi 签名 ──

def _mixin_key(cookie: str = "") -> str:
    # type: (str) -> str
    """从 x/web-interface/nav 的 wbi_img 两个 CDN 文件名算混淆 key，缓存 10 分钟。"""
    now = time.time()
    if _wbi_cache["key"] and now - _wbi_cache["ts"] < 600:
        return _wbi_cache["key"]
    data = _get(API + "/x/web-interface/nav", cookie=cookie)
    # 匿名调用 code=-101（未登录）但 wbi_img 照样返回，这里不能因为 -101 就放弃取 key
    wbi = ((data.get("data") or {}).get("wbi_img") or {})
    img = wbi.get("img_url") or ""
    sub = wbi.get("sub_url") or ""
    if not img or not sub:
        raise RuntimeError("拿不到 wbi 密钥（nav 接口异常），稍后重试")
    raw = img.rsplit("/", 1)[-1].split(".", 1)[0] + sub.rsplit("/", 1)[-1].split(".", 1)[0]
    key = "".join(raw[i] for i in MIXIN_TAB if i < len(raw))[:32]
    _wbi_cache.update(key=key, ts=now)
    return key


def _signed(params: dict, cookie: str = "") -> dict:
    """补 wts 与 w_rid。

    必须对 **urlencode 之后**的串算 md5，不能拿原始值拼：dm_img_inter 这类参数含
    `{ } " : , [ ]`，requests 发出去时会被百分号转义，签名串与实发串一旦不一致，
    B 站直接回 code=-403（表现得像 cookie 坏了，其实是签名错）。参数还要按 key 排序。
    """
    out = dict(params)
    out["wts"] = str(int(time.time()))
    key = _mixin_key(cookie)
    query = urllib.parse.urlencode(sorted(out.items()))
    out["w_rid"] = hashlib.md5((query + key).encode("utf-8")).hexdigest()
    return out


# ── UP 主资料与投稿列表 ──

def fetch_up_info(mid: str) -> dict:
    """UP 主昵称/粉丝/投稿数。这条接口匿名可用，添加订阅时就能带出作者名。"""
    time.sleep(REQUEST_INTERVAL)
    data = _get(API + "/x/web-interface/card", params={"mid": mid})
    reason = _fail_reason(data.get("code"), data.get("message", ""))
    card = data.get("data") or {}
    return {
        "name": ((card.get("card") or {}).get("name") or "").strip(),
        "fans": card.get("follower"),
        "archive_count": (card.get("card") or {}).get("archive_count"),
        "error": reason,
    }


def _to_seconds(raw: Any) -> int:
    """列表接口给 "12:34"/"1:02:03" 或纯数字秒，统一成秒；解析失败给 0。"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


def _pick_video_list(data: dict) -> list:
    """投稿列表在 vlist（现行）或 bvideo（旧版），两个都认。"""
    lst = (data.get("list") or {}) if data else {}
    return lst.get("vlist") or lst.get("bvideo") or []


# ── 投稿清单：走 yt-dlp（2026-10-08 实测唯一能过风控的轻量路径） ──
#
# 为什么不用 requests 直连 x/space/wbi/arc/search：带正确 wbi 签名 + 完整 cookie +
# 照抄浏览器的 dm_img_* 指纹参数，仍然稳定回 code=-403「访问权限不足」（HTTP 200、
# 响应头 bili-status-code:-403）。同一时刻真实 Chrome 的首发请求也是 -403，
# 它靠 bapis/GenWebTicket + gaia-gateway/ExClimbCongLing 那道验证自愈后才拿到列表。
# 换任何 mid 都一样，所以不是 cookie 坏了（nav 接口 isLogin=True、昵称等级都正常）。
# yt-dlp 内部处理了这套握手，--cookies 传进去就能出 bvid 清单；它 --flat-playlist 只给
# id 和 url，所以标题/时长/播放量/封面仍要用 x/web-interface/view 逐条补（那条匿名可用）。

def _write_cookie_file(cookie: str) -> str:
    """把整段 cookie 头写成 Netscape 格式临时文件（0600），调用方负责删。"""
    fd, path = tempfile.mkstemp(prefix="bili_ck_", suffix=".txt")
    os.chmod(path, 0o600)
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        key, val = pair.split("=", 1)
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "TRUE", "1893456000",
                                key.strip(), val.strip()]))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def list_bvids(mid: str, cookie: str = "", cap: int = 60) -> dict:
    """按最新发布顺序拿投稿 bvid。返回 {"bvids": [...], "error": ""}。"""
    exe = ytdlp_cmd()
    if not exe:
        return {"bvids": [], "error":
                "本机没有 yt-dlp，B 站投稿清单拿不到（直连接口必被 -403 风控）。"
                "装一下：pip3 install yt-dlp"}
    if not cookie:
        return {"bvids": [], "error": "B 站清单需要 cookie，先在 config/config.env.local 配 BILIBILI_COOKIE="}
    ck_path = _write_cookie_file(cookie)
    try:
        cmd = exe + ["--no-warnings", "--quiet", "--flat-playlist", "--cookies", ck_path,
                     "--playlist-end", str(int(cap)), "--print", "%(id)s", SPACE_URL % mid]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            why = err[-1][:160] if err else "退出码 %s" % proc.returncode
            if "Login" in why or "登录" in why or "412" in why or "blocked" in why:
                return {"bvids": [], "error": "B 站清单被拦（cookie 可能已失效）：%s" % why}
            return {"bvids": [], "error": "yt-dlp 取投稿清单失败：%s" % why}
        bvids = [ln.strip() for ln in proc.stdout.splitlines()
                 if re.match(r"^(BV|av)[A-Za-z0-9]*$", ln.strip())]
        return {"bvids": bvids, "error": ""}
    except subprocess.TimeoutExpired:
        return {"bvids": [], "error": "yt-dlp 取投稿清单超时（%ds）" % YTDLP_TIMEOUT}
    except Exception as e:
        return {"bvids": [], "error": "yt-dlp 调用异常: %s" % e}
    finally:
        try:
            os.unlink(ck_path)
        except OSError:
            pass


def fetch_user_videos(mid: str, known_bvids=None, limit: int = 20, cookie: str = "") -> dict:
    # type: (str, set, int, str) -> dict
    """增量拉 UP 主投稿：yt-dlp 出 bvid 清单（能过风控），逐条用 view 接口补元数据。

    返回 {"videos": [...], "hit_known_page": bool, "author": "", "error": ""}
    每条含 bvid/title/url/cover_url/published_at/duration_sec/view_count。
    字幕（transcript/subs_status）由 sync_bilibili_user 再补，这里不碰。
    """
    known = known_bvids or set()
    listed = list_bvids(mid, cookie=cookie, cap=max(limit * 3, 60))
    if listed["error"]:
        return {"videos": [], "hit_known_page": False, "author": "", "error": listed["error"]}

    collected = []
    author = ""
    hit_known = False
    for bvid in listed["bvids"]:
        if bvid in known:
            continue
        detail = fetch_video_detail(bvid, cookie=cookie)
        if detail["error"]:
            # 单条详情失败不拖垮整轮：跳过这条，下一轮还会再试（没入库就不算数）
            continue
        author = author or detail.get("owner") or ""
        collected.append({
            "bvid": bvid,
            "title": detail.get("title") or "",
            "url": VIDEO_URL % bvid,
            "cover_url": detail.get("pic") or "",
            "published_at": detail.get("published_at") or "",
            "duration_sec": detail.get("duration_sec") or 0,
            "view_count": detail.get("view_count") or 0,
            # 往下直接复用，免得 sync 再打一次 view（每条素材两次同接口请求没必要）
            "cid": detail.get("cid"),
            "parts": detail.get("parts") or 1,
            "desc": detail.get("desc") or "",
        })
        if len(collected) >= limit:
            break
    if not collected and listed["bvids"]:
        hit_known = True          # 翻出来的全是已入库的，说明没有新稿
    return {"videos": collected, "hit_known_page": hit_known, "author": author, "error": ""}


# ── 单视频详情与字幕 ──

def fetch_video_detail(bvid: str, cookie: str = "") -> dict:
    """x/web-interface/view：标题/封面/发布日期/UP 主/cid（字幕接口要用）/简介。匿名可用。

    这条接口是 B 站投稿元数据的实际来源——清单只给 bvid，其余全靠它。
    owner.name 也从这里取：x/web-interface/card 现在同样会被风控 -352。
    """
    time.sleep(REQUEST_INTERVAL)
    data = _get(API + "/x/web-interface/view", cookie=cookie,
                referer=VIDEO_URL % bvid, params={"bvid": bvid})
    reason = _fail_reason(data.get("code"), data.get("message", ""))
    v = data.get("data") or {}
    pub = int(v.get("pubdate") or 0)
    return {
        "cid": v.get("cid"),
        "aid": v.get("aid"),
        "title": (v.get("title") or "").strip(),
        "pic": re.sub(r"^http://", "https://", v.get("pic") or ""),
        "published_at": time.strftime("%Y-%m-%d", time.localtime(pub)) if pub else "",
        "owner": ((v.get("owner") or {}).get("name") or "").strip(),
        "owner_mid": str((v.get("owner") or {}).get("mid") or ""),
        "desc": (v.get("desc") or "").strip(),
        "duration_sec": int(v.get("duration") or 0),
        "view_count": int((v.get("stat") or {}).get("view") or 0),
        "parts": len(v.get("pages") or []),
        "error": reason,
    }


def _subtitle_text(bvid: str, cid: Any, cookie: str = "") -> dict:
    """取字幕轨正文。优先人工 CC，其次 AI 轨；分 P 视频只取第一 P（cid 由详情接口给）。"""
    time.sleep(REQUEST_INTERVAL)
    params = _signed({"bvid": bvid, "cid": str(cid or ""), "platform": "web",
                      "web_location": "1315670"}, cookie)
    data = _get(API + "/x/player/wbi/v2", cookie=cookie,
                referer=VIDEO_URL % bvid, params=params)
    reason = _fail_reason(data.get("code"), data.get("message", ""))
    if reason:
        return {"text": "", "status": "字幕取回失败", "note": reason}
    subs = ((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return {"text": "", "status": "无字幕", "note": ""}
    # sub_type 0=人工字幕；lan 以 ai- 开头的是 B 站自动生成
    subs = sorted(subs, key=lambda s: 0 if not str(s.get("lan", "")).startswith("ai-") else 1)
    chosen = subs[0]
    url = chosen.get("subtitle_url") or ""
    if not url:
        return {"text": "", "status": "无字幕", "note": ""}
    if url.startswith("//"):
        url = "https:" + url
    resp = requests.get(url, headers=_headers(cookie, VIDEO_URL % bvid), timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    lines = [str(b.get("content") or "").strip() for b in (payload.get("body") or [])]
    text = "\n".join([ln for ln in lines if ln]).strip()
    if not text:
        return {"text": "", "status": "字幕取回失败", "note": "字幕文件是空的"}
    is_ai = str(chosen.get("lan", "")).startswith("ai-")
    return {"text": text, "status": "AI字幕" if is_ai else "字幕", "note": ""}


def _clean_transcript(text: str, cap: int = 6000) -> str:
    """字幕行去重复语气词、压空行；B 站 AI 字幕常整段重复同一句。"""
    if not text:
        return ""
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln or ln in ("，", "。", "…", "..."):
            continue
        if lines and lines[-1] == ln:
            continue
        lines.append(ln)
    out = "\n".join(lines)
    return out[:cap]


# ── 主同步入口（与 wechat_fetcher.sync_wechat_feed 同构） ──

def sync_bilibili_user(
    mid: str,
    known_bvids=None,
    limit: int = 20,
    with_subtitles: bool = True,
    cookie: str = "",
) -> dict:
    """拉某 UP 主的新投稿并补齐字幕，返回待入库记录。

    返回 {"videos":[{aweme_id,url,title,published_at,transcript,cover_url,
                     duration_sec,view_count,subs_status}], "author":"", "error":"", "note":""}
    author 是从稿件 owner 带出来的昵称（订阅记录里没有时给调用方回填用）。
    元数据（标题/封面/时长/播放/cid）已在 fetch_user_videos 里取过一次 view，
    这里只补字幕，不再重复打同一个接口。
    """
    empty = {"videos": [], "author": "", "error": "", "note": ""}
    try:
        listing = fetch_user_videos(mid, known_bvids=known_bvids, limit=limit, cookie=cookie)
    except requests.RequestException as e:
        return dict(empty, error="B 站列表请求失败: %s" % e)

    if listing["error"]:
        return dict(empty, error=listing["error"])

    notes = ["已翻到存量页，无更多新投稿"] if listing["hit_known_page"] else []
    out = []
    for item in listing["videos"]:
        rec = {
            "aweme_id": item["bvid"],
            "url": item["url"],
            "title": item["title"],
            "cover_url": item["cover_url"],
            "published_at": item["published_at"],
            "duration_sec": item["duration_sec"],
            "view_count": item["view_count"],
            "transcript": "",
            "subs_status": "未取字幕",
        }
        if item.get("parts", 1) > 1:
            rec["subs_status"] = "分P视频(仅首P)"
        if with_subtitles and item.get("cid"):
            got = _subtitle_text(item["bvid"], item["cid"], cookie=cookie)
            rec["transcript"] = _clean_transcript(got["text"])
            rec["subs_status"] = got["status"]
            if got["note"]:
                notes.append("%s %s" % (item["bvid"], got["note"]))
            elif not rec["transcript"] and len(item.get("desc") or "") >= 40:
                # 无字幕时退回简介：至少让选题雷达与选题包有一段能看的文字，
                # 且状态标清楚是「仅简介」，不冒充转写。
                rec["transcript"] = "简介：" + item["desc"]
                rec["subs_status"] = "无字幕（仅简介）"
        out.append(rec)

    return {"videos": out, "author": listing.get("author") or "",
            "error": "", "note": "；".join(notes)}


# ── 合集（ugc_season）整档入库 ────────────────────────────────────
#
# 为什么要有这一条：订阅同步只翻 UP 主投稿列表、每轮 20 条（BILI_SYNC_LIMIT），
# 而「好赛雷切片」这种把内容全塞进合集的号，投稿列表里根本看不到合集那 997 条
# （实测订阅它 last_new_count=0）。号主要的是"合集整档"，那是另一个入口。

_BVID_RE = re.compile(r"(BV[0-9A-Za-z]{5,}|av\d+)")


def parse_bvid(raw: str) -> str:
    """从稿件链接（或裸 bvid）里抠出 bvid。分享链接常带 ?spm_id_from=…&vd_source=… 尾巴。"""
    m = _BVID_RE.search(raw or "")
    return m.group(1) if m else ""


def fetch_collection(bvid: str, cookie: str = "") -> dict:
    """一条稿件 → 它所在合集的全量清单。**一次 view 接口就够，不逐条打详情**。

    实测（2026-10-27，BV1ZT7j6WESN）：一次响应 1.7MB，带 997 条 episodes，每条自带
    bvid/cid/title 和 arc{pic,pubdate,duration,stat,author}。所以"入 300 条元数据"是
    几秒钟的事——BILI_SYNC_LIMIT 那句注释说的"几百条连打会撞风控"讲的是逐条补字幕，
    不适用在这条路上，别照那个理由把这里也限成 20。
    """
    bvid = parse_bvid(bvid)
    if not bvid:
        return {"ok": False, "items": [], "error": "链接里认不出稿件编号（要 BV…/av… 这种视频链接）"}
    data = _get(API + "/x/web-interface/view", cookie=cookie,
                referer=VIDEO_URL % bvid, params={"bvid": bvid})
    reason = _fail_reason(data.get("code"), data.get("message", ""))
    if reason:
        return {"ok": False, "items": [], "error": "合集清单取回失败：" + reason}
    v = data.get("data") or {}
    season = v.get("ugc_season") or {}
    owner = v.get("owner") or {}
    if not (season.get("sections") or []):
        # 单条投稿没有 ugc_season。这时候不许静默返回 0 条——号主会以为"合集没抓到"，
        # 实际是他贴的那条本来就不在合集里
        return {"ok": False, "items": [], "single": {"aweme_id": bvid,
                "title": v.get("title") or "", "mid": str(owner.get("mid") or ""),
                "author": owner.get("name") or ""},
                "error": "这条稿件不属于任何合集，它只是一条单独投稿"}
    items = []
    for sec in season.get("sections") or []:
        for ep in sec.get("episodes") or []:
            arc = ep.get("arc") or {}
            pub = arc.get("pubdate") or arc.get("ctime") or 0
            stat = arc.get("stat") or {}
            one = ep.get("bvid") or ""
            if not one:
                continue
            items.append({
                "aweme_id": one,
                "cid": ep.get("cid") or "",
                "url": VIDEO_URL % one,
                "title": arc.get("title") or ep.get("title") or "",
                "cover_url": arc.get("pic") or "",
                "author": arc.get("author") or owner.get("name") or "",
                "published_at": time.strftime("%Y-%m-%d", time.localtime(pub)) if pub else "",
                "duration_sec": _to_seconds(arc.get("duration") or 0),
                "view_count": int(stat.get("view") or 0),
                "section": sec.get("title") or "",
                # 合集清单里没有字幕，也不在这一步去取（997 条逐条取必撞风控）
                "transcript": "",
                "subs_status": "未取字幕",
            })
    # 合集接口给的是"编排顺序"，不保证从新到旧；入库要按发布时间排，
    # 否则"取最近 300 条"取到的是作者手动排的靠前那几集
    items.sort(key=lambda x: (x.get("published_at") or "", x.get("aweme_id") or ""),
               reverse=True)
    return {"ok": True, "items": items, "error": "",
            "season_id": season.get("id"), "title": season.get("title") or "",
            "intro": season.get("intro") or "",
            "mid": str(season.get("mid") or owner.get("mid") or ""),
            "author": owner.get("name") or "",
            "ep_count": int(season.get("ep_count") or (season.get("stat") or {}).get("ep_count")
                            or len(items)),
            "total": len(items)}


def sync_bilibili_collection(url_or_bvid: str, known_bvids=None, limit: int = 300,
                             cookie: str = "") -> dict:
    """合集整档 → 待入库记录（不写库，写库交给调用方走 insert_bilibili_videos）。

    与 sync_bilibili_user 同一套分层：这一层只出数据，app.py 负责加锁入库。
    limit 是"最近多少条"（已按发布时间从新往老排过），去重按 bvid。
    返回 {"videos","total","skipped","season","title","mid","author","error"}
    """
    known = set(known_bvids or [])
    col = fetch_collection(url_or_bvid, cookie=cookie)
    if not col.get("ok"):
        return {"videos": [], "total": 0, "skipped": 0, "season": "", "title": "",
                "mid": col.get("single", {}).get("mid", "") if col.get("single") else "",
                "author": col.get("single", {}).get("author", "") if col.get("single") else "",
                "error": col.get("error") or "合集取回失败"}
    items = col["items"]
    fresh = [it for it in items if it["aweme_id"] not in known]
    picked = fresh[:int(limit)] if limit else fresh
    return {"videos": picked, "total": len(items), "skipped": len(items) - len(fresh),
            "season": col.get("title") or "", "mid": col.get("mid") or "",
            "author": col.get("author") or "", "error": "",
            "ep_count": col.get("ep_count") or len(items)}


# ── B 站音频下载与转写（合集整档入库后补正文用） ──────────────────

AUDIO_TIMEOUT = 300          # 秒；一条 7 分钟的片子取音频，正常几十秒


def download_audio(bvid: str, workdir: str, cookie: str = "") -> tuple:
    """yt-dlp 只下音频并转 wav，返回 (音频路径, 错误)。

    为什么不下整片：这个合集实测 122.7 小时，整片哪怕最低画质也要几十 GB，而转写只要声音。
    `-x --audio-format wav` 直接给出 16bit PCM，省掉 main.asr_transcribe 里那道抽音频。
    """
    exe = ytdlp_cmd()
    if not exe:
        return "", "本机没有 yt-dlp，装一下：pip3 install yt-dlp"
    one = parse_bvid(bvid)
    if not one:
        return "", "链接里认不出稿件编号"
    os.makedirs(workdir, exist_ok=True)
    ck_path = _write_cookie_file(cookie) if cookie else ""
    cmd = exe + ["--no-warnings", "--quiet", "-f", "bestaudio/best", "-x",
                 "--audio-format", "wav", "--audio-quality", "1", "--no-playlist",
                 # 直接落成 16k 单声道：whisper 本来就要这个规格。不指定的话 yt-dlp 按源
                 # 44.1k 立体声出 wav，一条 4 小时的直播录像就是 2.6GB 临时文件（实测这个合集
                 # 里最长的一条 15018 秒），16k 单声道把它压到 ~460MB
                 "--postprocessor-args", "FFmpegCmd:-ar 16000 -ac 1",
                 "-o", os.path.join(workdir, "%(id)s.%(ext)s")]
    if ck_path:
        cmd += ["--cookies", ck_path]
    cmd.append(VIDEO_URL % one)
    proc = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=AUDIO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "", "音频下载超时（%ds）" % AUDIO_TIMEOUT
    except Exception as e:
        return "", "yt-dlp 调用异常: %s" % str(e)[:120]
    finally:
        if ck_path:
            try:
                os.unlink(ck_path)
            except OSError:
                pass
    if proc is None or proc.returncode != 0:
        tail = [ln for ln in ((proc.stderr if proc else "") or "").strip().splitlines() if ln]
        return "", "取音频失败：%s" % ((tail[-1] if tail else "退出码异常")[:160])
    wavs = [os.path.join(workdir, n) for n in os.listdir(workdir) if n.lower().endswith(".wav")]
    if not wavs:
        return "", "音频下载完成但没找到 wav（yt-dlp 可能没装 ffmpeg 解码）"
    newest = max(wavs, key=os.path.getmtime)
    if os.path.getsize(newest) < 4096:
        return "", "音频文件是空的（这条可能被删了或是付费/会员专享）"
    return newest, ""


def transcribe_bvid(bvid: str, cookie: str = "", model_size: str = "base",
                    workdir: str = "") -> tuple:
    """一条 B 站稿件 → (转写文本, 错误)。取音频 → 转写 → 删临时文件。

    模型调用和出口那两道规范化（繁转简、术语表回改）不在这里，走 main.transcribe_audio
    那一份——抖音与 B 站共用，抄两份就会漂移。
    workdir 由调用方给（落 output/ 下，半途挂了还能看见是哪个文件）。
    """
    tmp = workdir or tempfile.mkdtemp(prefix="bili_asr_")
    path, err = download_audio(bvid, tmp, cookie=cookie)
    if err:
        return "", err
    try:
        from main import transcribe_audio
    except Exception as e:
        return "", "转写入口不可用: %s" % str(e)[:120]
    try:
        text, _segs = transcribe_audio(path, model_size=model_size, language="zh",
                                       tag=str(bvid)[:12])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if text.startswith("[转写失败]"):
        return "", text
    return text, ""


VIDEO_TIMEOUT = 900          # 秒；整片（限 720p）比音频重，一条 5 分钟的片子实测几十秒

# ── 下整片的资源闸（本机 2026-11-01 实测：数据卷只剩 28GB、已用 93%）──────
MIN_FREE_GB = 6.0            # 低于这条空闲就不下整片：留 6GB 是给系统和工作台写库用的余量
# 实测（2026-11-01，三条 14.3-14.5 分钟的稿件）：**2.1–3.0 MB/分钟**，下完一条只要 3-6 秒。
# 这个上限不是我们挑的——B 站 720P/1080P 属于大会员档，游客与普通号只到 852x480，
# 所以估到 14MB/分钟是凭空放大五倍。这里取实测最大值的两倍当余量。
MB_PER_MIN = 6.0
HEADROOM_X = 2               # 预估要乘的头寸：一条下完之前不能把盘子顶满
STALE_TMP_MINUTES = 360      # 临时片老化线：作业被 kill 会留下半截 wav/mp4，超过 6 小时的清掉


def free_gb(path: str = "") -> float:
    """这台机器现在还剩多少 GB（按落临时文件的那个卷问，不按根卷猜）。"""
    try:
        import shutil
        return round(shutil.disk_usage(path or tempfile.gettempdir()).free / 1024.0 ** 3, 2)
    except Exception:
        return -1.0


def est_download_mb(duration_sec) -> float:
    """按条长估整片体积（MB）。估不出来（0）就当很大，宁可不抽帧。"""
    try:
        d = float(duration_sec or 0)
    except (TypeError, ValueError):
        return 1e9
    if d <= 0:
        return 1e9
    return d / 60.0 * MB_PER_MIN


def may_download_video(free: float, duration_sec) -> tuple:
    """这一条能不能下整片。返回 (能不能, 为什么)。

    为什么不设"一轮累计上限"那种闸：空闲是**这台机器此刻**的事实，别的程序也在写，
    记自己的累计字节会漂；每条下之前重新问一次 `free_gb` 才是真判据。
    """
    if free < 0:
        return False, "读不到磁盘空闲"
    est = est_download_mb(duration_sec)
    if free < MIN_FREE_GB:
        return False, "空闲只剩 %.1fGB（低于 %.0fGB 底线）" % (free, MIN_FREE_GB)
    if est * HEADROOM_X > free * 1024.0:
        return False, "这条估 %.0fMB，×%d 头寸超过空闲" % (est, HEADROOM_X)
    return True, ""


def sweep_stale_tmp(workdir: str, keep_minutes: int = STALE_TMP_MINUTES) -> tuple:
    """清掉临时目录里的**陈年残片**，返回 (删除个数, 释放字节)。

    为什么必须有这一条：作业被 kill（改代码后重启工作台就会这样）时，`finally` 里那句
    `os.remove` 根本不执行，半截 wav/mp4 就一直躺在盘上——11-01 实测那个目录已经躺了 149MB。
    判据只用 mtime：正在下的那个文件时间戳是新鲜的，不会误删；也不认文件名，
    yt-dlp 的 .part 和我们的 wav 一起收拾。
    """
    import time as _t
    cutoff = _t.time() - max(1, int(keep_minutes or STALE_TMP_MINUTES)) * 60
    n = freed = 0
    try:
        names = os.listdir(workdir)
    except OSError:
        return 0, 0
    for name in names:
        p = os.path.join(workdir, name)
        try:
            if not os.path.isfile(p) or os.path.getmtime(p) >= cutoff:
                continue
            size = os.path.getsize(p)
            os.remove(p)
            n += 1
            freed += size
        except OSError:
            continue
    return n, freed


def download_video(bvid: str, workdir: str, cookie: str = "",
                   max_height: int = 720) -> tuple:
    """把整片下到本地，返回 (视频路径, 错误)。

    为什么非下整片不可：关键帧是**画面**，`download_audio` 那条只有声音，抽不出帧。
    `max_height` 只是上限，**实际拿到哪一档由账号权益决定**：11-01 实测这个 cookie 只给到
    852x480——B 站把 720P/1080P 划进大会员档。所以抽出来的帧宽 852px，而排版那条带要
    900-950px（`frame_pool.FRAME_W`），等于放大 1.06-1.11 倍，略软但能用；
    要真清晰只能买大会员，或这批素材只走纯文字版式——这条归号主拍，不是我能替他降的。
    下整片只用在"要抽帧"的那一批；长录像仍然走 `download_audio`（见 transcribe_bvid 的注释）。
    """
    exe = ytdlp_cmd()
    if not exe:
        return "", "本机没有 yt-dlp，装一下：pip3 install yt-dlp"
    one = parse_bvid(bvid)
    if not one:
        return "", "链接里认不出稿件编号"
    os.makedirs(workdir, exist_ok=True)
    ck_path = _write_cookie_file(cookie) if cookie else ""
    h = int(max_height or 720)
    # B 站是 DASH 分轨：单文件档位（`best[height<=720]`）根本不存在，
    # 11-01 实跑 40 条全部报 "Requested format is not available"。
    # 所以要"视频轨+音频轨"一起要，交给 ffmpeg 合流成 mp4（本机有 ffmpeg）；
    # 最后兜 `b*` 是不限高度——宁可得差点也不能一条都抽不出帧。
    fmt = ("bv*[height<=%d]+ba*[height<=%d]/bv*[height<=%d]+ba/b[height<=%d]/b*"
           % (h, h, h, h))
    cmd = exe + ["--no-warnings", "--quiet", "-f", fmt,
                 "--merge-output-format", "mp4", "--no-playlist",
                 "-o", os.path.join(workdir, "%(id)s.%(ext)s")]
    if ck_path:
        cmd += ["--cookies", ck_path]
    cmd.append(VIDEO_URL % one)
    proc = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=VIDEO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "", "整片下载超时（%ds）" % VIDEO_TIMEOUT
    except Exception as e:
        return "", "yt-dlp 调用异常: %s" % str(e)[:120]
    finally:
        if ck_path:
            try:
                os.unlink(ck_path)
            except OSError:
                pass
    if proc is None or proc.returncode != 0:
        _drop_partial(workdir, one)
        tail = [ln for ln in ((proc.stderr if proc else "") or "").strip().splitlines() if ln]
        return "", "取整片失败：%s" % ((tail[-1] if tail else "退出码异常")[:160])
    vids = [os.path.join(workdir, n) for n in os.listdir(workdir)
            if n.lower().endswith((".mp4", ".flv", ".mkv", ".webm"))]
    if not vids:
        _drop_partial(workdir, one)
        return "", "整片下载完成但没找到视频文件（yt-dlp 可能没合流成功）"
    newest = max(vids, key=os.path.getmtime)
    if os.path.getsize(newest) < 4096:
        _drop_partial(workdir, one)
        return "", "视频文件是空的（这条可能被删了或是会员专享）"
    return newest, ""


def _drop_partial(workdir: str, bvid: str) -> None:
    """下失败时把这个稿件的半截文件清掉。失败不响——省盘子的事不该卡住整条链。"""
    try:
        for n in os.listdir(workdir):
            if bvid.lower() in n.lower() or n.endswith((".part", ".ytdl")):
                p = os.path.join(workdir, n)
                if os.path.isfile(p):
                    os.remove(p)
    except OSError:
        pass


def transcribe_with_frames(bvid: str, cookie: str = "", model_size: str = "small",
                           workdir: str = "", pool_key: str = "") -> tuple:
    """一条 B 站稿件走完整条链：下整片 → 转写 → 抽关键帧 → 删临时片。

    返回 `(文本, 备注, 帧数)`。三条不能改的地方：
      · **顺序**：抽帧必须在删片之前。片一删就只能靠海报弹窗那颗「补画面」重下，
        而那颗只认抖音（拿的是 aweme_id），B 站素材就永远补不回来；
      · **转写在前**：帧池的 `near` 字段要拿分句时间戳去对画面，先抽帧只能空着；
      · **抽帧不判帧**（`judge=False`，0 元）：判帧推迟到出图那一次，理由写在
        `frame_pool.build` 的注释里（批量 20 条就是 38 万 token，出图一次只要 8 张）。
    备注非空**不代表转写失败**——转写成功的文本照样要落库，只是这一条没抽到帧。
    pool_key 用 BV 号：素材库与攻略页都按 `aweme_id` 取帧池，B 站那一列存的就是 BV 号。
    """
    tmp = workdir or tempfile.mkdtemp(prefix="bili_asr_")
    path, err = download_video(bvid, tmp, cookie=cookie)
    if err:
        return "", err, 0
    try:
        from main import transcribe_audio
    except Exception as e:
        return "", "转写入口不可用: %s" % str(e)[:120], 0
    try:
        text, segs = transcribe_audio(path, model_size=model_size, language="zh",
                                      tag=str(bvid)[:12])
        if text.startswith("[转写失败]"):
            return "", text, 0
        note = ""
        frames = 0
        try:
            try:
                from src import frame_pool
            except ImportError:
                import frame_pool
            meta = frame_pool.build(path, pool_key or bvid, segments=segs)
            frames = len(meta.get("frames") or [])
        except Exception as e:
            # 抽帧坏了不许把已到手的正文一起丢掉：照旧落库，备注里写明没抽到帧
            note = "转写成功但抽帧失败：%s" % str(e)[:80]
        return text, note, frames
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def extract_frames_only(bvid: str, cookie: str = "", workdir: str = "",
                        pool_key: str = "") -> tuple:
    """已经转写过的稿件补关键帧：下整片 → 抽帧 → 删片。返回 (帧数, 备注)。

    为什么单开这一条：`transcribe_bvid` 只下音频，那批素材之后**没有任何入口能补画面**
    ——「补画面」那颗只认抖音那条链的 aweme_id，B 站的 BV 号进不去（号主 11-01 点名要帧）。
    代价要说清：这一档不重跑转写，所以拿不到分句时间戳，帧的 `near` 字段是空的，
    图注只能等下一次真转写才有话可锚——比"一帧都没有"强，但别当成完整链的产物。
    """
    tmp = workdir or tempfile.mkdtemp(prefix="bili_frames_")
    path, err = download_video(bvid, tmp, cookie=cookie)
    if err:
        return 0, err
    try:
        try:
            from src import frame_pool
        except ImportError:
            import frame_pool
        try:
            meta = frame_pool.build(path, pool_key or bvid)
            return len(meta.get("frames") or []), ""
        except Exception as e:
            return 0, "抽帧失败：%s" % str(e)[:80]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def resolve_author(mid: str, cookie: str = "") -> str:
    """拿 UP 主昵称。先试 card（一次请求就够），它现在会被风控 -352；
    拿不到就退一步用 yt-dlp 清单的第一条稿件，从 view 的 owner.name 里读——
    多花几秒，但昵称是订阅列表的显示主键，空着就没法按作者筛。"""
    info = fetch_up_info(mid)
    if info.get("name"):
        return info["name"]
    if not cookie:
        try:
            cookie, _src = load_cookie()
        except RuntimeError:
            return ""
    listed = list_bvids(mid, cookie=cookie, cap=1)
    if listed["error"]:
        # card 被风控、清单又拿不到时不要静默返回空串——订阅会建成"未知作者"，
        # 按作者筛选永远命不中，现场只看到"昵称怎么是空的"，查不到原因
        print("[bilibili] 昵称兜底也失败：card 无名字，清单 %s" % listed["error"], flush=True)
        return ""
    if listed["bvids"]:
        detail = fetch_video_detail(listed["bvids"][0], cookie=cookie)
        return detail.get("owner") or ""
    return ""


def cookie_status() -> dict:
    """只看 cookie 配没配、像不像，不发任何请求——设置页自检用。"""
    try:
        cookie, src = load_cookie()
    except RuntimeError as e:
        return {"ok": False, "cookie": "", "source": "", "error": str(e)}
    keys = set(re.findall(r"([A-Za-z0-9_]+)=", cookie))
    return {"ok": True, "cookie": "已读取（%d 个字段）" % len(keys), "source": src, "error": "",
            "has_sessdata": "SESSDATA" in keys}


def probe(mid: str) -> dict:
    """真发一次列表请求验证登录态可用：只在用户点「测试连通」时走，会吃一次接口配额。"""
    status = cookie_status()
    if not status["ok"]:
        return status
    cookie, _src = load_cookie()
    res = fetch_user_videos(mid, known_bvids=set(), limit=1, cookie=cookie)
    if res["error"]:
        return {"ok": False, "source": status["source"], "error": res["error"]}
    first = (res["videos"] or [{}])[0]
    return {"ok": True, "source": status["source"], "error": "",
            "sample": first.get("title", ""), "author": res.get("author") or "",
            "bvid": first.get("bvid", "")}


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    mid_str = parse_mid(arg)
    if not mid_str:
        print("用法: python3 src/bilibili_fetcher.py <mid 或 space.bilibili.com/<mid>/video>")
        raise SystemExit(2)
    ck = ""
    try:
        ck, where = load_cookie()
        print("cookie 来源:", where)
    except RuntimeError as e:
        print("警告:", e)
    info = fetch_up_info(mid_str)
    print("UP 主:", json.dumps(info, ensure_ascii=False))
    result = sync_bilibili_user(mid_str, known_bvids=set(), limit=3, cookie=ck)
    print("错误:", result["error"], "| 备注:", result["note"])
    for v in result["videos"]:
        print("-", v["aweme_id"], v["title"][:24], "|", v["subs_status"],
              "| 字幕字数=", len(v["transcript"]), "| 播放=", v["view_count"])
