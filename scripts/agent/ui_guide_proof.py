# -*- coding: utf-8 -*-
"""界面取证：攻略页那几处改动在真实弹窗里到底生效没有。

分两段跑（后台一次只能记一条素材的上次结果，`pkHydrate` 只认那一条）：
  python3 scripts/agent/ui_guide_proof.py 80        # 只查「改成攻略页」选中的是哪张脸
  python3 scripts/agent/ui_guide_proof.py 81 --full # 顺带查尾注只印末页、攻略页零件没被前端丢掉
"""
import json
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:15801"
SID = int(sys.argv[1]) if len(sys.argv) > 1 else 80
FULL = "--full" in sys.argv
OUT = "temp/guide_样张/ui_%d_攻略页%s.png" % (SID, "_full" if FULL else "")


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.evaluate("openPosterDialog(%d)" % SID)
        pg.wait_for_timeout(2500)
        pg.evaluate("pkUseGuideLayout()")     # 界面那个「直接改成攻略页」的按钮
        pg.wait_for_timeout(1800)
        got = pg.evaluate("(function(){var o=document.getElementById('pk-style-key-top');"
                          "return JSON.stringify({key:o.value,"
                          "label:o.options[o.selectedIndex]?o.options[o.selectedIndex].text:''});})()")
        print("点「改成攻略页」后，风格下拉 =", got)
        has = pg.evaluate("!!document.getElementById('pk-title')")
        print("这条素材在弹窗里有文案表单:", has)
        if FULL and has:
            print("尾注栏 =", pg.evaluate("(function(){var el=document.getElementById('pk-footer');"
                                         "if(!el) return '(没有尾注栏)';"
                                         "var row=el.closest('.pk-row');"
                                         "var tag=row?row.querySelector('.pk-tag'):null;"
                                         "return (tag?tag.textContent:'?')+' = 「'+el.value+'」';})()"))
            print("_pkPages =", json.dumps(json.loads(pg.evaluate(
                "JSON.stringify(_pkPages.map(function(p){return {layout:p.layout_key,"
                "footer:p.footer,photos:(p.photos||[]).length,kicker:p.kicker||''};}))")),
                ensure_ascii=False))
            print("发出去的 payload =", json.dumps(json.loads(pg.evaluate(
                "(function(){var b=pkReadPayload();return JSON.stringify({top:b.footer,"
                "pages:(b.pages||[]).map(function(p){return {layout:p.layout_key,"
                "footer:p.footer,photos:(p.photos||[]).length,kicker:p.kicker||''};})});})()")),
                ensure_ascii=False))
        pg.screenshot(path=OUT)
        print("截图:", OUT)
        if errs:
            print("JS 报错:", errs)
        b.close()


if __name__ == "__main__":
    main()
