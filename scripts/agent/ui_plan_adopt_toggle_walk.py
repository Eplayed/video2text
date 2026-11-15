# -*- coding: utf-8 -*-
"""ui_plan_adopt_toggle_walk.py — 排产表「已采用」那几行的复选框走查（只读，不点采用）。

号主 2026-10-30 问「已采用是什么意思，不能再次勾选了吗」。改之前：那几行没有复选框，
而点「采用勾选项到本篇」是**整组替换**，于是"想再加一条"必然把旧的两条挤掉。
改成：已采用那几行画成**默认勾着**的复选框——勾着=留在这一版，取消=移出，再勾候选=新增。

这一支只量界面上的勾选状态（`srPick` 只改内存里的 SRPICK，不发请求），
**全程不点「采用勾选项到本篇」**——那一下才会写库，会把人正在用的那一版改掉。

用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 \\
      scripts/agent/ui_plan_adopt_toggle_walk.py --out temp/plan_adopt_toggle.png
"""
import argparse
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

URL = os.environ.get("V2T_URL", "http://127.0.0.1:15801")
PAGE = URL + "/static/schedule.html"
fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:170]) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:240]) if detail else ""))
        fails.append(name)


def api(path):
    with urllib.request.urlopen(URL + path, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def slot_of(key):
    return [x for x in api("/api/plan/series")["slots"] if x["slot_key"] == key][0]


JS_BOXES = """(k) => {
  const c = document.getElementById('src-' + k);
  const ado = [...c.querySelectorAll('.sr-ado-rows input[type=checkbox]')];
  return {ado: ado.map(i => [Number(i.dataset.vid), i.checked]),
          picked: srIds(k).slice().sort((a, b) => a - b)};
}"""

# 点掉一颗已采用的框 / 勾上一条候选：都只改内存里那份待提交勾选，不发请求
JS_UNCHECK_ADOPTED = """(o) => {
  const i = [...document.getElementById('src-' + o.k).querySelectorAll('.sr-ado-rows input')]
    .find(x => Number(x.dataset.vid) === o.vid);
  i.checked = false; i.dispatchEvent(new Event('change'));
}"""
JS_CHECK_CAND = """(o) => {
  const i = [...document.getElementById('src-' + o.k).querySelectorAll('.sr-cand input')]
    .find(x => Number(x.dataset.vid) === o.vid);
  i.checked = true; i.dispatchEvent(new Event('change'));
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/plan_adopt_toggle.png")
    args = ap.parse_args()
    # 找一格「已采用 ≥2 条」的（要 2 条才看得出"取消其中一条"和"再加一条"的区别）
    slots = api("/api/plan/series")["slots"]
    target = next((s for s in slots if len(s.get("adopted_ids") or []) >= 2), None)
    if not target:
        print("库里没有「已采用 ≥2 条」的格子，这一支没法测")
        return 1
    key = target["slot_key"]
    adopted = sorted(int(i) for i in target["adopted_ids"])
    cand = [int(c["video_id"]) for c in target["candidates"]]
    print("测 %s（%s）：已采用 %s｜候选 %d 条" % (key, target["position"], adopted, len(cand)))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1400})
        page.goto(PAGE, wait_until="domcontentloaded")
        page.wait_for_selector(".sr-card", timeout=30000)
        page.evaluate("""(k) => {
            document.querySelectorAll('details.sr-ser').forEach(d => { d.open = true; });
            const c = document.getElementById('src-' + k);
            c.open = true; c.scrollIntoView();
        }""", key)
        page.wait_for_timeout(600)

        def boxes():
            return page.evaluate(JS_BOXES, key)

        print("== 1 已采用那几行现在是复选框，且默认勾着 ==")
        st = boxes()
        ck("已采用行数 == 库里 adopted_ids 条数（不是把候选混进来数的）",
           len(st["ado"]) == len(adopted), [a[0] for a in st["ado"]])
        ck("每一行都勾着（勾着=留在这一版）", all(c for _v, c in st["ado"]), st["ado"])
        ck("初始待提交清单 == 库里那几条（不点采用就等于不动）",
           st["picked"] == adopted, st["picked"])

        print("== 2 取消一条 = 从这一版移出（只改内存，不发请求） ==")
        drop = adopted[0]
        page.evaluate(JS_UNCHECK_ADOPTED, {"k": key, "vid": drop})
        page.wait_for_timeout(400)
        st2 = boxes()
        ck("取消那条后待提交清单少它一个",
           st2["picked"] == [i for i in adopted if i != drop], st2["picked"])
        ck("重画之后那一行仍是复选框、且是未勾状态（人点的没被服务端那份覆盖回去）",
           [c for v, c in st2["ado"] if v == drop] == [False], st2["ado"])

        print("== 3 再勾一条候选 = 新增（旧的都留着） ==")
        if not cand:
            ck("这一格没有候选可勾（换一格再测）", False, cand)
        else:
            add = cand[0]
            page.evaluate(JS_CHECK_CAND, {"k": key, "vid": add})
            page.wait_for_timeout(400)
            st3 = boxes()
            want = sorted([i for i in adopted if i != drop] + [add])
            ck("勾上候选后清单=留下的那些+新那条（这就是「再加一条不挤掉旧的」）",
               st3["picked"] == want, st3["picked"])

        print("== 4 重新拉一次看板：勾选回到库里那一版（内存里的改动不冒充已保存） ==")
        page.evaluate("(k) => { delete SRPICK[k]; loadSeries(); }", key)
        page.wait_for_timeout(1600)
        page.evaluate("(k) => { document.getElementById('src-' + k).open = true; }", key)
        page.wait_for_timeout(500)
        st4 = boxes()
        ck("刷新后待提交清单 == 库里 adopted_ids（没点采用就不会偷偷改掉那一版）",
           st4["picked"] == adopted, st4["picked"])
        after = slot_of(key)
        ck("服务端那一版没动过（库里 adopted_ids 还是原来那两条）",
           sorted(int(i) for i in after["adopted_ids"]) == adopted, after["adopted_ids"])
        page.locator("#src-" + key).screenshot(path=args.out)
        print("截图：%s" % args.out)
        browser.close()

    print("\nPLAN_ADOPT_TOGGLE_%s %d 条" % ("FAIL" if fails else "OK", len(fails)))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
