# -*- coding: utf-8 -*-
"""走一遍海报弹窗：把"他看见的每一个字"打出来，并截图。找哪里费解用的。

用法：
  python3 scripts/agent/ui_walk.py 83 "主题" --plan      # 先让后端拆一次（花模型那几厘），再走界面
  python3 scripts/agent/ui_walk.py 83 --chain            # 「整组改成攻略页」连逐页重拆一起跑完
不传 --plan 就复用后台上一次的结果（重启过工作台就没有了）。
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
SID = int(sys.argv[1]) if len(sys.argv) > 1 else 83
THEME = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "走查"
PLAN = "--plan" in sys.argv
CHAIN = "--chain" in sys.argv


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read().decode("utf-8"))


def wait_plan(task, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get("/api/poster/status")
        if int(st.get("task_id") or 0) != int(task):
            return None
        if st.get("error"):
            print("拆解失败:", st["error"])
            return None
        if st.get("done") and not st.get("running"):
            return st
        time.sleep(3)
    return None


def grab(pg):
    ev = pg.evaluate
    return {
        "顶部③": ev("(document.getElementById('pk-go-top')||{}).textContent"),
        "模式行": ev("(document.getElementById('pk-mode')||{}).textContent"),
        "风格": ev("(function(){var o=document.getElementById('pk-style-key-top');"
                  "return o.value+' | '+(o.options[o.selectedIndex]||{}).text;})()"),
        "版式": ev("(function(){var o=document.getElementById('pk-layout-key-top');"
                  "var l=document.getElementById('pk-layout-label');"
                  "return (l?l.textContent:'?')+' = '+(o.value||'（空）')+' | '"
                  "+((o.options[o.selectedIndex]||{}).text||'');})()"),
        "每页版式": json.loads(ev("JSON.stringify(_pkPages.map(function(p){return p.layout_key;}))")),
        "画面那行": (ev("(document.getElementById('pk-frames-hint')||{}).innerText") or "").replace("\n", " ⏎ "),
        "页签": (ev("(document.getElementById('pk-tabs')||{}).innerText") or "").replace("\n", " / "),
    }


def dump(tag, pg):
    print("── %s ──" % tag)
    for k, v in grab(pg).items():
        print("  %-8s %s" % (k, v))


def main():
    if PLAN:
        r = post("/api/poster/plan", {"summary_id": SID, "theme": THEME, "page_hint": 0,
                                      "text_mode": "typeset", "ratio": "9:16"})
        if r.get("error"):
            print("拆解提交失败:", r["error"])
            return
        st = wait_plan(r["task_id"])
        if not st:
            return
        pl = (st.get("plan") or {}).get("plan") or {}
        print("拆回来：style=%s 页数=%d 版式=%s guide_only=%s"
              % (pl.get("style_key"), len(pl.get("pages") or []),
                 [p.get("layout_key") for p in (pl.get("pages") or [])],
                 (st.get("plan") or {}).get("guide_only")))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        MSGS = []
        def on_dialog(d):
            MSGS.append(d.message)
            (d.accept() if CHAIN else d.dismiss())
        pg.on("dialog", on_dialog)
        pg.goto(BASE, wait_until="networkidle")
        pg.evaluate("openPosterDialog(%d)" % SID)
        pg.wait_for_timeout(4000)
        if not pg.evaluate("!!document.getElementById('pk-title')"):
            print("弹窗里没有文案（后台上次结果不属于这条素材，或工作台刚重启过）——加 --plan 先拆一次")
            b.close()
            return
        dump("1 打开弹窗（接回上次结果）", pg)
        pg.screenshot(path="temp/guide_样张/walk_%d_1.png" % SID,
                      clip={"x": 250, "y": 0, "width": 950, "height": 640})
        if pg.evaluate("(document.getElementById('pk-frames-hint')||{}).innerText.indexOf('看这')>=0"):
            pg.evaluate("pkToggleFrameStrip()")
            pg.wait_for_timeout(2500)
            print("  缩略图数:", pg.evaluate("document.querySelectorAll('#pk-frames-hint img').length"))
            dump("2 展开「看这 N 张画面」", pg)
            pg.screenshot(path="temp/guide_样张/walk_%d_2_画面条.png" % SID,
                          clip={"x": 250, "y": 0, "width": 950, "height": 940})
            pg.evaluate("pkToggleFrameStrip()")
        pg.evaluate("pkUseGuideLayout()")
        pg.wait_for_timeout(3000)
        dump("3 点「整组改成攻略页」%s" % ("（含逐页重拆）" if CHAIN else "（confirm 取消＝只换版式）"), pg)
        if CHAIN:
            t0 = time.time()
            while time.time() - t0 < 480:
                pg.wait_for_timeout(4000)
                d = json.loads(pg.evaluate("JSON.stringify({chain:_pkGuideChain.length,"
                                           "total:_pkGuideTotal,busy:_pkPageBusy})"))
                if d["chain"] == 0 and d["total"] == 0 and not d["busy"]:
                    break
            dump("4 逐页重拆跑完", pg)
        pg.evaluate("posterPreview()")
        pg.wait_for_timeout(7000)
        print("── 预览结果区 ──")
        print(" ", (pg.evaluate("(document.getElementById('pk-result')||{}).innerText") or "")
              .replace("\n", " ⏎ ")[:520])
        pg.screenshot(path="temp/guide_样张/walk_%d_5_预览.png" % SID,
                      clip={"x": 250, "y": 0, "width": 950, "height": 940})
        # 花钱那条路的确认框：这一版是 AI 画底图时，有没有说清"用不到那 N 张素材画面"
        msgs = MSGS[len(MSGS):]
        pg.evaluate("_pkPages.forEach(function(p){p.layout_key='top_art'; p.photos=[];});"
                    "pkSyncSelectors(); posterGenerate();")
        pg.wait_for_timeout(1500)
        print("── 4 切成 AI 档后点 ③，确认框说的是 ──")
        print("  ③ 按钮:", pg.evaluate("(document.getElementById('pk-go-top')||{}).textContent"))
        for m in MSGS[1:]:
            print("  确认框:", m.replace("\n", " ⏎ "))
        pg.evaluate("_pkPages.forEach(function(p){p.layout_key='guide';}); pkSyncSelectors();"
                    "posterGenerate();")
        pg.wait_for_timeout(1500)
        print("── 5 攻略档点 ③ 的确认框 ──")
        print("  ③ 按钮:", pg.evaluate("(document.getElementById('pk-go-top')||{}).textContent"))
        for m in MSGS[2:]:
            print("  确认框:", m.replace("\n", " ⏎ "))
        if errs:
            print("JS 报错:", errs)
        b.close()


if __name__ == "__main__":
    main()
