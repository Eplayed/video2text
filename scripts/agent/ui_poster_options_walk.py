# -*- coding: utf-8 -*-
"""海报弹窗「选项选了要真生效」的界面走查（不花钱、不出图、不写库）。

抓的是号主 2026-10-23 那句「拆几页的选项选了没用，我刚才选了 6 页，结果生成的还是 4 页」，
以及同一类问题的其余几处：
① 「拆几页」以前被页签数压住——选了 6、页签还是上次的 4，就悄悄按 4 发请求；
② 攻略页那一档上「每页几张」「出法·直出」「布局」点了不会发生任何事，
   以前照样摆在栏上（抽 3 张得到 3 份同一张图的副本、直出得到一张没字的渐变底）。
判据全部在真页面上取：请求体里那个数、控件的 disabled、鼠标移上去那句为什么。

跑法（工作台要在 15801 上）：
  env -u PYTHONHOME -u PYTHONPATH python3 scripts/agent/ui_poster_options_walk.py 95
末行必须 ALL_OK。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:15801"
SID = sys.argv[1] if len(sys.argv) > 1 else "95"
SHOT_DIR = os.path.join(ROOT, "output/样稿", time.strftime("%Y-%m-%d-海报选项走查"))
fails = []


def ck(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (("　" + detail) if detail else ""))
    if not ok:
        fails.append(name)


PLAN_STUB = {  # 三页攻略档计划：够 pkRenderPlan 摆出页签，不需要后端真跑
    "pages": [{"role": "封面", "layout_key": "guide", "title": "宏库这一页就讲一件事",
               "subtitle": "照着设置一遍", "footer": "", "scene": "",
               "kicker": "第 1 期 · 任务路线",
               "photos": [{"path": "", "frame": "F1", "caption": "任务界面", "ts": "00:12"}],
               "cards": [{"t": "先勾这一项", "d": "勾了自动跟随。"}]},
              {"role": "中间页", "layout_key": "guide", "title": "插件装完先做这一步",
               "subtitle": "", "footer": "", "scene": "",
               "cards": [{"t": "宏槽别拉满", "d": "留两格够用。"}]},
              {"role": "末页", "layout_key": "guide", "title": "进本前再确认一次",
               "subtitle": "", "footer": "数据口径：2026-10-22 实测", "scene": "",
               "cards": [{"t": "报错先看这", "d": "对不上就是没重载。"}]}],
    "title": "宏库这一页就讲一件事", "style_key": "guide_ink", "footer": "数据口径：2026-10-22 实测",
    "spine": "装插件 → 勾设置 → 进本验证",
}

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    # 打桩必须挂在 context 上：`page.add_init_script` 在这套 Chrome headless 上根本不执行
    # （实测 page 级 typeof window.__dialogs === undefined，fetch 还是原生码），
    # 挂 context 才在页面脚本之前跑——confirm 与 fetch 的桩都靠它。
    ctx = b.new_context(viewport={"width": 1500, "height": 1080})
    ctx.add_init_script("""(() => { window.__dialogs = []; window.__sent = [];
        const of = window.fetch;
        window.fetch = function (u, o) {
          try {
            if (o && o.body && String(u).indexOf('/api/poster/') >= 0) {
              window.__sent.push({url: String(u), body: JSON.parse(o.body)});
            }
          } catch (e) {}
          return of.apply(this, arguments);
        };
        window.confirm = m => { window.__dialogs.push('confirm: ' + String(m).slice(0, 90)); return true; };
        window.alert = m => { window.__dialogs.push('alert: ' + String(m).slice(0, 90)); };
    })();""")
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:160]))
    # 拆解与出图两个花钱的入口一律拦掉：这一趟只验请求体和控件状态
    pg.route("**/api/poster/plan", lambda r: r.abort())
    pg.route("**/api/poster/generate", lambda r: r.abort())
    pg.route("**/api/poster/preview", lambda r: r.abort())

    pg.goto(BASE + "/#summaries", wait_until="domcontentloaded")
    pg.wait_for_selector(".summary-card", timeout=30000)
    target = pg.locator('.summary-card[onclick="openSummary(%s)"]' % SID)
    if target.count() == 0:
        target = pg.locator(".summary-card").first
    target.first.click()
    pg.wait_for_selector('button:has-text("AI 海报")', timeout=20000)
    pg.locator('button:has-text("AI 海报")').first.click()
    pg.wait_for_function("() => document.getElementById('pk-style-key-top').options.length > 0",
                         timeout=20000)

    print("== 1 先摆出一组 3 页的攻略档计划（页签数=3）==")
    pg.evaluate("""(plan) => { pkRenderPlan(plan, {}, 0); }""", PLAN_STUB)
    got = pg.evaluate("() => ({n: _pkPages.length, guide: pkGuideChrome()})")
    ck("页签里现在摊开的是 3 页，且整组识别为攻略档", got["n"] == 3 and got["guide"], str(got))

    print("== 2 选了 6 页：请求体里必须是 6，不能被页签数 3 压掉 ==")
    pg.evaluate("""() => { const s = document.getElementById('pk-pages');
        s.value = '6'; s.dispatchEvent(new Event('change')); }""")
    wanted = pg.evaluate("() => _pkPagesWanted")
    ck("改动记成了人点了 6 页", wanted == 6, str(wanted))
    pg.evaluate("() => { window.__sent = []; posterPlan(); }")
    time.sleep(1.2)
    sent = pg.evaluate("() => window.__sent.filter(x => x.url.indexOf('/plan') >= 0)")
    ck("① 拆文案真把 page_hint=6 发了出去", bool(sent) and sent[0]["body"].get("page_hint") == 6,
       str(sent and sent[0]["body"].get("page_hint")))
    dlg = pg.evaluate("() => window.__dialogs || []")
    ck("覆盖旧页签前有确认框，并且把要按几页写在框里",
       any("按 6 页" in d for d in dlg), str(dlg)[:160])

    print("== 3 没点过下拉时，页签里手动摊开的页数仍然作数 ==")
    pg.evaluate("""() => { const s = document.getElementById('pk-pages');
        s.value = '0'; s.dispatchEvent(new Event('change')); }""")
    pg.evaluate("() => { window.__sent = []; posterPlan(); }")
    time.sleep(1.2)
    sent2 = pg.evaluate("() => window.__sent.filter(x => x.url.indexOf('/plan') >= 0)")
    ck("回到「让它定」后按页签数 3 发", bool(sent2) and sent2[0]["body"].get("page_hint") == 3,
       str(sent2 and sent2[0]["body"].get("page_hint")))

    print("== 4 拆回来的页数比要的少：不许悄悄把下拉改回去，要当场说一句 ==")
    note = pg.evaluate("""function () {
        const s = document.getElementById('pk-pages');
        s.value = '6'; s.dispatchEvent(new Event('change'));
        pkRenderPlan(arguments[0], {}, 0);
        const el = document.getElementById('pk-pages-note');
        return {sel: s.value, note: _pkPagesNote,
                shown: el ? (el.innerText || '') : ''};
    }""", PLAN_STUB)
    ck("下拉仍停在人选的 6（没被结果盖掉），且界面出现那句解释",
       note["sel"] == "6" and "6 页" in note["note"], str(note["note"])[:120])
    ck("那句解释真的看得见（页签下面那一行，没画面时也看得见）",
       "只拆出" in note["shown"], str(note["shown"])[:120])

    print("== 5 攻略档上不会发生任何事的三个控件：置灰 + 写明为什么 ==")
    dead = pg.evaluate("""() => {
        pkApplyModeChrome();
        const g = id => { const e = document.getElementById(id);
            return e ? {disabled: !!e.disabled, title: (e.title || '').slice(0, 60),
                         value: e.value,
                         text: (e.options && e.options[e.selectedIndex] || {}).text || ''} : null; };
        return {copies: g('pk-copies'), mode: g('pk-text-mode'), layout: g('pk-layout-key-top'),
                pages: g('pk-pages'),
                layouts: (_pkLayouts || []).map(l => l.key),
                layoutOpts: Array.from((document.getElementById('pk-layout-key-top') || {}).options || [])
                    .map(o => o.value)}; }""")
    lay = dead["layoutOpts"]
    ck("后端下发的布局名单真接进了下拉（只剩攻略页一档，不再挂「跟着风格」）",
       dead["layouts"] == ["guide"] and lay == ["guide"], str(dead["layouts"]) + " / " + str(lay))
    ck("「每页几张」灰掉，鼠标上有那句为什么（多页：张数会按页翻倍）",
       dead["copies"]["disabled"] and ("翻倍" in dead["copies"]["title"]
                                       or "同一张图" in dead["copies"]["title"]),
       str(dead["copies"]))
    single = pg.evaluate("""function () {
        const p = JSON.parse(JSON.stringify(arguments[0])); p.pages = [p.pages[0]];
        _pkPages = []; _pkCur = 0;              // 归一那条路只在没有页签时走，先清掉才真按这份计划重排
        pkRenderPlan(p, {}, undefined);
        pkApplyModeChrome();
        const e = document.getElementById('pk-copies');
        return {n: _pkPages.length, disabled: !!e.disabled, title: (e.title || '').slice(0, 70)};
    }""", PLAN_STUB)
    ck("只剩 1 页的攻略档时，那句为什么换成「抽几张只会得到同一张图」",
       single["n"] == 1 and single["disabled"] and "同一张图" in single["title"], str(single))
    ck("「出法」灰掉并停在排版（直出在攻略页只会得一张没字的底）",
       dead["mode"]["disabled"] and dead["mode"]["value"] == "typeset"
       and "攻略页" in dead["mode"]["title"], str(dead["mode"]))
    ck("「布局」这份名单只有一档，所以灰掉而不是让人白点",
       dead["layout"]["disabled"] and "只有一种排法" in dead["layout"]["title"], str(dead["layout"]))
    ck("「拆几页」保持可点（它是真生效的那个）",
       not dead["pages"]["disabled"], str(dead["pages"]))

    pg.evaluate("() => { const s = document.getElementById('pk-pages'); s.scrollIntoView({block:'center'}); }")
    os.makedirs(SHOT_DIR, exist_ok=True)
    bar = pg.locator(".pk-bar")
    bar.screenshot(path=os.path.join(SHOT_DIR, "界面-选项栏.png"))
    ck("截图落盘（选项栏：灰掉的那三个一眼看得出来）",
       os.path.exists(os.path.join(SHOT_DIR, "界面-选项栏.png")))

    print("== 6 没有 JS 报错 ==")
    # 这一趟把 /api/poster/plan|preview|generate 三个入口拦掉了，浏览器必然报"资源加载失败"，
    # 那是探针造的，不是页面的错——只留真正的脚本异常与业务报错
    real_errs = [e for e in errs if "Failed to load resource" not in e
                 and "ERR_FAILED" not in e and "ERR_ABORTED" not in e]
    ck("走查过程中零 JS 报错（拦路由造成的加载失败除外）", not real_errs, " | ".join(real_errs[:3]))

    b.close()

print()
if fails:
    print("FAIL %d 项：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("截图目录：%s" % SHOT_DIR)
print("ALL_OK")
