# -*- coding: utf-8 -*-
"""界面取证：「整组改成攻略页」一次点完，三页是不是真把真帧配回来了。

后台一次只跑一个任务，这条链是前端串着发的，所以要看的是：
  点一下 → 每页 layout_key 变 guide → 逐页重拆跑完 → 每页 photos 有没有、
  ③ 按钮上那句钱有没有改口成 0 元、最后那句总结说的是不是事实。
headless 里 confirm() 会把页面卡住，所以先把 dialog 全部打桩成"接受"。
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
SID = int(sys.argv[1]) if len(sys.argv) > 1 else 82
THEME = sys.argv[2] if len(sys.argv) > 2 else "无限服满级175小时"
OUT = "temp/guide_样张/ui_%d_整组攻略页.png" % SID


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read().decode("utf-8"))


def plan_first():
    """先让后端为这条素材拆一次文案——弹窗打开时 pkHydrate 只认"最后一次任务属于这条素材"。"""
    r = post("/api/poster/plan", {"summary_id": SID, "theme": THEME, "page_hint": 0,
                                  "text_mode": "typeset", "ratio": "9:16"})
    if r.get("error"):
        print("拆解提交失败:", r["error"])
        return False
    t0 = time.time()
    while time.time() - t0 < 600:
        st = get("/api/poster/status")
        if st.get("error"):
            print("拆解失败:", st["error"])
            return False
        if st.get("done") and not st.get("running"):
            pages = ((st.get("plan") or {}).get("plan") or {}).get("pages") or []
            print("拆解回来 %d 页，版式：%s" % (len(pages), [p.get("layout_key") for p in pages]))
            return True
        time.sleep(3)
    print("拆解超时")
    return False


def main():
    if "--no-plan" not in sys.argv:
        if not plan_first():
            return
    else:
        # 后台最后一次任务已经是这条素材（刚拆过），弹窗会自己接回文案，不再花一次拆解的钱
        st = get("/api/poster/status")
        print("复用后台上次结果：summary_id=%s done=%s" % (st.get("summary_id"), st.get("done")))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())      # 那一句确认自动点"继续"
        pg.goto(BASE, wait_until="networkidle")
        pg.evaluate("openPosterDialog(%d)" % SID)
        pg.wait_for_timeout(3000)
        if not pg.evaluate("!!document.getElementById('pk-title')"):
            print("弹窗没接回文案（pkHydrate 没认这条素材），取证止步")
            b.close()
            return
        print("点之前：版式 %s / 钱 %s" % (
            json.dumps(pg.evaluate("JSON.stringify(_pkPages.map(p=>p.layout_key))")),
            pg.evaluate("(document.getElementById('pk-go-top')||{}).textContent || "
                        "(document.getElementById('pk-go-bot')||{}).textContent || '?'")))
        pg.evaluate("pkUseGuideLayout()")
        # 等这条链跑完：链子空、没在重拆、且至少跑过一次
        t0 = time.time()
        while time.time() - t0 < 480:
            pg.wait_for_timeout(4000)
            done = pg.evaluate("(function(){return JSON.stringify({"
                               "chain: (typeof _pkGuideChain==='undefined'?-1:_pkGuideChain.length),"
                               "total: (typeof _pkGuideTotal==='undefined'?-1:_pkGuideTotal),"
                               "busy: (typeof _pkPageBusy==='undefined'?null:_pkPageBusy)});})()")
            d = json.loads(done)
            if d["chain"] == 0 and d["total"] == 0 and not d["busy"]:
                break
        print("链子状态:", done)
        print("点之后：", json.dumps(json.loads(pg.evaluate(
            "JSON.stringify(_pkPages.map(function(p){return {layout:p.layout_key,"
            "title:p.title,photos:(p.photos||[]).map(function(x){return x.caption;})};}))")),
            ensure_ascii=False, indent=1))
        print("风格下拉 =", pg.evaluate("(function(){var o=document.getElementById('pk-style-key-top');"
                                     "return o.value+' / '+o.options[o.selectedIndex].text;})()"))
        print("③ 按钮 =", pg.evaluate("(document.getElementById('pk-go-top')||{}).textContent || "
                                    "(document.getElementById('pk-go-bot')||{}).textContent || '?'"))
        print("结果区 =", pg.evaluate("(document.getElementById('pk-result')||{}).innerText || ''")[:200])
        pg.screenshot(path=OUT)
        print("截图:", OUT)
        if errs:
            print("JS 报错:", errs)
        b.close()


if __name__ == "__main__":
    main()
