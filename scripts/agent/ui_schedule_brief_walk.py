# -*- coding: utf-8 -*-
"""排产表颜色的渲染证据：起隔离服务 → 打开排产页 → 断言**真实卡片的 computed 色条**，桌面 + 375px 各拍一张。

为什么必须自己走这一遍（项目老规矩：界面改动没有截图＝没做完；"接上数据源 ≠ 用户看得见"）：
颜色分两半——判据在服务端 `src/plan_brief.py`，上色在 `web/static/schedule-brief.js`。
两头各自"看起来对"不代表中间接得上。2026-10-21 复核 R3 抓到的正是这条：
上色脚本写的是 `.item.vb-*`，而页面生成的卡片是 `.card`，**色条从来没画出来过**，
可老走查断的是"卡上有 vb- 类"和"标签有背景色"——class 是加上了、chip 的样式也确实生效，
所以一路全绿。现在断言改成读 `getComputedStyle` 的 `border-left-color/width`。

两段跑法：
· A 段用真接口真数据（今天到底是哪几档就验哪几档，逐张核对"类名 ↔ 颜色"对得上）；
· B 段把 `/api/plan/brief` 换成一份合成响应（六个档位各占一张真卡），
  这样红/顺延/橙/黄/绿/灰六种颜色都能在**同一份真实 DOM** 上量到，不靠"今天恰好有红槽"；
· C 段把接口掐掉，验"算不出就不许显示判断色"。

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

# 期望色条：这份是"显示层该有的样子"，故意写在这里而不是从 JS 里读——
# 两边同源就测不出"JS 被人改回 .item 了"这类回归。
EXPECT = {"vb-red": ("#d0433a", "5px"), "vb-deferred": ("#5b78c8", "5px"),
          "vb-orange": ("#e08a2e", "5px"), "vb-yellow": ("#c9b23a", "5px"),
          "vb-green": ("#2f9e63", "5px"), "vb-grey": ("#b9bfc7", "5px")}


def ck(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" — " + detail) if detail else ""))
    if not cond:
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


def wait_painted(pg, want=1, tries=40):
    """等上色脚本跑完（接口回来才画，固定 sleep 会把"慢"误判成"坏"）。"""
    for _ in range(tries):
        pg.wait_for_timeout(1000)
        n = pg.evaluate("() => !!document.getElementById('vb-legend') && "
                        "[...document.querySelectorAll('[data-item]')]"
                        ".filter(c => /\\bvb-\\w+/.test(c.className)).length")
        if n >= want:
            return n
    return -1


# 在页面里量：真实卡片的 computed 左边框 + 标签文字 + 图例那份话
MEASURE = """() => {
  const probe = {};
  for (const k of %s) {
    const d = document.createElement('div');
    d.className = 'card ' + k; d.style.display = 'none';
    document.body.appendChild(d);
    const cs = getComputedStyle(d);
    probe[k] = [cs.borderLeftColor, cs.borderLeftWidth];
    d.remove();
  }
  const cards = [...document.querySelectorAll('[data-item]')];
  return {
    probe: probe,
    n: cards.length,
    painted: cards.filter(c => /\\bvb-\\w+/.test(c.className)).length,
    cards: cards.map(c => {
      const cs = getComputedStyle(c);
      const cls = (c.className.match(/\\bvb-\\w+/g) || []).join(' ');
      const chip = c.querySelector('.vb-chip');
      return {id: c.getAttribute('data-item'), cls: cls,
              color: cs.borderLeftColor, width: cs.borderLeftWidth,
              chip: chip ? chip.textContent : '', title: chip ? (chip.title || '') : '',
              demote: (c.querySelector('.vb-demote') || {}).textContent || ''};
    }),
    freshRows: document.querySelectorAll('.ref.vf-fresh').length,
    windowRows: document.querySelectorAll('.ref.vf-window').length,
    legend: (document.getElementById('vb-legend') || {}).textContent || ''
  };
}""" % json.dumps(list(EXPECT))


def to_rgb(hexs):
    h = hexs.lstrip("#")
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def check_color(got, c, tag):
    """一张卡：类名 ↔ 颜色必须对得上；顺带验宽度真的是 5px（不是 1px 默认边框）。"""
    key = (c["cls"].split() or [""])[0]
    if not key:
        return None
    exp = EXPECT.get(key)
    if not exp:
        return "未知档位类名 %s" % key
    return ("%s %s：色条 %s/%s（期望 %s/%s）" % (
        tag, c["id"], c["color"], c["width"], to_rgb(exp[0]), exp[1]),
        c["color"] == to_rgb(exp[0]) and c["width"] == exp[1])


def main():
    out_dir = ROOT / "temp"
    out_dir.mkdir(exist_ok=True)
    proc, port, srv = start_server()
    base = "http://127.0.0.1:%d" % port
    try:
        print("── 1. 接口：判据是不是同一份、字段齐不齐、六档全不全 ──")
        with urllib.request.urlopen(base + "/api/plan/brief", timeout=60) as r:
            b = json.loads(r.read().decode("utf-8"))
        ck("接口返回 slots（不是 error）", bool(b.get("slots")), str(b.get("error") or "")[:80])
        need = ("verdict", "label", "why", "fresh", "published", "due")
        one = (b.get("ranked") or [{}])[0]
        ck("每格带 verdict/label/why/fresh/published/due（页面只读这些，不自己算）",
           all(k in one for k in need), ",".join(sorted(one.keys()))[:70])
        ck("档位词表六档齐（红/顺延/橙/黄/绿/灰），且由服务端下发",
           set((b.get("verdicts") or {}).keys()) == {"red", "deferred", "orange",
                                                "yellow", "green", "grey"},
           str(sorted((b.get("verdicts") or {}).keys())))
        ck("每档都带一句解释（why）+ 窗口口径 fresh_label，前端不抄第二份文案",
           set((b.get("verdict_why") or {}).keys()) == set(b.get("verdicts") or {})
           and bool(b.get("fresh_label")), str(b.get("fresh_label")))
        bad = [k for k, v in (b.get("verdicts") or {}).items()
               if "今天就发" in v or "一定要发" in v or "可发布" in v]
        ck("R1：接口下发的档位名字里没有「今天就发/一定要发/可发布」", not bad, str(bad))
        ck("判据读的是临时库副本，没连生产库", str(srv.get("db")) != str(ROOT / "output" / "video2text.db"),
           str(srv.get("db"))[-40:])

        from playwright.sync_api import sync_playwright
        labels = b.get("verdicts") or {}
        ids = list((b.get("slots") or {}).keys())

        print("── 2. A 段：真数据下，每张真卡的类名与 computed 色条对得上 ──")
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1440, "height": 1200})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            wait_painted(pg, want=len(ids))
            got = pg.evaluate(MEASURE)
            ck("每张卡都上了色（painted = 卡片数）", got["n"] > 0 and got["painted"] == got["n"],
               "%d/%d 张有色" % (got["painted"], got["n"]))
            ck("R3：探针元素（.card.vb-*）六种颜色的色条都量得到 5px 实线",
               all(v[1] == EXPECT[k][1] and v[0] == to_rgb(EXPECT[k][0])
                   for k, v in got["probe"].items()),
               str(got["probe"].get("vb-red")))
            mism = []
            for c in got["cards"]:
                r2 = check_color(got, c, "A")
                if isinstance(r2, tuple) and not r2[1]:
                    mism.append(r2[0])
                elif isinstance(r2, str):
                    mism.append(r2)
            ck("真卡逐张核对：类名 ↔ computed border-left 一致（不是 class 加上了、颜色没生效）",
               not mism, "; ".join(mism[:2])[:120])
            ck("卡片标签文字与接口 label 一字不差（前端不抄第二份文案）",
               all(c["chip"] == labels.get(c["cls"].replace("vb-", ""), c["chip"])
                   for c in got["cards"] if c["cls"]),
               "、".join(sorted({c["chip"] for c in got["cards"] if c["chip"]}))[:60])
            ck("标签带 title 解释「这一档为什么是这个颜色」（来自接口 why，不是 JS 里再写一份）",
               all(len(c["title"]) > 10 for c in got["cards"] if c["chip"]),
               next((c["title"][:40] for c in got["cards"] if c["title"]), ""))
            ck("图例说明颜色=排期优先级，并写明「不等于这篇能发」+ 四道审核",
               "今天先动哪一题" in got["legend"] and "不等于" in got["legend"]
               and "使用依据" in got["legend"], got["legend"][:70])
            ck("图例里六档名字全部来自接口（少一个就是页面抄了一份）",
               all(v in got["legend"] for v in labels.values()),
               str([v for v in labels.values() if v not in got["legend"]]))
            ck("素材行新鲜度色至少点中一条（绿=窗口内、灰=窗口外）",
               (got["freshRows"] + got["windowRows"]) > 0,
               "绿 %d / 灰 %d" % (got["freshRows"], got["windowRows"]))
            pg.screenshot(path=str(out_dir / "schedule_brief_1440.png"), full_page=False)

            print("── 3. B 段：合成一份「六档各一张」的接口响应，量真卡色条（含 375px）──")
            order = ["red", "deferred", "orange", "yellow", "green", "grey"]
            fake = dict(b)
            fake_slots = {}
            for i, k in enumerate(order):
                sid = ids[i % len(ids)]
                src = dict(b["slots"][sid])
                src["verdict"] = k
                src["label"] = labels[k]
                src["why"] = (b.get("verdict_why") or {}).get(k, "")
                src["demoted"] = ("今天已登记发过 1 篇，这篇缓到明天：一天一篇别扎堆"
                                  if k == "deferred" else "")
                src["fresh"] = dict(src["fresh"] or {})
                src["fresh"]["list"] = ([{"sheet": "抖音视频数据", "row": "1", "title": "一条新素材",
                                          "author": "甲", "date": b["today"], "tl": 900,
                                          "status": "fresh"}]
                                        + (src["fresh"].get("list") or [])[:1])
                src["fresh"]["fresh"] = 1
                fake_slots[sid] = src
            fake["slots"] = fake_slots
            fake["published_today"] = [{"channel": "toutiao", "title": "今天已发的那篇",
                                        "pub_date": b["today"], "slot_key": ids[0]}]
            pg2 = br.new_page(viewport={"width": 1440, "height": 1200})
            pg2.on("pageerror", lambda e: errs.append("B:" + str(e)))
            pg2.route("**/api/plan/brief",
                      lambda route: route.fulfill(status=200, content_type="application/json",
                                                  body=json.dumps(fake)))
            pg2.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            wait_painted(pg2, want=len(order))
            g2 = pg2.evaluate(MEASURE)
            by_cls = {}
            for c in g2["cards"]:
                if c["cls"]:
                    by_cls.setdefault(c["cls"].split()[0], c)
            for k in order:
                key = "vb-" + k
                c = by_cls.get(key)
                r3 = check_color(g2, c, "B") if c else None
                ck("%s（%s）在真实 .card 上有 5px 色条且颜色对得上" % (key, labels[k]),
                   c is not None and isinstance(r3, tuple) and r3[1],
                   (r3[0] if isinstance(r3, tuple) else "这张卡没上色"))
            ck("顺延那张卡写着原因，且不是「缺料」那句话",
               any("一天一篇" in c["demote"] or "名额" in c["demote"] for c in g2["cards"]),
               next((c["demote"][:40] for c in g2["cards"] if c["demote"]), ""))
            ck("六档标签都出现在页面上（跟接口 label 同源）",
               all(labels[k] in " ".join(c["chip"] for c in g2["cards"]) for k in order),
               "、".join(sorted({c["chip"] for c in g2["cards"] if c["chip"]}))[:70])
            pg2.screenshot(path=str(out_dir / "schedule_brief_all6.png"), full_page=False)
            pg2.set_viewport_size({"width": 375, "height": 900})
            pg2.wait_for_timeout(1200)
            g3 = pg2.evaluate(MEASURE)
            mob = [c for c in g3["cards"] if c["cls"]]
            ck("375px 下色条仍然是 5px 实线（移动端没被媒体查询吃掉）",
               len(mob) >= 6 and all(c["width"] == EXPECT[c["cls"].split()[0]][1] for c in mob),
               "%d 张，宽度集合 %s" % (len(mob), sorted({c["width"] for c in mob})))
            over = pg2.evaluate("""() => {
               const c=[...document.querySelectorAll('[data-item]')];
               return {doc: document.documentElement.scrollWidth,
                       inner: c.filter(x=>x.scrollWidth>x.clientWidth+2).length};
            }""")
            ck("375px 下文档不横向溢出、卡片内部不溢出（色条与标签没把布局撑坏）",
               over["doc"] <= 390 and over["inner"] == 0,
               "文档宽 %d，内部溢出 %d 张" % (over["doc"], over["inner"]))
            pg2.screenshot(path=str(out_dir / "schedule_brief_375.png"))
            br.close()

        print("── 4. C 段：接口挂了不许装成「有结论」 ──")
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1440, "height": 1000})
            pg.route("**/api/plan/brief", lambda r: r.abort())      # 模拟工作台没起/接口挂了
            pg.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            for _ in range(20):
                pg.wait_for_timeout(1000)
                if "不猜" in pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent || ''"):
                    break
            txt = pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent || ''")
            n = pg.evaluate("() => document.querySelectorAll('[data-item]').length")
            off = pg.evaluate("""() => [...document.querySelectorAll('[data-item]')].map(c => {
                const cs = getComputedStyle(c);
                return {cls: (c.className.match(/\\bvb-\\w+/g) || []).join(' '),
                        width: cs.borderLeftWidth, color: cs.borderLeftColor};
            })""")
            ck("接口失败时图例明说「算不出、这里不猜」", "不猜" in txt, txt[:70])
            ck("接口失败时卡上没有任何判断类名", not [x for x in off if x["cls"]],
               "%d/%d 张仍带类名" % (len([x for x in off if x["cls"]]), n))
            judged = {to_rgb(v[0]) for v in EXPECT.values()}
            ck("接口失败时左边框仍是默认 1px、颜色不是任何一档的判断色（不是「看着像灰」而是真没上色）",
               all(x["width"] != "5px" and x["color"] not in judged for x in off),
               str(sorted({x["width"] for x in off}))[:40])
            pg.screenshot(path=str(out_dir / "schedule_brief_offline.png"))
            br.close()

        print("── 5. D 段：工作台进程还是旧判据（少 verdict_why/fresh_label）时，整个不画 ──")
        # 为什么单验这条：静态 JS 一改就生效、后端要重启才生效。这中间的混态就是
        # "新图例 + 旧档位名（今天就发）"——R1 要挡的正是页面上出现这句话。
        old = dict(b)
        old.pop("verdict_why", None)
        old.pop("fresh_label", None)
        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            pg = br.new_page(viewport={"width": 1440, "height": 1000})
            pg.route("**/api/plan/brief",
                     lambda route: route.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps(old)))
            pg.goto(base + "/static/schedule.html", wait_until="domcontentloaded")
            for _ in range(20):
                pg.wait_for_timeout(1000)
                t = pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent || ''")
                if "旧判据" in t or "不猜" in t:
                    break
            t = pg.evaluate("() => (document.getElementById('vb-legend')||{}).textContent || ''")
            painted = pg.evaluate("() => [...document.querySelectorAll('[data-item]')]"
                                  ".filter(c=>/\\bvb-\\w+/.test(c.className)).length")
            ck("旧判据的响应被拒收，图例说要重启（不拿旧五档画新页面）",
               "旧判据" in t or "重启" in t, t[:80])
            ck("旧判据时卡上不画任何颜色", painted == 0, "%d 张仍有色" % painted)
            ck("页面上不出现「今天就发」这句旧档位名",
               "今天就发" not in pg.evaluate("() => document.body.innerText"))
            pg.screenshot(path=str(out_dir / "schedule_brief_stale_api.png"))
            br.close()
        ck("页面没有 JS 报错（上色脚本没把渲染打断）", not errs, "; ".join(errs[:2])[:120])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("截图：temp/schedule_brief_1440.png / _all6.png / _375.png / _offline.png / _stale_api.png")
    if fails:
        print("\nSCHEDULE_BRIEF_WALK_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("SCHEDULE_BRIEF_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
