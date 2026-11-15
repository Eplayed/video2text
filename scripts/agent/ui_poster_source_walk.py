# -*- coding: utf-8 -*-
"""海报弹窗「画面来源」这一档的界面走查（不花钱、不出图）。

抓的是 2026-10-22 号主那句"两张排版一模一样，比较呆板"背后的两个界面级前提：
① 一篇稿是几条素材合的，但弹窗以前**只报主素材那一条**的画面数——主视频原片删了就显示
   "没有可用画面"，而同一篇另一条明明有 18 张帧；于是 0 元真帧档在界面上永远是隐形的。
② 页数以前只能"拆完再手动加页"，第一次拆解拿不到页数指示，模型永远拆 2 页。
这两条都要在**真页面**上有肉眼可见的入口，不然改了后端也没人用得上。

跑法（工作台要在 15801 上）：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/agent/ui_poster_source_walk.py 94
"""
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
SID = sys.argv[1] if len(sys.argv) > 1 else "94"
SHOTS = []
fails = []


def ck(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (("　" + detail) if detail else ""))
    if not ok:
        fails.append(name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1080})
    pg.add_init_script("""() => { window.__dialogs = [];
        window.confirm = m => { window.__dialogs.push('confirm: ' + String(m).slice(0, 60)); return true; };
        window.alert = m => { window.__dialogs.push('alert: ' + String(m).slice(0, 80)); }; }""")
    pg.on("dialog", lambda d: d.accept())
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:160]))

    pg.goto(BASE + "/#summaries", wait_until="domcontentloaded")
    pg.wait_for_selector(".summary-card", timeout=30000)
    pg.locator('.summary-card[onclick="openSummary(%s)"]' % SID).first.click()
    pg.wait_for_selector('button:has-text("AI 海报")', timeout=20000)
    pg.locator('button:has-text("AI 海报")').first.click()
    pg.wait_for_function("() => document.getElementById('pk-style-key-top').options.length > 0",
                         timeout=20000)

    print("== 1 页数入口：拆之前就能定页数 ==")
    sel = pg.evaluate("""() => { const s = document.getElementById('pk-pages');
        if (!s) return null;
        return {opts: Array.from(s.options).map(o => o.value + ':' + o.text),
                value: s.value, label: (s.closest('label') || {}).textContent || ''}; }""")
    ck('顶部栏有「拆几页」下拉', bool(sel), str(sel and sel.get("label", "")[:20]))
    ck('选项覆盖 2-6 页，并有"让它定"当默认',
       sel is not None and sel["opts"][0].startswith("0:")
       and set(["2", "3", "4", "5", "6"]) <= set(o.split(":")[0] for o in sel["opts"]),
       str(sel and sel["opts"]))
    body = pg.evaluate("""() => {
        const s = document.getElementById('pk-pages'); s.value = '4';
        s.dispatchEvent(new Event('change'));
        return document.getElementById('pk-pages').value; }""")
    ck('选 4 页能选上（不会被立刻改回）', body == "4", body)
    pg.screenshot(path="temp/poster_pages_select.png", clip=None)
    SHOTS.append("temp/poster_pages_select.png")

    print("== 2 画面来源：逐条采用素材各报各的数 ==")
    # _pkFrames 是 script 里的 let，不挂 window，所以界面侧一律读 DOM，数据侧直接问接口
    src = pg.evaluate("""(sid) => fetch('/api/poster/frames?summary_id=' + sid).then(r => r.json())""", SID)
    pg.wait_for_function("() => { const b = document.getElementById('pk-frames-hint');"
                         " return b && b.style.display !== 'none' && b.innerText.length > 8; }",
                         timeout=25000)
    hint = pg.evaluate("() => document.getElementById('pk-frames-hint').innerText")
    ck('横幅按素材逐条列（不是只报主素材那一条）',
       hint.find("这篇采用") >= 0 and len(src.get("materials") or []) >= 2,
       "素材 %d 条" % len(src.get("materials") or []))
    for m in (src.get("materials") or []):
        t = ("「%s」" % (m["title"] or "")[:12])
        ck(t + " 这一条在横幅里看得见", hint.find(t[:8]) >= 0,
           "有帧池 %s／能用 %d／判过 %d／共 %d" % (m["has_pool"], m["usable"], m["judged"], m["total"]))
    nopool = [m for m in (src.get("materials") or []) if not m["has_pool"]]
    if nopool:
        ck('没帧池那条给的是「补画面」按钮（这一步不花钱）',
           "补画面" in hint and "没抽过帧" in hint)
    pg.locator("#pk-frames-hint").screenshot(path="temp/poster_frames_band.png")
    SHOTS.append("temp/poster_frames_band.png")

    print("== 3 有可用画面时，文案要说清「这一组几页、图从哪来、多少钱」 ==")
    ck('报出能用张数', "张能用" in hint, hint.splitlines()[0][:80] if hint else "")
    ck('说清出图出的是页、以及 0 元那一档', ("出图出的是" in hint or "这一组" in hint),
       hint.replace("\n", " ")[:120])
    ck('不再把"8 张画面"说成"会出 8 张图"', "出 8 张图" not in hint)

    print("== 4 画面条：图打得开、每张带素材归属 ==")
    if src.get("usable"):
        btn = pg.locator('#pk-frames-hint button:has-text("看这")')
        ck('有「看这 N 张」入口', btn.count() > 0, btn.first.inner_text() if btn.count() else "")
        if btn.count():
            btn.first.click()
            pg.wait_for_timeout(1200)
            box = pg.locator("#pk-frames-hint")
            strip = box.inner_text()
            ck('每张标了「来自「哪条素材」」（两条素材会有同名帧）',
               strip.count("来自「") >= (src.get("usable") or 0),
               "标了 %d 张" % strip.count("来自「"))
            # 碎图是这次实测抓到的：URL 以前写死 clean/，而裁过的帧落在 raw/ → 整排 404
            bad = pg.evaluate("""() => Array.from(document.querySelectorAll('#pk-frames-hint img'))
                .filter(im => im.complete && im.naturalWidth === 0)
                .map(im => im.getAttribute('src'))""")
            ck('缩略图真的打得开（没有 404 碎图）', not bad, "打不开的：%s" % (bad or [])[:3])
            missing = pg.evaluate("""() => document.querySelectorAll('#pk-frames-hint img').length""")
            ck('画面条张数与接口报的能用数一致', missing == (src.get("usable") or 0),
               "%d 张 vs 接口 %d" % (missing, src.get("usable") or 0))
            box.screenshot(path="temp/poster_frame_strip.png")
            SHOTS.append("temp/poster_frame_strip.png")

    pg.evaluate("() => document.getElementById('pk-frames-hint').scrollIntoView({block:'center'})")
    pg.screenshot(path="temp/poster_dialog_overview.png")
    SHOTS.append("temp/poster_dialog_overview.png")

    ck("页面没有 JS 报错", not errs, " / ".join(errs)[:200])
    b.close()

print("\n截图：%s" % "、".join(SHOTS))
if fails:
    print("FAIL %d 项：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("ALL_OK")
