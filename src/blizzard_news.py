# -*- coding: utf-8 -*-
"""blizzard_news.py — 抓《魔兽世界》国服官网公告，落进素材库（第一手中文官方源）

为什么要有这一层（2026-10-17 号主定的）：他要做"第一手资讯"，可库里 5 个微信订阅
全是第三方转述号（重返艾泽拉斯 / 魔兽世界情报局 / 艾泽拉斯快讯 / 艾泽拉斯前哨 / G团日记），
**一个官方源都没有**——以前写"制作总监确认"那种句子，其实是别人转述的再转述。

实测过的取法（10-17 23:15 本机跑）：
  · `https://wow.blizzard.cn/news/` 原始 HTML 里就有 26 条新闻链接和标题，**不用浏览器**
    （页面里那套 news/list_*.js 只是把同一份 DOM 美化了一遍）；
  · 详情页 `class="detail"` 是正文、`class="Content"` 是发布日期、`<title>` 是标题；
  · 国际服 `news.blizzard.com/en-us/world-of-warcraft` 会 302 到一个 JS 壳、没有 RSS，
    要它得另写渲染那一版（这一版只做国服，先把一手中文源跑通）；
  · `gateway.battle.net` 连不上、wowhead 403、battlenet.com.cn 401 —— 都别指望。

去重键：`aweme_id = "bn" + md5(url)[:16]`（微信文章那一路也是拿 URL 做 hash，
官方公告没有 bvid 那种原生 id）。落点与微信/B站同一套：DB 直写、不进 Excel、行号自增。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import argparse
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # 没装 bs4 也不该整条链子断：退成正则抽取
    BeautifulSoup = None

LIST_URL = "https://wow.blizzard.cn/news/"
SHEET = "暴雪官方"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_GAP_SEC = 0.6          # 官方站也是网易托管，别对着它连打
MAX_DETAIL_PER_RUN = 30


def news_id(url: str) -> str:
    return "bn" + hashlib.md5(str(url or "").encode("utf-8")).hexdigest()[:16]


def _get(url: str, timeout: int = 25) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _abs(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://wow.blizzard.cn" + href
    return href


def fetch_list(limit: int = MAX_DETAIL_PER_RUN) -> List[Dict[str, str]]:
    """列表页 → [{url, title}]，按页面顺序（官网是从新往老排）。"""
    html = _get(LIST_URL)
    out: List[Dict[str, str]] = []
    seen = set()  # type: set
    pat = re.compile(r'<a[^>]+href="([^"]*?/news/[^"]+?\.html)"[^>]*>(.*?)</a>', re.S | re.I)
    for href, inner_html in pat.findall(html):
        url = _abs(href)
        if url in seen or url.startswith("javascript"):
            continue
        title = re.sub(r"<[^>]+>", " ", inner_html)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 6:
            continue
        seen.add(url)
        out.append({"url": url, "title": title})
        if len(out) >= limit:
            break
    return out


def _text_of(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def fetch_detail(url: str) -> Dict[str, Any]:
    """详情页 → 标题 / 发布日期 / 运营团队署名 / 正文 / 首图。

    正文取不到时**宁可留空并标状态**，不拿导航文案凑数——这条链子上"看着有内容其实是菜单"
    已经坑过一次（整理稿配图那回）。
    """
    html = _get(url)
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "lxml")
        title = (soup.title.get_text(strip=True) if soup.title else "") or url
        box = soup.find(class_="detail")
        body = _text_of(box)
        date = ""
        dnode = soup.find(class_="Content")
        if dnode:
            m = re.search(r"20\d\d-\d\d-\d\d", _text_of(dnode))
            date = m.group(0) if m else ""
        author = ""
        pane = soup.find(class_="Pane-content")
        if pane:
            m = re.search(r"([\u4e00-\u9fa5A-Za-z（）()]{2,18}(?:团队|官方|运营团队))", _text_of(pane))
            author = m.group(1) if m else ""
        img = ""
        if box is not None:
            im = box.find("img")
            if im is not None and im.get("src"):
                img = _abs(im["src"])
    else:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        title = (m.group(1).strip() if m else url)
        seg = re.search(r'class="detail"[^>]*>(.*?)</div>', html, re.S)
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", seg.group(1))).strip() if seg else ""
        dm = re.search(r"20\d\d-\d\d-\d\d", html)
        date = dm.group(0) if dm else ""
        author = "魔兽世界运营团队" if "运营团队" in html else ""
        im = re.search(r'class="detail"[^>]*>.*?<img[^>]+src="([^"]+)"', html, re.S)
        img = _abs(im.group(1)) if im else ""
    if not date:                      # 详情页没写日期就退到 URL 里的 /news/YYYYMMDD/
        m = re.search(r"/news/(20\d{6})/", url)
        if m:
            date = "%s-%s-%s" % (m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8])
    return {"url": url, "aweme_id": news_id(url), "title": title,
            "published_at": date, "author": author or "魔兽世界运营团队",
            "transcript": body, "cover_url": img}


def known_urls(db_path) -> set:
    try:
        from . import content_store
    except ImportError:
        import content_store
    return content_store.official_news_urls(db_path)


def sync(db_path, limit: int = 12, progress_cb: Optional[Any] = None,
         dry_run: bool = False) -> Dict[str, Any]:
    """抓列表 → 跳过库里已有的 → 逐条取正文 → 入库。返回统计，绝不静默吞错。"""
    def say(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)
        print("[%s] %s" % (SHEET, msg), flush=True)

    say("读列表页…")
    items = fetch_list(limit=max(limit * 3, limit))
    if not items:
        return {"error": "列表页一条链接都没抓到（官网改版？），本轮没动库", "found": 0}
    have = set() if dry_run else known_urls(db_path)
    todo = [it for it in items if it["url"] not in have][:limit]
    say("列表 %d 条，库里已有 %d 条，本轮要抓 %d 条" % (len(items), len(have), len(todo)))
    if not todo:
        return {"found": len(items), "new": 0, "detail_ok": 0, "errors": []}

    fetched, errors = [], []
    for n, it in enumerate(todo):
        try:
            d = fetch_detail(it["url"])
            if not d["transcript"]:
                errors.append("%s：正文为空（只登记标题，不拿菜单凑数）" % d["title"][:24])
            fetched.append(d)
            say("取正文 %d/%d｜%s｜%s 字" % (n + 1, len(todo), d["title"][:22], len(d["transcript"])))
        except Exception as e:
            errors.append("%s：%s" % (it["title"][:24], str(e)[:90]))
        time.sleep(REQUEST_GAP_SEC)

    if dry_run:
        return {"found": len(items), "new": len(fetched), "dry_run": True,
                "sample": fetched[:3], "errors": errors}
    try:
        from . import content_store
    except ImportError:
        import content_store
    inserted = content_store.insert_official_news(db_path, fetched)
    say("入库完成：新增 %d 条" % len(inserted))
    return {"found": len(items), "new": len(inserted), "detail_ok": len(fetched),
            "errors": errors, "titles": [i.get("title", "")[:30] for i in inserted]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓《魔兽世界》国服官网公告入库")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "output" / "video2text.db"))
    ap.add_argument("--limit", type=int, default=12, help="本轮最多抓几条（默认 12）")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写库")
    a = ap.parse_args(argv)
    r = sync(a.db, limit=a.limit, dry_run=a.dry_run)
    print("\n结果：%s" % {k: v for k, v in r.items() if k != "sample"})
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
