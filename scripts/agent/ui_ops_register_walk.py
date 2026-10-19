# -*- coding: utf-8 -*-
"""走一遍「运营周报」的发布登记：从界面上真点一次，验证台账里出现的行与后端同源，测完把走查行删干净。

为什么要有这一份（2026-10-18）：5180 那个运营层停了，发布登记搬到 15801，
新增的是「一个入口 + 一列 url + 一份从旧台账整本导入的历史」。
接上数据源不等于用户看得见，所以这里全部按他真会走的动作判：
在页面上填 → 点登记 → 读接口核对 → 再点一次看会不会长出重复行 → 空标题看报错说的是不是人话。

用法：python3 scripts/agent/ui_ops_register_walk.py [--out temp/ops_register_walk.png]
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
DB = "output/video2text.db"
MARK = "[走查]登记验证-请忽略"


def api(path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=(json.dumps(body).encode("utf-8") if body is not None else None),
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


def find_row(articles, ref_id):
    return next((a for a in articles if a.get("ref_id") == ref_id), None)


def wait_for(cond, tries=24, gap=0.5):
    """等一个界面/接口状态真的变了再看断言。

    这页现在 136 行，首屏 fetch 完要一两秒；上一版只 wait 了 800ms 就开始数「原文」，
    抓到的是「加载中...」那个空壳，报了 9 条假问题（跟 10-15 那次拿旧产物当现状同一类坑）。
    """
    for _ in range(tries):
        if cond():
            return True
        time.sleep(gap)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/ops_register_walk.png")
    args = ap.parse_args()

    fails = []
    base, _ = api("/api/ops/report")
    arts0 = base.get("articles") or []
    n0 = len(arts0)
    cols = base.get("columns") or []
    want_links = len([a for a in arts0 if (a.get("url") or "").startswith("http")])
    today = None
    registered = []      # (channel, ref_id) 走查期间写的行，结尾删掉

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1500, "height": 1100})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/#ops", wait_until="domcontentloaded")
        # 这页现在 136 行，首屏 fetch 要一两秒。等"表格里出现输入框"才算渲染完：
        # 上一版只等 tr，抓到的是「加载中...」那个空壳，于是报了 9 条假问题
        pg.wait_for_selector("#ops-rows tr td input", timeout=60000)
        pg.wait_for_timeout(600)
        if not wait_for(lambda: pg.eval_on_selector_all(
                "#ops-reg-channel option", "es=>es.length") > 0):
            fails.append("首屏没把渠道下拉填上（接口没回或渲染没跑）")

        # ① 那一行控件在不在、渠道下拉是不是后端下发（不是前端抄的三个名字）
        for sel in ("#ops-reg-channel", "#ops-reg-title", "#ops-reg-date", "#ops-reg-url"):
            if not pg.query_selector(sel):
                fails.append("登记表单缺控件：" + sel)
        ch_opts = pg.eval_on_selector_all("#ops-reg-channel option", "es=>es.map(e=>e.value)")
        want_ch = [c["key"] for c in (base.get("channels") or [])]
        if ch_opts and ch_opts != want_ch:
            fails.append("渠道下拉 %s 与后端 %s 不一致" % (ch_opts, want_ch))
        # ② 旧入口要真的收掉了（同一件事不许留两个入口）
        if pg.query_selector("#ops-add-channel") or pg.evaluate(
                "() => !!document.querySelector('#page-ops .header') && "
                "Array.from(document.querySelectorAll('#page-ops .header .btn'))"
                ".some(b => (b.textContent||'').indexOf('加一行') >= 0)"):
            fails.append("旧的「＋ 加一行」还在，两个入口并着")
        # ③ 日期默认今天：拿页面本地日期比，不看 UTC（凌晨登记写成昨天就是 toISOString 那个坑）
        today = pg.evaluate("() => { const n=new Date();"
                            "return n.getFullYear()+'-'+('0'+(n.getMonth()+1)).slice(-2)"
                            "+'-'+('0'+n.getDate()).slice(-2); }")
        got_date = pg.eval_on_selector("#ops-reg-date", "e=>e.value")
        if got_date != today:
            fails.append("登记日期默认 %s，页面本地今天是 %s" % (got_date, today))
        # ④ 「原文」链接数 = 接口里有 url 的行数（同源判据，不数 HTML 里的 a 标签个数碰运气）
        got_links = pg.eval_on_selector_all("#ops-rows a.src", "es=>es.length")
        if got_links != want_links:
            fails.append("界面画了 %d 个「原文」，接口说有 %d 行带链接" % (got_links, want_links))
        # ⑤ 没加新列（走查按列位取格子，多一列会把 ui_ops_walk 的断言打歪）
        n_head = pg.eval_on_selector_all("#ops-head th", "es=>es.length")
        if n_head != len(cols) + 3:
            fails.append("表头 %d 列 ≠ 后端 %d 列 + 3" % (n_head, len(cols)))
        # ⑥ 右上那排按钮不许被挤成多行
        hs = pg.evaluate("() => Array.from(document.querySelectorAll('#page-ops .header .btn'))"
                         ".map(b => Math.round(b.getBoundingClientRect().height))")
        for h in hs:
            if h > 46:
                fails.append("按钮高 %dpx，文案被挤成多行了" % h)

        # ⑦ 真点一次登记
        pg.fill("#ops-reg-title", MARK)
        pg.fill("#ops-reg-url", "https://example.com/walk")
        pg.click("#ops-reg .btn")
        wait_for(lambda: "已记上" in pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText"))
        hint = pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText")
        if "已记上" not in hint:
            fails.append("登记后提示写的不是成功：「%s」" % hint)
        wait_for(lambda: len((api("/api/ops/report")[0].get("articles") or [])) == n0 + 1)
        arts1, _ = api("/api/ops/report")
        rows1 = arts1.get("articles") or []
        if len(rows1) != n0 + 1:
            fails.append("登记后台账 %d 行，应为 %d 行" % (len(rows1), n0 + 1))
        row = next((a for a in rows1 if (a.get("title") or "") == MARK), None)
        if not row:
            fails.append("接口里找不到刚登记那条")
        else:
            registered.append((row["channel"], row["ref_id"]))
            if row.get("source") != "人工登记":
                fails.append("来源应是「人工登记」，实际「%s」" % row.get("source"))
            if row.get("pub_date") != today:
                fails.append("发布日期 %s，应 %s" % (row.get("pub_date"), today))
            if (row.get("url") or "") != "https://example.com/walk":
                fails.append("链接没落库：%r" % row.get("url"))
            # 数字列必须留空，不许被填 0
            for k in ("reads", "show", "looks", "comment", "saves", "shares",
                      "net_follow", "revenue"):
                if row.get(k) not in (None, ""):
                    fails.append("数字列 %s 被写成了 %s（登记时不该有数）" % (k, row.get(k)))
        # 界面上这一行也应当看得见、带原文链接
        js_seen = ("es=>es.filter(t=>(t.innerText||'').indexOf('" + MARK + "')>=0)"
                   ".map(t=>({txt:t.innerText.replace(/\\n/g,' '),link:!!t.querySelector('a.src')}))")
        wait_for(lambda: len(pg.eval_on_selector_all("#ops-rows tr", js_seen)) > 0)
        seen = pg.eval_on_selector_all("#ops-rows tr", js_seen)
        if not seen:
            fails.append("登记完表格里找不到那条（没刷出来）")
        elif not seen[0]["link"]:
            fails.append("登记的行有链接却没画「原文」")

        # ⑧ 连点两次不许长两行（同渠道同标题同一天复用同一把键）
        pg.fill("#ops-reg-title", MARK)
        pg.click("#ops-reg .btn")
        wait_for(lambda: "本来就有" in pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText"))
        arts2, _ = api("/api/ops/report")
        rows2 = arts2.get("articles") or []
        dup = [a for a in rows2 if (a.get("title") or "") == MARK]
        if len(rows2) != n0 + 1 or len(dup) != 1:
            fails.append("重复登记长成了 %d 行（同标题 %d 条），应为 1 条" % (len(rows2) - n0, len(dup)))
        hint2 = pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText")
        if "本来就有" not in hint2:
            fails.append("第二次登记的提示没说明是补链接：「%s」" % hint2)

        # ⑨ 空标题要点名报错，不能静默
        pg.fill("#ops-reg-title", "")
        pg.click("#ops-reg .btn")
        wait_for(lambda: "没登记上" in pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText"))
        hint3 = pg.eval_on_selector("#ops-reg-hint", "e=>e.innerText")
        if "标题不能空" not in hint3:
            fails.append("空标题的提示不是人话：「%s」" % hint3)
        arts3, _ = api("/api/ops/report")
        if len(arts3.get("articles") or []) != n0 + 1:
            fails.append("空标题那次也写进库了")

        pg.screenshot(path=args.out, full_page=True)
        summary = pg.inner_text("#ops-summary")
        why = pg.inner_text(".ops-why")
        reg_row_h = pg.eval_on_selector("#ops-reg", "e=>Math.round(e.getBoundingClientRect().height)")
        browser.close()

    # ⑩ 把走查那行删干净（只删我这一把键），并证明回到基线
    conn = sqlite3.connect(DB)
    for ch, rid in registered:
        conn.execute("DELETE FROM publish_stats WHERE channel=? AND ref_id=?", (ch, rid))
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM publish_stats WHERE title=?", (MARK,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM publish_stats").fetchone()[0]
    conn.close()
    if left or total != n0:
        fails.append("走查行没删干净：剩 %d 条，总数 %d（基线 %d）" % (left, total, n0))

    print("基线 %d 行 → 登记后 %d 行 → 删掉走查行后 %d 行" % (n0, n0 + 1, total))
    print("带「原文」链接的行：界面 %d / 接口 %d" % (got_links, want_links))
    print("渠道下拉：%s ｜ 登记日期默认：%s ｜ 登记那一行高 %dpx" % (ch_opts, got_date, reg_row_h))
    print("登记提示：1) %s ｜ 2) %s ｜ 空标题) %s" % (hint, hint2, hint3))
    print("汇总行：" + summary.replace("\n", " ⏎ ")[:300])
    print("说明段末尾：" + why.replace("\n", " ⏎ ")[-200:])
    if errs:
        fails.append("页面 JS 报错：" + " ; ".join(errs[:3]))
    print("截图：" + args.out)
    if fails:
        print("发现 %d 处问题：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("OPS_REGISTER_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
