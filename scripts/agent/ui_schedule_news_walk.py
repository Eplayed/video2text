# -*- coding: utf-8 -*-
"""排产页顶部「暴雪官方公告」那一栏的走查（静态页，0 元、不调 AI）。

号主 2026-10-17 定的：头条号与小红书的发文优先在排产表里体现，他也会从排产表里挑平台。
所以一手公告必须在这张表上看得见、点得进素材库——不然它只是库里的 12 行数据，不是选题。
断言四件：栏出现了且条数对得上接口、每行有日期/作者/字数、「打开」真的走 gotoMaterial
（独立打开时是 window.open('/?open=sheet:row')，嵌在工作台 iframe 里是 postMessage），
以及整页没有 JS 报错。
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
fails = []


def ck(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (("　" + detail) if detail else ""))
    if not ok:
        fails.append(name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1420, "height": 980})
    errs = []
    # 报错只算"脚本级"的：资源 404 单独用响应状态查，并且放行全站共有的 favicon
    # （那条会话的走查也记了同一句"唯一 404 是 favicon"，混在 console 里会误判成页面坏了）
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error"
          and "Failed to load resource" not in (m.text or "") else None)
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:120]))
    pg.on("response", lambda r: errs.append("HTTP %d %s" % (r.status, r.url[:60]))
          if r.status >= 400 and "favicon" not in r.url else None)
    # 全站共有的 favicon 404 不算问题（别的会话那轮走查也记了这条），单独放行
    def _bad(m):
        return m.type == "error" and "favicon" not in (m.text or "")
    # 把 window.open 打桩，才能看清「打开」到底指向哪儿（不真开新标签）。
    # 注意 add_init_script 收的是**脚本源码**不是函数：写成 "() => {...}" 会被求值成
    # 一个没人调的函数、桩根本没装上（上一版就是这么误判成"按钮没反应"的），必须是 IIFE。
    pg.add_init_script("(() => { window.__opened = []; window.open = (u) => "
                       "{ window.__opened.push(u); return null; }; })()")
    pg.goto(BASE + "/static/schedule.html", wait_until="domcontentloaded")
    pg.wait_for_function("() => document.getElementById('newsbox') && "
                         "document.getElementById('newsbox').style.display !== 'none'", timeout=30000)
    pg.wait_for_timeout(1200)
    api = pg.evaluate("() => fetch('/api/videos?source=blizzard&page_size=12').then(r=>r.json()).then(d=>(d.videos||[]).length)")
    got = pg.evaluate("""() => {
        const rows=[...document.querySelectorAll('#newslist .nrow')];
        return {n: rows.length,
                first: rows.length ? rows[0].innerText.replace(/\\s+/g,' ').slice(0,80) : '',
                dated: rows.filter(r=>/20\\d\\d-\\d\\d-\\d\\d/.test(r.innerText)).length,
                withLen: rows.filter(r=>/\\d+字/.test(r.innerText)).length,
                btns: rows.length ? rows[0].querySelectorAll('button').length : 0,
                info: document.getElementById('newsinfo').innerText.slice(0,60)};
    }""")
    print("一手公告栏：接口 %d 条 → 页面 %d 行" % (api, got["n"]))
    ck("页面条数与接口一致", got["n"] == api and got["n"] > 0, "接口 %d / 页面 %d" % (api, got["n"]))
    ck("每行都带日期与字数", got["dated"] == got["n"] and got["withLen"] == got["n"],
       "带日期 %d、带字数 %d" % (got["dated"], got["withLen"]))
    ck("每行有「打开 / 复制标题」两个按钮", got["btns"] == 2, "%d 个" % got["btns"])
    print("   首行：%s" % got["first"])
    print("   说明行：%s" % got["info"])
    ck("说明行报了最新一条的日期", "最新一条" in got["info"], got["info"])

    pg.locator("#newslist .nrow button").first.click()      # 真点「打开」
    pg.wait_for_timeout(500)
    opened = pg.evaluate("() => window.__opened")
    ck("点「打开」真的跳到素材库那张卡", bool(opened) and "?open=" in opened[0],
       (opened or ["没反应"])[0][:64])

    # 嵌进工作台 iframe 的那条路：应该改成 postMessage，不开新标签
    frame = b.new_page(viewport={"width": 1420, "height": 980})
    frame.on("console", lambda m: errs.append(m.text) if m.type == "error"
             and "Failed to load resource" not in (m.text or "") else None)
    frame.goto(BASE + "/", wait_until="domcontentloaded")
    frame.wait_for_timeout(2500)
    frame.evaluate("""() => { window.__msgs = [];
        window.addEventListener('message', e => window.__msgs.push(e.data)); }""")
    frame.evaluate("""() => new Promise(res => {
        const f = document.createElement('iframe');
        f.id = 'sch'; f.src = '/static/schedule.html';
        f.style.cssText = 'width:1200px;height:800px';
        f.onload = () => setTimeout(res, 2500);
        document.body.appendChild(f); })""")
    inner = frame.frame_locator("#sch")
    inner.locator("#newslist .nrow button").first.click()
    frame.wait_for_timeout(800)
    got_msgs = frame.evaluate("() => (window.__msgs || []).map(m => m && m.v2t ? m.v2t + ':' + (m.sheet || '') : String(m))")
    ck("被工作台嵌入时改用 postMessage（不再开新标签）",
       any(str(x).startswith("openMaterial") for x in got_msgs), " / ".join(got_msgs[:3]) or "没收到消息")

    pg.screenshot(path="temp/schedule_news_walk.png")
    ck("整页无 JS 报错（favicon 404 除外）", not errs, "; ".join(errs[:2]))
    b.close()

print("SCHEDULE_NEWS_WALK_" + ("OK" if not fails else "FAIL：" + "、".join(fails)))
sys.exit(0 if not fails else 1)
