# -*- coding: utf-8 -*-
"""toutiao_stats.py — 从头条号创作中心取「已发内容」的真实表现数据（只读）

为什么需要这一层：2026-10-06 实测，5180 台账里头条登记停在 09-17、而且只抓文章不抓图文，
于是「出了 11 组图文、到底发出去几组、有没有量」长期没答案。用户拍板：发布登记不要了，
数据直接看头条号网页——所以回流改成抓创作中心内容列表，**抓到什么就是发过什么**。

⚠️ 必须走真实浏览器，别改成纯 requests/urllib（已踩过）：
   `mp.toutiao.com/api/feed/mp_provider/v1/` 在页面上是「内容列表」，
   但用 urllib 裸调同一个 URL 拿到的是**随机推荐流**——实测每次调用返回不同 3 条、
   has_more 永远 true、offset 不生效、status=8 时直接返回 0 条。
   页面自己发的请求带齐了签名与上下文，只有拦它才可靠。取证脚本见 temp/probe_tt_page*.py。

cookie 来源两级：config/config.env.local 的 TOUTIAO_COOKIE（本仓库自己的，**现在走的就是这一级**，
2026-10-19 从 5180 那份搬过来），没有再退回两份兜底：`output/legacy-5180/toutiao-cookies.json`
（删仓库前归档的那份）与 `media-workbench/runtime/toutiao-cookies.json`（原仓库还在时）。

只读：只打开内容管理页、点分页、读响应；不点发布、不发任何写请求。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_LOCAL = ROOT / "config" / "config.env.local"
# 兜底顺序：归档那份（删了 5180 也还在）→ 原仓库那份（还在时）
COOKIE_FALLBACKS = [ROOT / "output" / "legacy-5180" / "toutiao-cookies.json",
                    Path.home() / "Documents" / "project" / "media-workbench"
                    / "runtime" / "toutiao-cookies.json"]
MW_COOKIES = COOKIE_FALLBACKS[1]      # 老名字留着，别处引用不炸
TT_DIR = ROOT / "output" / "toutiao"
SNAPSHOT = TT_DIR / "_platform_items.json"

MANAGE_URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
PAGINATION_NEXT = "li.fake-pagination-item-icon"   # 列表右下箭头；最后一个 = 下一页
MAX_PAGES = 40
SETTLE_MS = 1800


def load_cookie():
    # type: () -> tuple
    """返回 (cookie 文本, 来源说明)。两处都没有就抛错，让人去配，不要静默抓到空列表。"""
    if CONFIG_LOCAL.is_file():
        for line in CONFIG_LOCAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TOUTIAO_COOKIE") and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(val) > 100:
                    return val, "config/config.env.local:TOUTIAO_COOKIE"
    for path in COOKIE_FALLBACKS:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        val = (data.get("cookie") or "").strip()
        if len(val) > 100:
            return val, "%s（存于 %s）" % (path, str(data.get("savedAt") or "")[:10])
    raise RuntimeError("没找到头条 cookie：在 config/config.env.local 加一行 TOUTIAO_COOKIE="
                       "（浏览器 DevTools 里任一 mp.toutiao.com 请求的 cookie 头整段复制），"
                       "或把 output/legacy-5180/toutiao-cookies.json 放回原处")


def _to_cookies(header):
    out = []
    for part in header.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out.append({"name": k.strip(), "value": v.strip(), "domain": ".toutiao.com", "path": "/"})
    return out


def _row(cell):
    base = cell.get("articleBase") or {}
    counter = cell.get("itemCounter") or {}
    gid = str(base.get("gidStr") or "").strip()
    if not gid:
        return None
    ts = int(base.get("publishTime") or base.get("createTime") or 0)
    title = re.sub(r"\s+", "", str(base.get("title") or "")).strip()
    return {
        "gid": gid,
        "title": title,
        "url": str(base.get("displayURL") or base.get("articleURL") or
                   "https://www.toutiao.com/a%s/" % gid),
        "publish_ts": ts,
        "publish_date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
        "show": int(counter.get("showCount") or 0),
        "read": int(counter.get("readCount") or 0),
        "like": int(counter.get("diggCount") or 0),
        "comment": int(counter.get("commentCount") or 0),
        "repin": int(counter.get("repinCount") or 0),
    }


def collect(cookie_text=None, max_pages=MAX_PAGES, headless=True, progress_cb=None):
    """打开内容管理「全部」页，逐页点下一页，把接口返回的卡片收成 {gid: row}。

    返回 (rows, meta)。meta 里有 cookie 来源、抓到的页数、页面显示的总条数（「共 N 条内容」）。
    """
    from playwright.sync_api import sync_playwright

    text, source = (cookie_text, "调用方传入") if cookie_text else load_cookie()
    rows = {}
    meta = {"cookie_from": source, "pages": 0, "total_on_page": None, "errors": []}

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=headless)
        except Exception:
            browser = pw.chromium.launch(headless=headless)   # 没装系统 Chrome 就退回自带内核
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 1400})
        ctx.add_cookies(_to_cookies(text))
        page = ctx.new_page()

        def on_response(resp):
            if "mp_provider" not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return
            for it in (data.get("data") or []):
                cell = (it.get("assembleCell") or {}).get("itemCell") or {}
                row = _row(cell) if cell else None
                if row:
                    rows[row["gid"]] = row

        page.on("response", on_response)
        try:
            page.goto(MANAGE_URL, wait_until="networkidle", timeout=90000)
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            m = re.search(r"共\s*(\d+)\s*条内容", body)
            if m:
                meta["total_on_page"] = int(m.group(1))
            if "登录" in body[:400] and not rows:
                meta["errors"].append("页面像未登录：cookie 可能已过期，请重新复制 TOUTIAO_COOKIE")
            seen = len(rows)
            for _ in range(max_pages):
                nxt = page.locator(PAGINATION_NEXT).last
                if nxt.count() == 0 or "disabled" in (nxt.get_attribute("class") or ""):
                    break
                nxt.click()
                page.wait_for_timeout(SETTLE_MS)
                meta["pages"] += 1
                if progress_cb:
                    progress_cb("已抓 %d 页 / %d 条" % (meta["pages"] + 1, len(rows)))
                if len(rows) == seen:
                    break
                seen = len(rows)
        except Exception as e:
            meta["errors"].append("抓取中断：%s" % e)
        finally:
            browser.close()

    return sorted(rows.values(), key=lambda r: -r["publish_ts"]), meta


_TITLE_NOISE = re.compile(r"[\s，。、：:；;！？!?·\-—_《》（）()【】\[\]\"'“”'']+")


def _norm(title):
    return _TITLE_NOISE.sub("", title or "").lower()


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s} if s else set()


def score(local_title, platform_title):
    """平台标题常被人工改过、而且普遍比本地标题长（实测：本地「哀嚎洞穴全任务攻略：蓝色武器+10格背包」
    平台上是「哀嚎洞穴任务又多又乱？别怕，这篇攻略帮你理清所有任务线！蓝色武器…」）。
    所以主用**非包含系数**（本地二元组被平台覆盖的比例），Jaccard 只当惩罚项，
    否则长标题会把相似度稀释到认不出来。"""
    a, b = _norm(local_title), _norm(platform_title)
    if not a or not b:
        return 0.0
    ga, gb = _bigrams(a), _bigrams(b)
    cover = len(ga & gb) / float(len(ga) or 1)
    jaccard = len(ga & gb) / float(len(ga | gb) or 1)
    head = 1.0 if a[:6] and a[:6] in b else 0.0
    return 0.75 * cover + 0.15 * jaccard + 0.10 * head


def load_snapshot():
    """读上次的抓取快照，避免为了重新匹配再跑一次 40 秒的浏览器抓取。"""
    if not SNAPSHOT.is_file():
        return [], {}
    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except ValueError:
        return [], {}
    return data.get("rows") or [], data.get("meta") or {}


def match_packages(pkg_dir=None, rows=None, meta=None, threshold=0.30, use_snapshot=False):
    """把平台数据按标题匹配进本地每个图文包的 manifest.performance。

    未匹配的包（= 根本没发出去，或改题改到认不出）如实标 matched:false，
    并把平台最像的一条留在 suggest 里给人一眼确认，不假装匹配成功。"""
    pkg_dir = Path(pkg_dir) if pkg_dir else TT_DIR
    if rows is None:
        if use_snapshot:
            rows, meta = load_snapshot()
            if not rows:
                raise RuntimeError("没有抓取快照，先跑一次 collect()（点界面上的「同步头条数据」）")
        else:
            rows, meta = collect()
    rows = rows or []
    TT_DIR.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT:
        try:
            SNAPSHOT.write_text(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        except OSError:
            pass

    synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    matched, unmatched = [], []
    for mf in sorted(pkg_dir.glob("*/manifest.json")):
        if not mf.parent.name.isdigit():
            continue
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        local_created = str(man.get("created_at") or "")[:10]
        cands = sorted(((score(man.get("title") or "", r["title"]), r) for r in rows),
                       key=lambda x: (-x[0], -(x[1]["show"])))
        # 排序时再叠一层「发布时间落在出稿后 0-5 天」的弱加成：
        # 同一系列的两篇（例如两篇暗黑4）光靠标题会互相抢，日期能把它们分开。
        ranked = []
        for sc, r in cands[:8]:
            bonus = 0.0
            if local_created and r["publish_date"]:
                try:
                    d0 = datetime.strptime(local_created, "%Y-%m-%d")
                    d1 = datetime.strptime(r["publish_date"], "%Y-%m-%d")
                    delta = (d1 - d0).days
                    if 0 <= delta <= 5:
                        bonus = 0.08
                except ValueError:
                    pass
            ranked.append((sc + bonus, sc, r))
        ranked.sort(key=lambda x: -x[0])
        final, title_sc, hit = ranked[0] if ranked else (0.0, 0.0, None)
        margin = title_sc - (ranked[1][1] if len(ranked) > 1 else 0.0)
        confident = title_sc >= threshold and margin >= 0.06
        if hit and confident:
            perf = dict(hit)
            perf.update({"matched": True, "score": round(title_sc, 3), "synced_at": synced_at})
            matched.append({"package": mf.parent.name, "score": round(title_sc, 3), **hit})
        else:
            perf = {"matched": False, "synced_at": synced_at, "best_score": round(title_sc, 3),
                    "ambiguous": bool(hit) and title_sc >= threshold and margin < 0.06}
            if hit:
                perf["suggest"] = {"title": hit["title"], "url": hit["url"],
                                   "show": hit["show"], "read": hit["read"],
                                   "score": round(title_sc, 3)}
            unmatched.append({"package": mf.parent.name, "title": man.get("title") or "",
                              "created_at": man.get("created_at") or "",
                              "best_score": round(title_sc, 3),
                              "ambiguous": perf["ambiguous"],
                              "suggest": perf.get("suggest")})
        man["performance"] = perf
        mf.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")

    gids = {m["gid"] for m in matched}
    tops = sorted(rows, key=lambda r: -r["show"])[:8]
    return {"synced_at": synced_at, "meta": meta or {}, "platform_total": len(rows),
            "matched": matched, "unmatched": unmatched,
            "platform_top": tops, "platform_unlinked": [r for r in rows if r["gid"] not in gids][:30]}


LEDGER_DAYS = 30
_TT_TITLE_MAX = 48
_TT_HEAD = re.compile(r"^(.{4,%d}?)[。！？；·]" % _TT_TITLE_MAX)


def _ledger_title(row: dict, local_titles: dict) -> str:
    """台账那行的标题：能对上本地图文包的用包里的标题，对不上的（多半是随手发的微头条）
    用平台标题截断。头条图文在列表里没有独立标题，title 字段是「第一句 + 整段正文」，
    不截断会把表格撑爆——所以取到第一个句末标点为止，取不到再按字数截。"""
    t = local_titles.get(str(row.get("gid")))
    if t:
        return t
    raw = " ".join((row.get("title") or "").split())
    m = _TT_HEAD.match(raw)
    if m:
        return m.group(1)
    return raw[:_TT_TITLE_MAX] + "…" if len(raw) > _TT_TITLE_MAX else raw


def backfill_ledger(db_path: str, days: int = LEDGER_DAYS,
                    rows: Optional[list] = None) -> dict:
    """把头条后台的真实数字灌进运营台账 publish_stats（channel=toutiao）。

    为什么写在头条这一侧：read→reads、repin→shares 这套对照是头条口径，
    ops_store 只管存、不该认识平台。

    两条刻意的边界：
      · 只提交数字 + 日期 + 来源，**不碰 revenue / net_follow**——那两个数只有他后台看得到，
        upsert_article 只更新提交上来的键，所以人工填过的钱和涨粉不会被这次写入抹掉。
      · 标题只在新行第一次写。他手改过标题的话，第二天同步不会又变回平台那串。

    数字是**累计**不是当日：平台列表给的就是发布至今的总量，所以每天回填是把同一行的数
    往上刷，行上的 updated_at 表示"这个数截至什么时候"。
    """
    from . import ops_store

    if rows is None:
        rows, _meta = load_snapshot()
    if not rows:
        return {"written": 0, "new": 0, "error": "没有头条抓取快照，先点一次「同步头条数据」"}

    local_titles = {}
    for mf in sorted(TT_DIR.glob("*/manifest.json")):
        if not mf.parent.name.isdigit():
            continue
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        perf = man.get("performance") or {}
        if perf.get("matched") and perf.get("gid"):
            local_titles[str(perf["gid"])] = (man.get("title") or "").strip()

    floor = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    have = {r["ref_id"] for r in ops_store.list_articles(db_path, channel="toutiao")}
    out = {"written": 0, "new": 0, "since": floor, "error": ""}
    for r in rows:
        day = (r.get("publish_date") or "")[:10]
        if not day or day < floor:
            continue
        ref = "tt:%s" % r.get("gid")
        # 注意没有 shares：内容列表接口的 repin 字段 177 条实测全 0（2026-10-14 数过），
        # 那是"这个接口不给转发数"，不是"他转发都是 0"。写进去就成了瞎填的结论，
        # 所以转发那一列留给真看得到数的地方（单篇数据页）手填。
        fields = {"pub_date": day, "show": r.get("show"), "reads": r.get("read"),
                  "looks": r.get("like"), "comment": r.get("comment"),
                  "source": "头条后台"}
        if ref not in have:
            fields["title"] = _ledger_title(r, local_titles)
            out["new"] += 1
        ops_store.upsert_article(db_path, "toutiao", ref, fields)
        out["written"] += 1
    return out
