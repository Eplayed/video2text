# -*- coding: utf-8 -*-
"""把一篇已经存到本地的公众号文章，按订阅同步同一套落点写进素材库。

为什么要有这一条：订阅同步只能整条 feed 拉（WeWe RSS），手上只有一篇链接时进不来。
而 `mp.weixin.qq.com` 对普通抓取会回"环境异常"，正文得先用真浏览器 UA 把 HTML 存下来：

    curl -sL -A "Mozilla/5.0 ... Chrome/131.0.0.0 Safari/537.36" "<文章链接>" -o temp/x.html

再跑本脚本。入库走 `content_store.insert_wechat_articles`——和订阅同步同一个函数、
同一张表（source_sheet='微信文章'、行号自增、aweme_id=URL hash 去重），不另造一套结构。

用法：
    python3 scripts/agent/import_wechat_url.py temp/x.html \
        --url "<文章链接>" --title "<标题>" --author "<账号名>" \
        --published "2026-10-29 17:41" --category 游戏攻略 --game 魔兽世界：无限

默认只打印要写什么、不写库；加 --commit 才真写（写前自动备份生产库）。
"""
import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import content_store, wechat_fetcher  # noqa: E402

DB = ROOT / "output" / "video2text.db"


def slice_js_content(html: str) -> str:
    """取 id="js_content" 那个 div 的全部内容（按 div 深度配对，不拿正则硬截）。"""
    m = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>', html)
    if not m:
        raise SystemExit("HTML 里找不到 id=js_content，多半是被微信拦了（环境异常），换真浏览器 UA 重存")
    i = m.end()
    depth = 1
    tag = re.compile(r"<(/?)div\b[^>]*>", re.I)
    pos = i
    for mm in tag.finditer(html, i):
        depth += -1 if mm.group(1) else 1
        if depth == 0:
            pos = mm.start()
            break
    else:
        raise SystemExit("js_content 这个 div 没找到收尾，别硬截半篇进库")
    return html[i:pos]


def clean(text: str) -> str:
    t = re.sub(r"[ \t]+\n", "\n", text)
    t = re.sub(r"\n{3,}", "\n\n", t)
    # 公众号正文后面永远挂一段官方导流位（活动页/宝箱/账号绑定，链接都是 *.w.163.com）。
    # 那部分不是文章内容，留着会把「原文要点」污染成一条 URL 墙，从第一个导流链接处整段切掉。
    i = t.find("163.com")
    if i > 0:
        lines = t[:i].split("\n")
        # 切点落在链接中间，往回把碎渣与链接行一起吃掉：空行、[ 、](http… 、[图片]
        while lines:
            s = lines[-1].strip()
            if s in ("", "[", "]", "[图片]", "[]") or s.startswith("](") or "http" in s \
                    or re.fullmatch(r"[\s\[\]()]*图?片?[\s\[\]()]*", s):
                lines.pop()
            else:
                break
        t = "\n".join(lines)
        if i < len(text) * 0.6:
            print("⚠ 导流链接出现在正文前 60%%，正文只留了 %d 字——这一篇可能是正文里就带广告，切点要人看一眼" % len(t))
    # 正文开头常挂着作者名/公众号水印那几行，认得出就削掉
    lines = [x for x in t.strip().split("\n")]
    while lines and len(lines[0].strip()) <= 12 and not lines[0].strip().startswith(("亲爱的", "魔兽")):
        lines.pop(0)
    return "\n".join(lines).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html", help="存下来的文章 HTML 路径")
    ap.add_argument("--url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--published", required=True)
    ap.add_argument("--category", default="")
    ap.add_argument("--game", default="")
    ap.add_argument("--commit", action="store_true", help="真写库（默认只预演）")
    a = ap.parse_args()

    raw = Path(a.html).read_text(encoding="utf-8", errors="ignore")
    body = clean(wechat_fetcher._strip_tags(slice_js_content(raw)))
    aweme_id = hashlib.md5(a.url.encode()).hexdigest()[:16]
    art = {"aweme_id": aweme_id, "url": a.url, "title": a.title, "author": a.author,
           "published_at": a.published, "transcript": body}
    print("标题：%s\n作者：%s　发布：%s\naweme_id=%s　正文 %d 字（图片占位 %d 处）"
          % (a.title, a.author, a.published, aweme_id, len(body), body.count("[图片]")))
    print("开头：", body[:120].replace("\n", " / "))
    print("结尾：", body[-120:].replace("\n", " / "))

    con = sqlite3.connect(str(DB))
    dup = con.execute("SELECT source_sheet, source_row FROM videos WHERE aweme_id=?", (aweme_id,)).fetchone()
    have = con.execute("SELECT count(*) FROM videos WHERE source_sheet='微信文章'").fetchone()[0]
    con.close()
    if dup:
        print("\n库里已有这一篇（%s R%s），不重复写" % (dup[0], dup[1]))
        return 0
    print("\n当前「微信文章」共 %d 行 → 这一篇会落成 R%d" % (have, have + 1))
    if not a.commit:
        print("预演结束（加 --commit 才写库）")
        return 0

    bak = ROOT / "output" / "backups" / ("video2text-before-wximport-%s.db" % time.strftime("%Y%m%d-%H%M%S"))
    bak.parent.mkdir(exist_ok=True)
    shutil.copy2(DB, bak)
    rows = content_store.insert_wechat_articles(
        DB, [art], category=a.category, game=a.game)
    if not rows:
        print("✗ 没写进去（函数返回空）")
        return 1
    r = rows[0]
    con = sqlite3.connect(str(DB))
    vid = con.execute("SELECT id, category, game FROM videos WHERE aweme_id=?", (aweme_id,)).fetchone()
    con.close()
    print("✓ 已入库：video_id=%s　%s R%s　category=%s　game=%s" % (vid[0], r["source_sheet"], r["source_row"], vid[1], vid[2]))
    print("  备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
