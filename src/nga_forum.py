# -*- coding: utf-8 -*-
"""nga_forum.py — 抓 NGA「魔兽世界：无限」板块（fid=510569）入库，作**选题素材**

为什么加这一层（2026-10-19 号主定的）：号主要在开服窗口里知道"玩家在问什么、
哪块资料缺答案"。官网公告（`blizzard_news.py`）只有官方口径，玩家侧的真实疑问
在 NGA 这座板块里。库里此前**没有任何论坛来源**（实测 source_sheet 只有
抖音视频数据 / B站视频 / 微信文章 / 抖音图文 / 暴雪官方 / AI面试）。

实测过的取法（2026-10-18/19 本机跑通，两条通道差别很大）：
  · **访客态**：`thread.php` 与 `read.php` 一律 403（响应体标题「访客不能直接访问」，
    PHPWind 的 ERROR:15）。带服务端发的访客 cookie 两步重试仍 403。
    唯一稳的访客入口是 App 只读接口 `app_api.php?__lib=subject&__act=hot&fid=…`，
    固定 ~32 条、覆盖最近 24 小时，**加任何分页参数会直接返回 0 条**（不是报错）。
  · **登录态**（本模块走这条）：列表能按发帖时间倒序翻页（页面里 `var __PAGE` 给出板块
    总页数，实测 289→291 页），正文在 `<p id='postcontent0' class='postcontent …'>`，
    标题 `<h1 id='currentTopicName'>`、首楼时间 `<span id='postdate0'>`。
  · **响应是 GBK**，requests 的 `r.text` 会乱码，必须按 `r.content` 自己解。
  · **302 不是失败**：PHPWind 用 302 刷新 `lastvisit`/`lastpath`，所以必须
    `allow_redirects=True` 且带同一个 `Session`（把服务端轮换的会话值带回去）。
    第一版没这么做，75 条只落了 27 条正文，看起来像被风控。

登录 cookie 只从仓库外读，顺序：环境变量 `NGA_COOKIE_FILE` → `~/.nga/cookie.txt`。
**永远不要把 cookie 写进 config.env 提交**（本仓库纪律 3：运行数据与密钥不入库）。

用途边界（这条是硬线，别在下游忘了）：帖子正文是**玩家原创**。按 wow-forever 仓库根
AGENTS.md 第五节的第三方取数边界，论坛内容**只当选题参考，不成段引用、不改写冒充本站观点、
不进小程序**。入库时在 remark 与 category 上把这个标记带着，下游取素材时能看见。

Python 3.9 兼容：不用 match / X|Y 语法。
"""
import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

FID = 510569
FORUM_NAME = "魔兽世界：无限"
BASE = "https://bbs.nga.cn"
SHEET = "NGA无限板块"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
# 座间：热帖里那个攻略索引帖作者自述"发帖太集中触发了系统风控，新帖发出会被自动删除"。
# 这是拿号主账号信用换数据，节流不是礼貌问题。
REQUEST_GAP_SEC = 1.5
MAX_BODY_PER_RUN = 40
CONSECUTIVE_FAIL_STOP = 6          # 连续失败就收手，别把头铁下去

USAGE_NOTE = "论坛素材：只当选题参考，不得成段引用或改写冒充本站观点"

ROW_RE = re.compile(
    r"<td class='c1'><a id='t_rc[^']*'[^>]*href='/read\.php\?tid=(?P<tid>\d+)'[^>]*>(?P<replies>[\d*]+)</a></td>"
    r".*?class='topic'[^>]*>(?P<subject>.*?)</a>"
    r".*?class='author'[^>]*title='用户ID (?P<uid>\d+)'>(?P<author>.*?)</a>"
    r"<span class='silver postdate'[^>]*>(?P<postdate>\d+)</span>",
    re.S)


def post_id(url: str) -> str:
    """去重键，跟暴雪官方那一路同法：`nf` + URL 的 md5 前 16 位。"""
    return "nf" + hashlib.md5(str(url or "").encode("utf-8")).hexdigest()[:16]


def _clean(s: str) -> str:
    s = re.sub(r"\[/?[a-zA-Z][^\]\n]{0,40}\]", " ", s or "")   # UBB 标签
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _decode(resp: requests.Response) -> str:
    """NGA 发的是 GBK。直接取 .text 会得到乱码，所以按字节自己解。"""
    return resp.content.decode("gbk", "replace")


