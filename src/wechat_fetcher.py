"""WeChat article fetcher via WeWe RSS.

Reads RSS feeds produced by a self-hosted WeWe RSS instance,
parses article content, and returns structured article dicts ready
to be inserted into the videos table (transcript field = article text).
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any


# ── RSS 解析 ──

def _fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch URL content as text."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_date(date_str: str) -> str:
    """Parse RSS date (RFC822 or ISO) to YYYY-MM-DD."""
    date_str = (date_str or "").strip()
    if not date_str:
        return ""
    # Try multiple formats (don't truncate input)
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",      # RFC822: Mon, 19 Aug 2024 10:00:00 +0800
        "%a, %d %b %Y %H:%M:%S %Z",      # RFC822 alt: Mon, 19 Aug 2024 10:00:00 CST
        "%d %b %Y %H:%M:%S %z",           # without weekday
        "%Y-%m-%dT%H:%M:%S%z",            # ISO 8601
        "%Y-%m-%dT%H:%M:%S",              # ISO without tz
        "%Y-%m-%d %H:%M:%S",              # simple datetime
        "%Y-%m-%d",                        # date only
    ):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Fallback: try regex extract YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def fetch_rss(feed_url: str, timeout: int = 30) -> list[dict[str, str]]:
    """Fetch and parse a WeWe RSS feed, return list of article dicts.

    Each dict has: title, link, author, published_at, html (full article HTML).
    """
    xml_text = _fetch_url(feed_url, timeout)
    root = ET.fromstring(xml_text)

    # RSS 2.0: /rss/channel/item
    # Atom: /feed/entry
    items: list[dict[str, str]] = []

    # Try RSS 2.0 first
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        author = (item.findtext("author") or item.findtext("dc:creator", namespaces=ns) or "").strip()
        pub_date = _parse_date(item.findtext("pubDate") or "")
        # content:encoded has full HTML
        html = ""
        cdata = item.find("content:encoded", namespaces=ns)
        if cdata is not None and cdata.text:
            html = cdata.text
        if not html:
            # Fallback to description
            html = item.findtext("description") or ""
        if title and link:
            items.append({
                "title": title,
                "link": link,
                "author": author,
                "published_at": pub_date,
                "html": html,
            })

    # If RSS 2.0 found nothing, try Atom
    if not items:
        ns_atom = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", namespaces=ns_atom):
            title = (entry.findtext("atom:title", namespaces=ns_atom) or "").strip()
            link_el = entry.find("atom:link", namespaces=ns_atom)
            link = link_el.get("href", "") if link_el is not None else ""
            author_el = entry.find("atom:author/atom:name", namespaces=ns_atom)
            author = author_el.text.strip() if author_el is not None and author_el.text else ""
            pub_date = _parse_date(entry.findtext("atom:published", namespaces=ns_atom) or "")
            html = entry.findtext("atom:content", namespaces=ns_atom) or ""
            if title and link:
                items.append({
                    "title": title,
                    "link": link,
                    "author": author,
                    "published_at": pub_date,
                    "html": html,
                })

    return items


# ── HTML → Markdown 转换 ──

def _strip_tags(html: str) -> str:
    """Convert WeChat article HTML to clean Markdown text."""
    if not html:
        return ""

    # 处理 CDATA
    html = html.replace("<![CDATA[", "").replace("]]>", "")

    # <img> → 提取 alt 或 src 尾段作为描述
    img_pattern = re.compile(r'<img[^>]+(?:alt=["\']([^"\']*)["\'])?[^>]*>', re.I)

    def _img_repl(m: re.Match) -> str:
        alt = m.group(1) or ""
        return f"\n[图片{'：' + alt if alt else ''}]\n" if alt else "\n[图片]\n"

    html = img_pattern.sub(_img_repl, html)

    # <br> → 换行
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)

    # 标题标签 → Markdown 标题
    for level in range(6, 0, -1):
        html = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, lv=level: f"\n{'#' * lv} {m.group(1).strip()}\n",
            html, flags=re.I | re.S,
        )

    # <p> → 换行
    html = re.sub(r"<p[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "", html, flags=re.I)

    # <div> → 换行
    html = re.sub(r"<div[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"</div>", "", html, flags=re.I)

    # <strong>/<b> → **bold**
    html = re.sub(r"<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>", r"**\1**", html, flags=re.I | re.S)

    # <em>/<i> → *italic*
    html = re.sub(r"<(?:em|i)[^>]*>(.*?)</(?:em|i)>", r"*\1*", html, flags=re.I | re.S)

    # <li> → - item
    html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.I)
    html = re.sub(r"</li>", "", html, flags=re.I)

    # <blockquote> → >
    html = re.sub(r"<blockquote[^>]*>", "\n> ", html, flags=re.I)
    html = re.sub(r"</blockquote>", "\n", html, flags=re.I)

    # <a href="...">text</a> → [text](url)
    html = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        html, flags=re.I | re.S,
    )

    # 去掉剩余标签
    html = re.sub(r"<[^>]+>", "", html)

    # HTML 实体
    entities = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&hellip;": "…",
        "&mdash;": "—", "&ldquo;": """, "&rdquo;": """,
    }
    for k, v in entities.items():
        html = html.replace(k, v)
    # 数字实体
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)

    # 清理多余空行
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


# ── 主同步函数 ──

def sync_wechat_feed(
    feed_url: str,
    existing_links: set[str],
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch a WeWe RSS feed, return new articles not yet in DB.

    Args:
        feed_url: RSS feed URL (e.g. http://localhost:4000/feed/xxx.xml)
        existing_links: set of article URLs already in DB (for dedup)
        timeout: HTTP timeout

    Returns:
        {"articles": [...], "error": ""}
    """
    try:
        items = fetch_rss(feed_url, timeout)
    except Exception as e:
        return {"articles": [], "error": f"RSS 拉取失败: {e}"}

    new_articles: list[dict[str, str]] = []
    for item in items:
        link = item["link"]
        if link in existing_links:
            continue
        text = _strip_tags(item["html"])
        if not text or len(text) < 50:
            continue
        # 用 URL hash 做 aweme_id（用于去重）
        aweme_id = hashlib.md5(link.encode()).hexdigest()[:16]
        new_articles.append({
            "aweme_id": aweme_id,
            "url": link,
            "title": item["title"],
            "author": item["author"],
            "published_at": item["published_at"],
            "transcript": text,
        })

    return {"articles": new_articles, "error": ""}
