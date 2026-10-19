# -*- coding: utf-8 -*-
"""走一遍「运营周报」这一页：把他真会读到的每一处文字原样打出来 + 截图，并当场验三条口径。

为什么单独写一个：这张表 2026-10-14 开始有三个渠道的数混在一起，
"哪一列在哪个渠道根本没有" 这种事看代码看不出来，得看渲染出来的格子。
判据跟后端同源（/api/ops/report 下发的 columns.na），不是拿上次回传的标记当现状。

用法：python3 scripts/agent/ui_ops_walk.py [--out temp/ops_walk.png]
"""
import argparse
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"


def api(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/ops_walk_1014.png")
    args = ap.parse_args()

    d = api("/api/ops/report")
    cols = d["columns"]
    arts = d["articles"]
    na_map = {c["key"]: set(c.get("na") or []) for c in cols}
    fails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1500, "height": 1050})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/#ops", wait_until="domcontentloaded")
        pg.wait_for_selector("#ops-rows tr td input", timeout=40000)
        pg.wait_for_timeout(700)

        head = pg.eval_on_selector_all("#ops-head th", "es=>es.map(e=>e.innerText.trim())")
        pills = pg.eval_on_selector_all("#ops-rows .rv-pill", "es=>es.map(e=>e.innerText.trim())")
        nrows = pg.eval_on_selector_all("#ops-rows tr", "es=>es.length")
        why = pg.eval_on_selector("#ops-why, .ops-why", "e=>e.innerText")
        filt = pg.eval_on_selector_all("#ops-filter option", "es=>es.map(e=>e.textContent) ")
        summary = pg.inner_text("#ops-summary")
        # 每行：渠道、标题、每格是"输入框(值)"还是"—"
        grid = pg.evaluate("""() => Array.from(document.querySelectorAll('#ops-rows tr')).map(tr =>
            ({title: (tr.querySelector('td .t')||{}).title || '',
              cells: Array.from(tr.querySelectorAll('td')).slice(2, -1).map(td => {
                const i = td.querySelector('input');
                return i ? (i.value === '' ? '空' : i.value) : td.innerText.trim();
              })}))""")
        pg.screenshot(path=args.out, full_page=True)

        # ① 表头列数 = 后端下发列数 + 标题/发布日/来源
        if len(head) != len(cols) + 3:
            fails.append("表头列数 %d ≠ 后端 %d 列+3" % (len(head), len(cols)))
        # ② 没有那个指标的渠道必须是「—」，有的必须给输入框（不许拿 0 顶）
        by_title = {g["title"]: g for g in grid}
        for a in arts:
            g = by_title.get(a.get("title") or "")
            if not g:
                continue
            for j, c in enumerate(cols):
                cell = g["cells"][j] if j < len(g["cells"]) else "?"
                is_na = a["channel"] in na_map[c["key"]]
                if is_na and cell != "—":
                    fails.append("%s/%s 这列 %s 根本没有，界面却给了格子（%s）"
                                 % (a["channel"], a["ref_id"], c["key"], cell))
                if not is_na and cell == "—":
                    fails.append("%s/%s 有 %s 这个指标，界面画成了「—」"
                                 % (a["channel"], a["ref_id"], c["key"]))
        # ③ 头条的转发没抓到，不许出现 0
        tt = [a for a in arts if a["channel"] == "toutiao"]
        if any(a.get("shares") not in (None, "") for a in tt):
            fails.append("头条 shares 有值：接口 repin 全 0，这个数不该写进库")
        # ④ 右上角控件挤不换行时，按钮会被压成一列一个字（1808px 截图实测过）。
        #    单行 .btn.sm 实测 37px，换行后 60+，所以按高度判就够。
        hs = pg.evaluate("""() => Array.from(document.querySelectorAll('#page-ops .header .btn'))
              .map(b => Math.round(b.getBoundingClientRect().height))""")
        print("右上按钮高度 %s，那一行高 %dpx" % (
            hs, int(pg.evaluate("document.querySelector('#page-ops .header')"
                                ".getBoundingClientRect().height"))))
        for h in hs:
            if h > 46:
                fails.append("按钮高 %dpx，文案被挤成多行了" % h)
        print("表头：" + " | ".join(head))
        print("渲染行数 %d（库里 %d 条：%s）" % (
            nrows, len(arts), json.dumps({k: sum(1 for a in arts if a["channel"] == k)
                                          for k in sorted({a["channel"] for a in arts})},
                                         ensure_ascii=False)))
        print("筛选框：" + " / ".join(filt))
        print("汇总行：" + summary.replace("\n", " ⏎ "))
        print("来源栏前 6 条：" + " ⏎ ".join(pills[:6]))
        print("说明段：" + why.replace("\n", " ⏎ ")[:600])
        for a in arts[:3]:
            g = by_title.get(a.get("title") or "")
            print("样例 %s｜%s → %s" % (a["channel"], (a.get("title") or "")[:18],
                                        " ".join(str(x) for x in (g or {}).get("cells", []))))
        # 勾上「只看这一周」应当只剩本周的行。
        pg.click("#ops-week-only")
        pg.wait_for_timeout(300)
        # 只数有输入框的行：本周还没发过东西时表格里是那一格占位提示（也是一个 tr），
        # 拿 tr 总数比后端行数会在"新的一周刚开始"这天误报（2026-10-19 就是这么红了一次）
        wk = pg.eval_on_selector_all("#ops-rows tr", "es=>es.filter(t=>t.querySelector('input')).length")
        want = len([a for a in arts if a.get("week") == d["report"]["week"]])
        placeholder = pg.eval_on_selector_all(
            "#ops-rows tr", "es=>es.filter(t=>!t.querySelector('input'))"
                            ".map(t=>t.innerText.trim())")
        print("勾「只看这一周」→ %d 行（后端算 %d 行，周 %s）占位：%s"
              % (wk, want, d["report"]["week"], " / ".join(placeholder)[:40]))
        if wk != want:
            fails.append("「只看这一周」筛出 %d 行，后端是 %d 行" % (wk, want))
        browser.close()

    if errs:
        fails.append("页面 JS 报错：" + " ; ".join(errs[:3]))
    print("截图：" + args.out)
    if fails:
        print("发现 %d 处问题：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("OPS_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
