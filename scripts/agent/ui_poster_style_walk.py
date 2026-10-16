# -*- coding: utf-8 -*-
"""AI 海报弹窗「风格/版面」名单走查（真入口：整理稿卡片 → 🎨 AI 海报）。

号主 2026-10-16 的要求：风格下拉只留攻略页那三套皮肤、老的模板与"自由发挥"都不要，
默认进来就是攻略页，看版面也看攻略页。这个脚本就验这五件事，全部 0 元（不调 AI）：

1) 风格下拉里到底有哪几项（必须三项，且都以「攻略页」开头）；
2) 默认选中哪一项（必须等于后端按 summary_id 算出来的那张脸，不是前端写死的）；
3) 布局下拉只剩「攻略页（真帧+栏目名+页码）」且已选中；
4) ③ 出图按钮上那句钱必须跟着改口成 0 元（名单是异步到的，不补一次就是虚报）；
5) 点「② 看版面 · 免费」真排一张回来，回来的 check.layout 必须是 guide。
"""
import json
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
SID = sys.argv[1] if len(sys.argv) > 1 else "86"
PLAN = "output/poster/%s/manifest.json" % SID

with open(PLAN, encoding="utf-8") as f:
    plan = (json.load(f).get("plan") or {})
plan["style_key"] = ""          # 故意清空：让界面按"默认那一档"来选，验的就是默认值本身

fails = []


def ck(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (("　" + detail) if detail else ""))
    if not ok:
        fails.append(name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1050})
    # headless 里 confirm/alert 会挂住整个流程：打桩掉，但把内容记下来（要看得见它问了什么）
    pg.evaluate("""() => { window.__dialogs = [];
        window.confirm = m => { window.__dialogs.push('confirm: ' + m); return true; };
        window.alert = m => { window.__dialogs.push('alert: ' + m); }; }""")
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + "/#summaries", wait_until="domcontentloaded")
    pg.wait_for_selector(".summary-card", timeout=30000)      # 列表是异步拉的，不等它就是 0 个
    card = pg.locator('.summary-card[onclick="openSummary(%s)"]' % SID)
    if card.count() == 0:
        print("列表里找不到整理稿 %s（分页？）" % SID)
        sys.exit(2)
    card.first.click()                      # 真入口：点卡片开整理稿
    pg.wait_for_selector('button:has-text("AI 海报")', timeout=20000)
    pg.locator('button:has-text("AI 海报")').first.click()   # 真入口：弹窗里点「🎨 AI 海报」
    pg.wait_for_function("() => document.getElementById('pk-style-key-top') && "
                         "document.getElementById('pk-style-key-top').options.length > 0",
                         timeout=20000)
    pg.wait_for_timeout(600)

    st = pg.evaluate("""() => {
        const s = document.getElementById('pk-style-key-top');
        const l = document.getElementById('pk-layout-key-top');
        const go = document.getElementById('pk-go-top');
        const mb = document.getElementById('pk-manual-btn');
        return {styles: [...s.options].map(o => [o.value, o.textContent.trim()]),
                style: s.value, layouts: [...l.options].map(o => [o.value, o.textContent.trim()]),
                layout: l.value, go: go ? go.textContent.trim() : '',
                manualHidden: !mb || getComputedStyle(mb).display === 'none',
                defaultFromServer: _pkDefaultStyle};
    }""")
    print("整理稿 %s · 打开海报弹窗（真点击：卡片 → 🎨 AI 海报）" % SID)
    labels = [t for _, t in st["styles"]]
    ck("风格下拉只剩攻略页三套", len(labels) == 3 and all(t.startswith("攻略页") for t in labels),
       "｜".join(t[:12] for t in labels))
    ck("没有老模板/自由发挥/★用户模板混进来",
       all(v.startswith("guide_") for v, _ in st["styles"]),
       ",".join(v for v, _ in st["styles"]))
    ck("默认选中的就是后端按素材轮换算出来的那张脸",
       bool(st["style"]) and st["style"] == st["defaultFromServer"],
       "界面=%s 后端=%s" % (st["style"], st["defaultFromServer"]))
    ck("布局下拉只有「攻略页（真帧+栏目名+页码）」且已选中",
       len(st["layouts"]) == 1 and st["layout"] == "guide",
       "%s｜选中=%s" % (st["layouts"][0][1] if st["layouts"] else "空", st["layout"]))
    ck("③ 按钮上那句钱跟着改口成 0 元",
       "0 元" in st["go"] and "0.5" not in st["go"], st["go"])
    ck("「去豆包画无字底图」那一块收起（攻略页没有底图可画）", st["manualHidden"])

    chips = pg.evaluate("""() => [...document.querySelectorAll('#pk-tpls .pk-tpl b')]
        .map(e => e.textContent.trim())""")
    ck("「我的模板」那排不再摆老模板（暗金史诗食谱 / 388礼包 都不该在）",
       not any(("暗金史诗" in c) or ("388礼包" in c) for c in chips), "｜".join(chips) or "（只剩拆参考图入口）")

    # 把已有文案灌进弹窗（0 元），再点「② 看版面」——这一步验的是"看版面看的是攻略页"
    pg.evaluate("(plan) => { pkRenderPlan(plan, {}, 0); }", plan)
    pg.wait_for_timeout(500)
    pg.click("#pk-preview-top")
    pg.wait_for_function("() => document.querySelectorAll('#pk-result img').length > 0",
                         timeout=60000)
    got = pg.evaluate("""() => {
        const im = document.querySelector('#pk-result img');
        return {n: document.querySelectorAll('#pk-result img').length,
                w: im ? im.naturalWidth : 0, h: im ? im.naturalHeight : 0,
                src: im ? im.getAttribute('src') : '',
                layouts: (_pkPreviewPages || []).map(x => (x.check || {}).layout),
                labels: (_pkPreviewPages || []).map(x => (x.check || {}).layout_label),
                note: (document.getElementById('pk-result').innerText || '').slice(0, 70)};
    }""")
    print("② 看版面 · 免费：回来 %d 页，版面=%s（%s），尺寸 %dx%d"
          % (got["n"], ",".join(got["layouts"] or ["?"]), ",".join(got["labels"] or ["?"]),
             got["w"], got["h"]))
    ck("看版面排出来的每一页都是攻略页",
       bool(got["layouts"]) and all(x == "guide" for x in got["layouts"]),
       str(got["layouts"]))
    ck("预览图真的加载出来了（不是 0×0）", got["w"] > 300 and got["h"] > 300,
       "%dx%d" % (got["w"], got["h"]))
    pg.screenshot(path="temp/poster_style_walk.png", full_page=False)
    net = [e for e in errs if "favicon" not in e]
    ck("console 无报错", not net, "; ".join(net[:2]))
    b.close()

print("POSTER_STYLE_WALK_" + ("OK" if not fails else "FAIL：" + "、".join(fails)))
sys.exit(0 if not fails else 1)
