# -*- coding: utf-8 -*-
"""素材库 → 详情弹窗「素材画面」这一格的走查（真页面，不是拼字符串）。

号主 2026-10-15 指出我上一轮"改完了"是错的：后端接上了帧池，前端那一格从来没渲染过，
所以他那天打开工作台一张新图都看不到。这个脚本就干一件事——
**从素材库列表里双击那张卡**（真实入口），然后量弹窗里那一格到底有没有图、图有没有加载出来。
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
CASES = [("抖音视频数据", "373", "帧池未判帧（今天采的）", "shown"),
         ("抖音视频数据", "338", "帧池已判帧（clean）", "shown"),
         ("抖音视频数据", "866", "只有老 keyframes", "shown"),
         # 微信文章没有"从视频抽帧"这一说，这一格必须整块收起来，不许摆个空标题
         ("微信文章", "81", "微信文章（该没有这一格）", "hidden")]


def probe(pg, sheet, row, label, expect="shown", by_click=False):
    sel = '[ondblclick*=\'"%s"\']' % row      # 卡上的 ondblclick='openDetail("抖音视频数据","373")'
    if by_click and pg.locator(sel).count() == 0:
        print("  列表里没找到第 %s 行的卡（分页/筛选），改走函数入口" % row)
        by_click = False
    if by_click:
        pg.locator(sel).first.dblclick()      # 真实入口：双击卡片
    else:
        pg.evaluate("([s, r]) => openDetail(s, Number(r))", [sheet, row])
    if expect == "shown":
        pg.wait_for_function(
            "() => document.getElementById('modal-kf-section').style.display !== 'none'",
            timeout=15000)
    pg.wait_for_timeout(1200)
    info = pg.evaluate("""() => {
        const sec = document.getElementById('modal-kf-section');
        const imgs = [...document.querySelectorAll('#modal-kf img')];
        return {shown: getComputedStyle(sec).display !== 'none',
                title: document.getElementById('modal-kf-title').textContent,
                note: document.getElementById('modal-kf-note').innerText.slice(0, 46),
                n: imgs.length,
                loaded: imgs.filter(i => i.naturalWidth > 0).length,
                sizes: imgs.slice(0, 3).map(i => i.naturalWidth + '×' + i.naturalHeight
                        + '/' + Math.round(i.getBoundingClientRect().width) + 'px'),
                h: Math.round(sec.getBoundingClientRect().height)};
    }""")
    if expect == "hidden":
        ok = not info["shown"]
        print("%-30s 第 %s 行  → %s（这一格 display=%s）"
              % (label, row, "OK" if ok else "不该出现这一格",
                 "显示" if info["shown"] else "收起"))
        return ok
    ok = info["shown"] and info["n"] and info["loaded"] == info["n"]
    print("%-22s 第 %s 行  %s" % (label, row, "→ " + ("OK" if ok else "有问题")))
    print("   %s：%d 张，加载成功 %d 张，这一格高 %spx，前排尺寸 %s"
          % (info["title"], info["n"], info["loaded"], info["h"], ", ".join(info["sizes"])))
    print("   说明：%s" % info["note"].replace("\n", " "))
    return ok


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + "/#videos", wait_until="domcontentloaded")
    pg.wait_for_selector(".video-card", timeout=30000)
    results = []
    for i, (sheet, row, label, expect) in enumerate(CASES):
        results.append(probe(pg, sheet, row, label, expect, by_click=(i == 0)))
        pg.evaluate("() => document.querySelector('.modal-overlay').classList.remove('show')")
    pg.evaluate("([s, r]) => openDetail(s, Number(r))", [CASES[0][0], CASES[0][1]])
    pg.wait_for_timeout(1500)
    pg.screenshot(path="temp/material_kf_walk.png")
    print("console 报错:", errs[:3] if errs else "无")
    print("MATERIAL_KF_WALK_" + ("OK" if all(results) and not errs else "FAIL"))
    b.close()
    sys.exit(0 if all(results) and not errs else 1)
