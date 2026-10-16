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
    exe = shutil.which(YTDLP_BIN) or ""
    if not exe:
        return {"bvids": [], "error":
                "本机没有 yt-dlp，B 站投稿清单拿不到（直连接口必被 -403 风控）。"
                "装一下：pip3 install yt-dlp"}
    if not cookie:
        return {"bvids": [], "error": "B 站清单需要 cookie，先在 config/config.env.local 配 BILIBILI_COOKIE="}
    ck_path = _write_cookie_file(cookie)
    try:
        cmd = [exe, "--no-warnings", "--quiet", "--flat-playlist", "--cookies", ck_path,
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
