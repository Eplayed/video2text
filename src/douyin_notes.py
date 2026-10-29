"""douyin_notes.py — 抖音图文（aweme_type=68）落库与图片本地化

为什么要有这一个模块（2026-10-16）：抖音的「图文作品」在作者主页上的锚点是
`/note/<id>`，而采集那条只刮 `a[href*="/video/"]`，所以图文一条都没进过素材库
（实测：笨小孩首屏 27 条里 20 条是图文）。号主拍板两点——来源栏单开一档「抖音图文」，
图片下载到本地存着。

图片为什么必须落盘而不是直接引用：抖音图链是带签名的（`x-expires` 实测约一个月后失效、
`x-signature` 校验），链接存进库迟早变成一排 403。存到 `output/douyin_notes/<aweme_id>/`
之后走工作台自己的 `/media/` 伺服，不再依赖抖音的签名。

取哪一份图：`images[].url_list[0]` 是无水印的展示图（`tplv-dy-aweme-images`），
`download_url_list[0]` 反而是**带水印**的那份（`tplv-dy-water-v2:<base64 抖音号>`），
名字容易骗人，默认取 url_list、拿不到才退回 download。
实测直连下载这两份都不需要 cookie / referer（签名已在 URL 里）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from src import content_store

NOTE_DIR = "douyin_notes"          # output/ 下的子目录名
MAX_IMAGES_PER_NOTE = 12           # 首屏实测见过 16 张的，按号主要用不用得上的判断先截 12
IMG_TIMEOUT = 25
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def note_output_dir(output_dir: Path, aweme_id: str) -> Path:
    return Path(output_dir) / NOTE_DIR / str(aweme_id)


def _publish_str(create_time: Any) -> str:
    """秒级时间戳 → 和抖音那条一样的 '%Y-%m-%d %H:%M:%S'（界面取前 10 位显示日期）。"""
    try:
        ts = int(create_time or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def normalize_note(raw: dict[str, Any]) -> dict[str, Any]:
    """浏览器脚本带回的一条 aweme/post 条目 → 入库用的形状（还没有本地图片路径）。"""
    aweme_id = str(raw.get("aweme_id") or "")
    images = raw.get("images") or []
    return {
        "aweme_id": aweme_id,
        "url": "https://www.douyin.com/note/%s" % aweme_id,
        "title": (raw.get("desc") or "").strip(),
        "published_at": _publish_str(raw.get("create_time")),
        "cover_url": raw.get("cover") or "",
        "remote_images": [
            {"url": (im.get("url") or ""), "download": (im.get("download") or "")}
            for im in images if (im.get("url") or im.get("download"))
        ],
        "stats": {
            "digg": int(raw.get("digg") or 0),
            "comment": int(raw.get("comment") or 0),
            "share": int(raw.get("share") or 0),
        },
        "images": [],
    }


def download_note_images(aweme_id: str, remote_images: list, output_dir: Path,
                         session: Any = None) -> tuple:
    """下载一条图文的图，返回 (本地相对路径列表, 错误列表)。

    单张失败只记一条错误、不影响其余——图文常 3~9 张，为一张贴不上就把整条丢掉，
    表现就是"同步说明明新增了却看不到图"。已存在且非空的文件直接复用（重复同步不重下）。
    """
    rel_paths = []
    errors = []
    if not remote_images:
        return rel_paths, errors
    target = note_output_dir(output_dir, aweme_id)
    target.mkdir(parents=True, exist_ok=True)
    http = session or requests
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    for idx, im in enumerate(remote_images[:MAX_IMAGES_PER_NOTE], 1):
        url = (im.get("url") or "").strip() or (im.get("download") or "").strip()
        if not url:
            errors.append("第%d张没有链接" % idx)
            continue
        ext = ".webp"
        for cand in (".jpg", ".jpeg", ".png", ".webp"):
            if cand in url.lower().split("?")[0]:
                ext = ".jpg" if cand == ".jpeg" else cand
                break
        local = target / ("%02d%s" % (idx, ext))
        if local.exists() and local.stat().st_size > 1024:
            rel_paths.append("%s/%s/%s" % (NOTE_DIR, aweme_id, local.name))
            continue
        try:
            resp = http.get(url, headers=headers, timeout=IMG_TIMEOUT)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if resp.status_code != 200 or "image" not in ctype or len(resp.content) < 1024:
                # 被风控挡回 HTML 时 status 常常也是 200，所以必须看 Content-Type 和字节数
                errors.append("第%d张没取到（HTTP %s / %s / %dB）"
                              % (idx, resp.status_code, ctype or "无类型", len(resp.content or b"")))
                continue
            local.write_bytes(resp.content)
            rel_paths.append("%s/%s/%s" % (NOTE_DIR, aweme_id, local.name))
        except Exception as e:
            errors.append("第%d张下载异常: %s" % (idx, str(e)[:60]))
    return rel_paths, errors


def stats_note(stats: dict[str, int], image_count: int) -> str:
    """互动数落 remark 的一行人话。图文没有播放量，所以这行不代表播放。"""
    return "赞%s·评%s·转%s·图%d张" % (stats.get("digg", 0), stats.get("comment", 0),
                                       stats.get("share", 0), image_count)


def sync_notes(db_path: Any, output_dir: Any, notes: list, author: str = "",
               sec_uid: str = "", category: str = "", game: str = "",
               limit: int = 20, log_cb=None) -> dict:
    """一轮图文同步：先按 aweme_id 去重，再下图，最后入库。

    顺序是故意的：**去重放在下载之前**——同一批图文重复同步时不该再付一次图链请求，
    而入库放在所有下载之后，这样"图全挂了的条目"不会被记成已完成（正文空 + 图 0 张的
    素材对写手没用，宁可下一轮重试）。
    返回 {"candidates":n, "inserted":n, "images":n, "skipped":[...], "errors":[...]}
    """
    out = {"candidates": 0, "inserted": 0, "images": 0, "skipped": [], "errors": []}
    items = [n for n in (notes or []) if n.get("aweme_id")]
    if not items:
        return out
    existing = content_store.get_douyin_note_ids(db_path)
    fresh = []
    for raw in items:
        nid = str(raw.get("aweme_id"))
        if nid in existing:
            out["skipped"].append(nid)
            continue
        fresh.append(raw)
    out["candidates"] = len(fresh)
    if not fresh:
        return out

    prepared = []
    for raw in fresh[:limit]:
        item = normalize_note(raw)
        rel, errs = download_note_images(item["aweme_id"], item["remote_images"],
                                         Path(output_dir))
        out["errors"].extend(["%s %s" % (item["aweme_id"], e) for e in errs])
        if errs and not rel:
            # 一张都没落下：这条素材等于没正文，记错误、不入库，下轮重试
            continue
        item["images"] = rel
        item["stats_note"] = stats_note(item["stats"], len(rel))
        prepared.append(item)

    if prepared:
        inserted = content_store.insert_douyin_notes(
            db_path, prepared, author=author, sec_uid=sec_uid,
            category=category, game=game)
        out["inserted"] = len(inserted)
        out["images"] = sum(len(p.get("images") or []) for p in prepared)
        out["rows"] = [(i["source_sheet"], i["source_row"]) for i in inserted]
    if log_cb:
        log_cb(out)
    return out


def purge_note_images(output_dir: Any, aweme_ids: list) -> int:
    """删掉本地图文图片目录（取消订阅 / 删除素材时连带清理用）。返回删掉的目录数。"""
    removed = 0
    for aid in (aweme_ids or []):
        d = note_output_dir(Path(output_dir), str(aid))
        if d.exists() and d.is_dir():
            try:
                for f in sorted(d.iterdir()):
                    if f.is_file():
                        f.unlink()
                d.rmdir()
                removed += 1
            except OSError as e:
                print("[douyin_notes] 图片目录没删净 %s: %s" % (d, e), flush=True)
    return removed
