# -*- coding: utf-8 -*-
"""release_review.py — 成品的最终人工放行记录，**绑定成品哈希**，改稿即失效。

号主 2026-10-29 的原话是"最终的人工审稿我也不知道到底正不正确"——这句是关键。
所以这一层**不做逐项打分表**（那等于把"审得细不细"变成一道伪装的机器判据），
只保留一条真正机器可判的事实：**你放行的是哪一版**。

只解决一件事：上周审过的那版和今天要发的那版是不是同一个东西。
成品一改（文案、图上文字、任何一张页图），哈希就变，旧放行自动作废，得重新签一次。
不做的事：不判断内容对不对、不替人给"通过"的结论、不拦出图（只影响 `publishable`）。

记录落在成品包里的 `review.json`（跟包走、拷库不丢），不进 SQLite：
一张海报一个目录，放行记录天然属于那个目录，没必要再建一张跨表的键。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

REVIEW_FILE = "review.json"
# 参与哈希的发布字段：这些一改，读者看到的东西就变了
_PUBLISH_KEYS = ("copy_text", "digest", "source_note", "keyword_reply",
                 "tt_titles", "xhs_titles", "xhs_copy", "xhs_tags")


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_hash(out_dir: str, manifest: Dict[str, Any]) -> str:
    """成品指纹 = 会发出去的文字 + 每一页图片的字节 + 图上的文字清单。

    页图按文件名排序后逐张取 sha256，再和文字一起进总哈希：
    只改图（重出一页）也算改稿，旧放行同样作废。
    """
    h = hashlib.sha256()
    pub = manifest.get("publish") or {}
    for k in _PUBLISH_KEYS:
        if pub.get(k):
            h.update(("pub:%s=%s\n" % (k, _canon(pub.get(k)))).encode("utf-8"))
    for pg in (manifest.get("pages") or []):
        h.update(("page:%s\n" % _canon({
            "file": pg.get("file"), "chars": pg.get("chars"), "layout": pg.get("layout"),
            "cards": pg.get("cards")})).encode("utf-8"))
    for name in sorted(os.listdir(str(out_dir))) if os.path.isdir(str(out_dir)) else []:
        if not name.endswith(".png"):
            continue
        if not (name.startswith("poster") or name.startswith("base")):
            continue                      # 预览图、缩略图不参与：它们不是发出去的那张
        with open(os.path.join(str(out_dir), name), "rb") as fh:
            h.update(("%s:%s\n" % (name, hashlib.sha256(fh.read()).hexdigest())).encode("utf-8"))
    return h.hexdigest()[:16]


def load(out_dir: str) -> List[Dict[str, Any]]:
    p = os.path.join(str(out_dir), REVIEW_FILE)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save(out_dir: str, rows: List[Dict[str, Any]]) -> None:
    p = os.path.join(str(out_dir), REVIEW_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def record(out_dir: str, channel: str, pkg_hash: str, reviewer: str = "",
           note: str = "") -> Dict[str, Any]:
    """签一次放行。签名人和成品哈希必填——没有"谁签的"的放行等于没放行。"""
    who = str(reviewer or "").strip()
    if not who:
        return {"ok": False, "error": "要写谁放行的（署名空着，这条记录就没法追）"}
    if not pkg_hash:
        return {"ok": False, "error": "没有成品指纹，不能记放行"}
    rows = load(out_dir)
    row = {"channel": channel or "toutiao", "package_hash": pkg_hash, "reviewer": who,
           "note": str(note or "").strip()[:200], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rows = [r for r in rows if r.get("channel") != row["channel"]]
    rows.append(row)
    _save(out_dir, rows[-20:])
    return {"ok": True, "row": row}


def status(out_dir: str, channel: str, pkg_hash: str) -> Dict[str, Any]:
    """当前这一版有没有被放行过。哈希对不上就是没审过（改稿即失效）。"""
    row = next((r for r in load(out_dir) if r.get("channel") == (channel or "toutiao")), None)
    if not row:
        return {"reviewed": False, "hash_matches": False, "at": "", "reviewer": "",
                "note": "", "stale": False}
    match = bool(pkg_hash) and row.get("package_hash") == pkg_hash
    return {"reviewed": True, "hash_matches": match, "at": row.get("at") or "",
            "reviewer": row.get("reviewer") or "", "note": row.get("note") or "",
            "stale": not match, "signed_hash": row.get("package_hash") or ""}


def blocks_publish(channel: str, status: Dict[str, Any]) -> bool:
    """这一版能不能标"待发"。只有小红书这一档要求先签最终放行。

    头条/公众号维持原样——号主没在那两条链上提过这个要求，无端加一道只会让人先学会绕。
    """
    return channel == "xhs" and not (status.get("reviewed") and status.get("hash_matches"))


def checklist_hint(channel: str) -> List[str]:
    """放行时要人过一眼的几件事（**提示清单，不是打分表**，勾不勾都不落库）。"""
    base = ["图上每一页的文字你读过一遍（不是读摘要，是读图）",
            "发布标题与配文里没有把没核过的数字写成确定结论",
            "来源那一行分得清：素材哪天发的、稿子哪天生成的、事实核没核过"]
    if channel == "xhs":
        base.append("小红书这条别写「回复 XX 领取」那类互动钩子（10-10 那次被判 AI 托管就是这个）")
    return base
