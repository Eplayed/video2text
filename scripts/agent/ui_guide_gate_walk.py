# -*- coding: utf-8 -*-
"""走一遍「事实闸在界面上到底拦不拦得住、人有没有出口」：headless Chrome 开真弹窗点真按钮。

为什么必须走这一遍（2026-10-21 整改任务书第四项）：闸口写成纯函数、单测全绿，
不代表界面上点「出图」真的被拦住，也不代表被拦之后人看得见放行按钮——
本项目栽过两次："接上数据源 ≠ 用户看得见"、"门禁红了但没人知道为什么红"。

三条纪律：
· **不花钱**：把 87 已存的成品文案塞回表单（`pkRenderPlan`，就是拆解回来时用的那个装载函数），
  点「出图」走的是"人改过文案"那条路，后端不再调 LLM 拆解；而事实闸在**任何出图之前**就抛错，
  所以这一趟连图片模型都没碰。
· **不动生产数据**：被拦住的请求不落盘（`generate_poster` 在写 manifest 之前就 raise），
  脚本结尾还会核对 87 的 manifest mtime 没变——真变了就是这次跑漏了闸。
· 断言只看用户真会读到的文字，不看内部变量。

用法：python3 scripts/agent/ui_guide_gate_walk.py [--out temp/guide_gate_walk.png]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SID = 87
NEEDLE = "46亿"          # 整理稿【风险核查】点名"疑似转写或口误"的那个数


def api(path, body=None):
    req = urllib.request.Request(BASE + path,
                                 data=(json.dumps(body).encode("utf-8") if body is not None else None),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/guide_gate_walk.png")
    args = ap.parse_args()
    fails = []

    mf = os.path.join(ROOT, "output", "poster", str(SID), "manifest.json")
    if not os.path.isfile(mf):
        print("夹具不在：%s" % mf)
        return 2
    before = os.path.getmtime(mf)
    m = json.load(open(mf, encoding="utf-8"))
    plan = m.get("plan") or {}
    pub = m.get("publish") or {}
    if NEEDLE not in json.dumps(plan, ensure_ascii=False):
        fails.append("夹具本身不含「%s」，这一轮的断言没有意义（成品文案变了？）" % NEEDLE)

    # ① 后端：带着 87 已存的文案提交出图 → 必须在花钱和落盘之前被拦
    _ = api("/api/poster/status")
    api("/api/poster/generate", {"summary_id": SID, "plan": plan, "publish": pub,
                                 "text_mode": m.get("text_mode") or "typeset",
                                 "review": False, "copies": 1})
    err = ""
    for _ in range(40):
        st = api("/api/poster/status")
        if st.get("running"):
            time.sleep(1)
            continue
        err = str(st.get("error") or "")
        break
    if "待核事实进了成品" not in err:
        fails.append("后端没按事实闸拦下来，error=「%s」" % err[:120])
    else:
        print("  [PASS] 后端拦下：%s" % err[:110])
    if NEEDLE not in err:
        fails.append("拦下的原因里没点名「%s」，人不知道改哪" % NEEDLE)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        # ② 界面：开 87 的弹窗、把已存文案装回表单（拆解回来时用的就是 pkRenderPlan）
        pg.evaluate("(id) => openPosterDialog(id)", SID)
        pg.wait_for_timeout(1500)
        pg.evaluate("(plan) => { pkRenderPlan(plan, {}, 0); }", plan)
        pg.wait_for_timeout(600)
        got = pg.evaluate("""() => {
            const t = document.getElementById('pk-title');
            return {title: t ? t.value : null,
                    has46: (document.getElementById('pk-result')||{}).innerText
                           ? document.getElementById('pk-result').innerText.indexOf('%s') >= 0 : false};
        }""" % NEEDLE)
        if not got.get("title"):
            fails.append("表单没装进文案（pkRenderPlan 没生效），后面这趟不算走过界面")
        # ③ 点真按钮：confirm 在 headless 里默认 false，先打桩
        pg.evaluate("window.confirm = () => true")
        pg.evaluate("() => posterGenerate(false)")
        shot_txt = ""
        for _ in range(40):
            time.sleep(1)
            shot_txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
            if "待核事实" in shot_txt:
                break
        if "待核事实进了成品" not in shot_txt:
            fails.append("界面上没看到拦下的提示，用户会以为还在跑")
        else:
            print("  [PASS] 界面提示：%s" % shot_txt.split("\n")[0][:100])
        if NEEDLE not in shot_txt:
            fails.append("界面提示没点名「%s」" % NEEDLE)
        btn = pg.evaluate("""() => Array.from(document.querySelectorAll('#pk-result button'))
            .map(b => b.textContent.trim())""")
        if not any("仍然出图" in b for b in btn):
            fails.append("拦完没给放行出口（按钮只有：%s）——人只能绕接口去改数据" % ("、".join(btn) or "无"))
        else:
            print("  [PASS] 放行出口：%s" % [b for b in btn if "仍然出图" in b][0])
        # 截证据要截**那一格**：整屏截图里错误提示在弹窗下方看不见，等于没证据
        pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
        pg.wait_for_timeout(400)
        pg.query_selector("#pk-result").screenshot(path=args.out)
        pg.screenshot(path=args.out.replace(".png", "_full.png"))
        browser.close()

    after = os.path.getmtime(mf)
    if after != before:
        fails.append("87 的 manifest 被写了（mtime 变了）——闸口应当在落盘之前拦住")
    else:
        print("  [PASS] 生产成品没被动：manifest mtime 未变")
    if errs:
        fails.append("页面 JS 报错：" + " ; ".join(errs[:2]))
    print("截图：" + args.out)
    if fails:
        print("发现 %d 处问题：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("GUIDE_GATE_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
