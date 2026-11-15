# -*- coding: utf-8 -*-
"""ui_plan_level1_walk.py — 排产表「第一级只列视频素材 + 相关非视频按钮按需出现」的真页面走查。

为什么必须有这一支：判据改在后端、字段也下发了，**页面上看不见等于没做**
（10-15 那次教训：接口全绿、用户打开工作台还是老样子）。号主 2026-10-30 提的三条
都是"他眼睛里看到的东西"，所以这里只量 DOM，不查库。

只读：全程只做 GET 与展开/点击「相关非视频」（也是 GET），不点采用、不点生成、不登记发布。
用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 \\
      scripts/agent/ui_plan_level1_walk.py --out temp/plan_level1.png
"""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("V2T_URL", "http://127.0.0.1:15801")
PAGE = URL + "/static/schedule.html"
VIDEO_SHEETS = ("抖音视频数据", "B站视频")
fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:160]) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:240]) if detail else ""))
        fails.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/plan_level1.png")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1400})
        page.goto(PAGE, wait_until="domcontentloaded")
        page.wait_for_selector(".sr-card", timeout=30000)
        # 把两个系列都展开（默认折着一行，看不全）
        page.evaluate("""() => {
            document.querySelectorAll('details.sr-ser').forEach(d => d.open = true);
            document.querySelectorAll('.sr-card').forEach(d => d.open = true);
        }""")
        page.wait_for_timeout(600)

        data = page.evaluate("""() => {
          var out = {cand: [], ado: [], notes: [], buttons: 0};
          document.querySelectorAll('.sr-card').forEach(function (card) {
            var slot = card.id.replace('src-', '');
            card.querySelectorAll('.sr-cand > .sr-ci').forEach(function (row) {
              var a = row.querySelector('a');
              out.cand.push({slot: slot,
                             title: a ? a.textContent : '',
                             novid: !!row.querySelector('.sr-novid'),
                             pill: !!row.querySelector('.sr-more')});
            });
            card.querySelectorAll('.sr-ado-rows > .sr-ci').forEach(function (row) {
              var a = row.querySelector('a');
              var tag = row.querySelector('.sr-novid');
              out.ado.push({slot: slot, title: a ? a.textContent : '',
                            novid: !!tag,
                            bg: tag ? getComputedStyle(tag).backgroundColor : '',
                            pill: !!row.querySelector('.sr-more')});
            });
            var n = card.querySelector('.sr-note');
            if (n && n.textContent.indexOf('第一级候选') >= 0) out.notes.push({slot: slot, t: n.textContent});
          });
          out.buttons = document.querySelectorAll('.sr-cand .sr-more').length;
          // 期望值只数**页面上真画出来**的那些格：legacy 那 11 格 10-29 起不再出卡片，
          // 拿全量 SRMAP 当期望会永远少一截（第一版就撞在这里）
          var drawn = {};
          document.querySelectorAll('.sr-card').forEach(function (c) {
            drawn[c.id.replace('src-', '')] = 1;
          });
          out.expect = 0;
          Object.keys(SRMAP || {}).forEach(function (k) {
            if (!drawn[k]) return;
            (SRMAP[k].candidates || []).forEach(function (c) {
              if ((c.sup_n || 0) > 0) out.expect += 1;
            });
          });
          out.total = out.cand.length;
          out.drawn = Object.keys(drawn).length;
          return out;
        }""")

        print("== 1 第一级只列视频素材 ==")
        bad = [c for c in data["cand"] if c["novid"]]
        ck("候选行里一张「论坛帖 · 无关键帧」红药丸都没有（=第一级全是视频）",
           not bad, bad[:3])
        noise = [c["title"] for c in data["cand"]
                 if any(w in c["title"] for w in ("面试", "AI技术", "WorkBuddy", "小米", "Agent"))]
        ck("候选标题里没有面试/AI 技术/数码那一路（号主点名的那批）", not noise, noise[:4])
        ck("页面上真的画出了候选（不是把整块弄空来「看起来干净」）",
           data["total"] > 100, "候选行 %d 条 / 页面画出 %d 格" % (data["total"], data["drawn"]))

        print("== 2 「相关非视频」按钮只画在真有子集的那一行 ==")
        ck("按钮颗数 == 后端数出来 sup_n>0 的候选行数（不靠前端猜）",
           data["buttons"] == data["expect"], "DOM %d 颗 / 应有 %d 颗" % (
               data["buttons"], data["expect"]))
        withpill = [c for c in data["cand"] if c["pill"]]
        ck("有按钮的行只占少数（大多数视频素材名下其实没有强相关非视频料）",
           0 < len(withpill) < len(data["cand"]), "有按钮 %d / 候选 %d" % (
               len(withpill), len(data["cand"])))

        print("== 3 候选被挡掉那一格要说清为什么 ==")
        ck("至少有一格显示「没有可列的第一级候选」并带原因（不是空着）",
           len(data["notes"]) >= 1, [(n["slot"], n["t"][:46]) for n in data["notes"][:2]])
        if data["notes"]:
            ck("那句话点得出是被哪三类挡的（非视频/别的赛道/判不出归属）",
               any(("非视频素材" in n["t"]) for n in data["notes"]),
               data["notes"][0]["t"][:70])

        print("== 4 已采用那一区：非视频那条仍然标得出来 ==")
        nonvid_ado = [a for a in data["ado"] if a["novid"]]
        ck("已采用里那条公众号文章带红药丸（库里真有这一条：free-a0b82c）",
           len(nonvid_ado) >= 1, [(a["slot"], a["title"][:16]) for a in nonvid_ado])
        if nonvid_ado:
            ck("药丸的样式真的生效（不是挂在没配 CSS 的 class 上）",
               nonvid_ado[0]["bg"].startswith("rgba("), nonvid_ado[0]["bg"])
        ck("非视频那一行不挂「相关非视频」按钮（它自己就是子集）",
           not any(a["pill"] and a["novid"] for a in data["ado"]))

        print("== 5 点一颗按钮，子集真的出得来 ==")
        btn = page.locator(".sr-cand .sr-more").first
        btn.scroll_into_view_if_needed()
        btn.click()
        page.wait_for_timeout(2500)
        # 必须指到**这一行**的子集框：每一行都先渲染一个空的 .sr-sub，拿 document 里第一个
        # 那个去量，量到的是别家没点开的空框（第一版就这样，红了还以为是懒查坏了）
        got = page.evaluate("""() => {
          var row = document.querySelector('.sr-cand .sr-more').closest('.sr-ci');
          var box = row ? row.querySelector('.sr-sub') : null;
          return box ? {rows: box.querySelectorAll('label').length,
                        text: box.textContent.slice(0, 90)} : null;
        }""")
        ck("展开后子集里真列出条目（不是「正在查…」卡住，也不是「没找到」）",
           bool(got) and got["rows"] >= 1, got)

        print("== 6 状态栏那一行报得出「挡掉多少、为什么挡」 ==")
        bar = page.evaluate("() => (document.getElementById('sr-run')||{}).textContent || ''")
        ck("状态栏把服务端的 note 原样念出来（含「挡掉」与三类原因，不是前端估的）",
           "挡掉" in bar and "非视频素材" in bar, bar.strip()[:120])

        page.screenshot(path=args.out, full_page=False)
        print("截图：%s" % args.out)
        browser.close()

    print("\nPLAN_LEVEL1_%s %d 条" % ("FAIL" if fails else "OK", len(fails)))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
