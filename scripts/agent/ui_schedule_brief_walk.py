# -*- coding: utf-8 -*-
"""排产表颜色的渲染证据：起隔离服务 → 打开排产页 → 断言五档颜色真的画到卡上，桌面 + 375px 各拍一张。

为什么必须自己走这一遍（项目老规矩：界面改动没有截图＝没做完；"接上数据源 ≠ 用户看得见"）：
颜色这块的逻辑分两半——判据在服务端 `src/plan_brief.py`，上色在 `web/static/schedule-brief.js`。
两头各自"看起来对"不代表中间接得上：接口换了字段名、卡片选择器对不上、
异步重画把 class 冲掉，任何一种都会让页面**一片灰**而没有任何报错。

只连隔离服务（`poster_test_server.py`：临时库副本 + 独立端口 + 模型桩），不碰 15801。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/agent/ui_schedule_brief_walk.py
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "scripts" / "agent" / "poster_test_server.py"
fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + detail) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def start_server():
    port = free_port()
    p = subprocess.Popen([sys.executable, str(SERVER), "--port", str(port)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), bufsize=1, universal_newlines=True)
    t0 = time.time()
    while time.time() - t0 < 40:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                break
            continue
        if line.startswith("READY"):
            parts = line.strip().split()
            return p, port, dict(kv.split("=", 1) for kv in parts[1:] if "=" in kv)
    p.kill()
    raise RuntimeError("隔离服务没起来")


def main():
    out_dir = ROOT / "temp"
    out_dir.mkdir(exist_ok=True)
    proc, port, srv = start_server()
    base = "http://127.0.0.1:%d" % port
    try:
        print("── 1. 接口：判据是不是同一份、字段齐不齐 ──")
        with urllib.request.urlopen(base + "/api/plan/brief", timeout=60) as r:
            b = json.loads(r.read().decode("utf-8"))
        ck("接口返回 slots（不是 error）", bool(b.get("slots")), str(b.get("error") or "")[:80])
        need = ("verdict", "label", "fresh", "published", "due")
        one = (b.get("ranked") or [{}])[0]
        ck("每格带 verdict/label/fresh/published/due 五个字段（页面只读这些，不自己算）",
           all(k in one for k in need), ",".join(sorted(one.keys()))[:70])
        ck("档位词表跟日报同源（五档）",
           set((b.get("verdicts") or {}).keys()) == {"red", "orange", "yellow", "green", "grey"},
           str(sorted((b.get("verdicts") or {}).keys())))
        ck("素材行带 status（fresh/window），页面不再自己比日期",
           all("status" in v for s in (b.get("slots") or {}).values()
               for v in (s.get("fresh") or {}).get("list") or []))
        ck("判据读的是临时库副本，没连生产库", str(srv.get("db")) != str(ROOT / "output" / "video2text.db"),
           str(srv.get("db"))[-40:])

        print("── 2. 页面：颜色画没画上去、图例在不在、375px 会不会挤 ──")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1440, "height": 1200})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            # 上色要等接口回来（11 格 × 每格现查素材，第一次跑要几秒），固定 sleep 会把"慢"误判成"坏"
            for _ in range(40):
                pg.wait_for_timeout(1000)
                if pg.evaluate("() => !!document.getElementById('vb-legend') && "
                               "[...document.querySelectorAll('[data-item]')]"
                               ".every(c => /\\bvb-\\w+/.test(c.className))"):
                    break
            got = pg.evaluate("""() => {
              const cards=[...document.querySelectorAll('[data-item]')];
              return {n: cards.length,
                      painted: cards.filter(c=>/\\bvb-\\w+/.test(c.className)).length,
                      chips: cards.map(c=>{const e=c.querySelector('.vb-chip');return e?e.textContent:''}).filter(Boolean),
                      legend: (document.getElementById('vb-legend')||{}).textContent || '',
                      freshRows: document.querySelectorAll('.ref.vf-fresh').length,
                      windowRows: document.querySelectorAll('.ref.vf-window').length,
                      borderOf: (document.querySelector('.item.vb-red,.item.vb-orange,.item.vb-grey')
                                 || {getBoundingClientRect(){return {}}}).getBoundingClientRect().left};
            }""")
            ck("每张卡都上了色（painted = 卡片数）", got["n"] > 0 and got["painted"] == got["n"],
               "%d/%d 张有色" % (got["painted"], got["n"]))
            ck("状态字用日报那五档，且每格都有一个（空数组不算通过）",
               len(got["chips"]) == got["n"] and all(
                   c in ("今天就发", "该发但缺料", "疑似已发，请确认", "已发过，别再发", "还没到")
                   for c in got["chips"]), "、".join(sorted(set(got["chips"])))[:60])
            ck("图例说清颜色=该不该发，并写明判据同源",
               "颜色 = 该不该发" in got["legend"] and "日报" in got["legend"], got["legend"][:60])
            ck("素材行至少有一条被点上新鲜度色（绿=48h 内、灰=窗口内）",
               (got["freshRows"] + got["windowRows"]) > 0,
               "绿 %d / 灰 %d" % (got["freshRows"], got["windowRows"]))
            pg.screenshot(path=str(out_dir / "schedule_brief_1440.png"), full_page=False)
            m = pg.evaluate("() => { const e=document.querySelector('.vb-chip');"
                            "return e?getComputedStyle(e).backgroundColor:'';}")
            ck("色块不是透明的（computed style 有背景色）", bool(m) and "rgba(0, 0, 0, 0)" != m, m)
            pg.set_viewport_size({"width": 375, "height": 900})
            pg.wait_for_timeout(1200)
            wide = pg.evaluate("""() => {
               const c=[...document.querySelectorAll('[data-item]')];
               const over=c.filter(x=>x.scrollWidth>x.clientWidth+2).length;
               return {doc: document.documentElement.scrollWidth, over: over};
            }""")
            ck("375px 下文档不横向溢出、卡片内部不溢出",
               wide["doc"] <= 390 and wide["over"] == 0,
               "文档宽 %d，内部溢出 %d 张" % (wide["doc"], wide["over"]))
            pg.screenshot(path=str(out_dir / "schedule_brief_375.png"))
            ck("页面没有 JS 报错（上色脚本没把渲染打断）", not errs, "; ".join(errs[:2])[:120])
            br.close()

        print("── 3. 反向：接口挂了会不会装成「有结论」 ──")
        from playwright.sync_api import sync_playwright as sp2
        with sp2() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1440, "height": 1000})
            pg.route("**/api/plan/brief", lambda r: r.abort())      # 模拟工作台没起/接口挂了
            pg.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            for _ in range(20):
                pg.wait_for_timeout(1000)
                if pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent"
                               " && document.getElementById('vb-legend').textContent.indexOf('不猜') >= 0"):
                    break
            txt = pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent || ''")
            n = pg.evaluate("() => document.querySelectorAll('[data-item]').length")
            painted = pg.evaluate("() => [...document.querySelectorAll('[data-item]')]"
                                  ".filter(c=>/\\bvb-(red|orange|yellow|green)\\b/.test(c.className)).length")
            ck("接口失败时图例明说「算不出、这里不猜」", "不猜" in txt, txt[:70])
            ck("接口失败时不许画出「今天就发/该发」这类结论色", painted == 0,
               "%d/%d 张仍带结论色" % (painted, n))
            pg.screenshot(path=str(out_dir / "schedule_brief_offline.png"))
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("截图：temp/schedule_brief_1440.png / _375.png / _offline.png")
    if fails:
        print("\nSCHEDULE_BRIEF_WALK_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("SCHEDULE_BRIEF_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