def cookie_pairs() -> Dict[str, str]:
    path = os.environ.get("NGA_COOKIE_FILE") or os.path.expanduser("~/.nga/cookie.txt")
    if not os.path.exists(path):
        raise RuntimeError(
            "读不到 NGA 登录 cookie（找 %s）。board 模式必须要登录态；"
            "写法：登录 bbs.nga.cn 后从浏览器请求头复制整行 Cookie 存进该文件，chmod 600。" % path)
    raw = Path(path).read_text(encoding="utf-8").strip()
    out = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if "=" in seg:
            k, v = seg.split("=", 1)
            out[k.strip()] = v.strip()
    if "ngaPassportUid" not in out or "ngaPassportCid" not in out:
        raise RuntimeError("%s 里缺 ngaPassportUid / ngaPassportCid，登录态不完整" % path)
    return out


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for k, v in cookie_pairs().items():
        s.cookies.set(k, v, domain=".nga.cn")
    return s


def fetch_list(pages: int = 2, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """板块列表按发帖时间倒序翻页。返回 [{tid,url,title,author,authorid,postdate,replies}]"""
    s = session or _session()
    out: List[Dict[str, Any]] = []
    total_pages = None
    for p in range(1, max(1, pages) + 1):
        url = "%s/thread.php?fid=%d&order_by=postdatedesc&page=%d&rand=%d" % (
            BASE, FID, p, int(time.time()) % 900 + 10 + p)
        r = s.get(url, timeout=25, allow_redirects=True,
                  headers={"Referer": "%s/thread.php?fid=%d" % (BASE, FID)})
        if r.status_code != 200:
            print("[NGA] 第 %d 页 HTTP %d，停在这里" % (p, r.status_code), flush=True)
            break
        html = _decode(r)
        if "访客不能直接访问" in html:
            print("[NGA] 第 %d 页又被当访客拦了 —— cookie 大概率过期，重新从浏览器复制" % p, flush=True)
            break
        if total_pages is None:
            m = re.search(r"var __PAGE = \{[^}]*?1:(\d+)", html)
            total_pages = int(m.group(1)) if m else 0
            print("[NGA] 板块按发帖时间倒序共 %s 页" % (total_pages or "?"), flush=True)
        rows = []
        for m in ROW_RE.finditer(html):
            rep = m.group("replies")
            rows.append({
                "tid": int(m.group("tid")),
                "url": "%s/read.php?tid=%s" % (BASE, m.group("tid")),
                "title": _clean(m.group("subject")),
                "author": _clean(m.group("author")),
                "authorid": int(m.group("uid")),
                "postdate": int(m.group("postdate")),
                "replies": 0 if "*" in rep else int(rep or 0),
            })
        seen = set()
        rows = [x for x in rows if not (x["tid"] in seen or seen.add(x["tid"]))]
        if not rows:
            print("[NGA] 第 %d 页解析到 0 行 —— 板块改版了，先别加量，回去核 ROW_RE" % p, flush=True)
            break
        out.extend(rows)
        print("[NGA] 第 %d 页：%d 条" % (p, len(rows)), flush=True)
        time.sleep(REQUEST_GAP_SEC)
    return out


def fetch_detail(tid: int, session: requests.Session) -> Dict[str, Any]:
    url = "%s/read.php?tid=%d&rand=%d" % (BASE, tid, int(time.time()) % 900 + 10)
    r = session.get(url, timeout=25, allow_redirects=True,
                    headers={"Referer": "%s/thread.php?fid=%d" % (BASE, FID)})
    html = _decode(r) if r.status_code == 200 else ""
    if r.status_code != 200 or "访客不能直接访问" in html:
        raise RuntimeError("HTTP %s 或被访客拦截" % r.status_code)
    if "帖子被设为隐藏" in html:
        return {"hidden": True, "body": "", "title": ""}

    def grab(pat: str) -> str:
        m = re.search(pat, html, re.S)
        return _clean(m.group(1)) if m else ""

    return {"hidden": False,
            "title": grab(r"<h1 id='currentTopicName'[^>]*>(.*?)</h1>"),
            "forum": grab(r"<h2 id='currentForumName'[^>]*>(.*?)</h2>"),
            "author": grab(r"<a[^>]*class='author'[^>]*>(.*?)</a>"),
            "date": grab(r"<span id='postdate0'[^>]*>(.*?)</span>"),
            "body": grab(r"<p id='postcontent0' class='postcontent[^']*'>(.*?)</p>")}


def known_ids(db_path: str) -> set:
    import sqlite3
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        return {r[0] for r in conn.execute(
            "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL", (SHEET,))}
    finally:
        conn.close()


def insert(db_path: str, items: List[Dict[str, Any]]) -> List[str]:
    """直写 videos 表。不去改 content_store.py（别的会话正在写它），
    字段口径照 `insert_official_news`：正文空的记「未开始」，不拿导航文案凑数。"""
    if not items:
        return []
    import sqlite3
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    inserted: List[str] = []
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT aweme_id FROM videos WHERE source_sheet = ?", (SHEET,))}
        max_row = conn.execute(
            "SELECT COALESCE(MAX(source_row), 0) FROM videos WHERE source_sheet = ?",
            (SHEET,)).fetchone()[0]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        row_num = int(max_row)
        for it in items:
            aid = it["aweme_id"]
            if aid in existing:
                continue
            row_num += 1
            body = it.get("transcript") or ""
            conn.execute(
                """INSERT INTO videos (
                       aweme_id, source_sheet, source_row, source_url, cover_url,
                       author, title, status, published_at, transcript, remark,
                       category, game, source, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (aid, SHEET, row_num, it["url"], "",
                 it.get("author", ""), it["title"], "已完成" if body else "未开始",
                 it.get("published_at", ""), body, USAGE_NOTE,
                 "论坛选题", "魔兽世界：无限", "forum", stamp))
            inserted.append(aid)
        conn.commit()
    finally:
        conn.close()
    return inserted


def sync(db_path: str, pages: int = 2, limit: int = MAX_BODY_PER_RUN,
         dry_run: bool = False) -> Dict[str, Any]:
    """抓列表 → 跳过库里已有的 → 逐条取正文 → 入库。返回统计，错误不静默。"""
    s = _session()
    items = fetch_list(pages=pages, session=s)
    if not items:
        return {"error": "列表一条都没解析出来（改版或 cookie 失效），本轮没动库", "found": 0}
    uniq: Dict[int, Dict[str, Any]] = {}
    for it in items:
        uniq.setdefault(it["tid"], it)
    rows = list(uniq.values())
    have = set() if dry_run else known_ids(db_path)
    todo = [r for r in rows if post_id(r["url"]) not in have][:limit]
    print("[NGA] 列表 %d 条（去重后），库里已有 %d 条，本轮取 %d 条正文"
          % (len(rows), len(have), len(todo)), flush=True)
    if not todo:
        return {"found": len(rows), "new": 0, "detail_ok": 0, "errors": []}

    fetched, errors = [], []
    fails = 0
    for n, r in enumerate(todo):
        try:
            d = fetch_detail(r["tid"], s)
            fails = 0
            if d["hidden"]:
                errors.append("tid=%s 作者设为隐藏" % r["tid"])
            title = d["title"] or r["title"]
            body = d["body"]
            if not body:
                errors.append("tid=%s 正文为空（只登记标题）" % r["tid"])
            fetched.append({"aweme_id": post_id(r["url"]), "url": r["url"],
                            "title": title, "author": d["author"] or r["author"],
                            "published_at": d["date"] or time.strftime(
                                "%Y-%m-%d", time.localtime(r["postdate"])),
                            "transcript": body})
            print("[NGA] 正文 %d/%d｜%s｜%d 字" % (n + 1, len(todo), title[:22], len(body)), flush=True)
        except Exception as e:
            fails += 1
            errors.append("tid=%s：%s" % (r["tid"], str(e)[:80]))
            if fails >= CONSECUTIVE_FAIL_STOP:
                errors.append("连续 %d 次失败，本轮主动收手（继续硬打会把这个账号打进风控）" % fails)
                print("[NGA] 连续失败 %d 次，停止本轮" % fails, flush=True)
                break
        time.sleep(REQUEST_GAP_SEC)

    if dry_run:
        return {"found": len(rows), "new": len(fetched), "dry_run": True,
                "sample": [{"title": x["title"][:30], "chars": len(x["transcript"])}
                           for x in fetched[:3]], "errors": errors}
    ins = insert(db_path, fetched)
    print("[NGA] 入库完成：新增 %d 条（档位「%s」）" % (len(ins), SHEET), flush=True)
    return {"found": len(rows), "new": len(ins), "detail_ok": len(fetched), "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓 NGA 无限板块入库（选题素材，不是内容源）")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "output" / "video2text.db"))
    ap.add_argument("--pages", type=int, default=2, help="列表翻几页（默认 2）")
    ap.add_argument("--limit", type=int, default=MAX_BODY_PER_RUN, help="本轮最多抓几条正文")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写库")
    a = ap.parse_args(argv)
    r = sync(a.db, pages=a.pages, limit=a.limit, dry_run=a.dry_run)
    print("\n结果：%s" % {k: v for k, v in r.items() if k != "sample"})
    if r.get("sample"):
        print("样本：%s" % r["sample"])
    print("提醒：%s" % USAGE_NOTE)
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
