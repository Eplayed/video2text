# -*- coding: utf-8 -*-
"""真点一次「③ 出图」——攻略页这一档 0 元，把整条链路走完给号主看。

为什么敢真跑：这一档的画面是素材真帧、底图不叫 AI 画（0.5 元/张那一档才会扣钱），
86 号那条素材的帧池**已经判过帧**（18 张判完、14 张可用），所以连看图模型的钱也不会再花。

脚本走的是真入口：整理稿卡片 → 弹窗里「🎨 AI 海报」→ 灌入这条素材已有的文案（不重拆，
重拆要花钱）→ 点「③ 出图」→ 轮询 /api/poster/status 直到跑完 → 把 manifest 里那几笔账打出来。
跑之前会把原成品目录备份到 temp/，别覆盖号主手上下过的东西。
"""
import json
import subprocess
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15801"
SID = sys.argv[1] if len(sys.argv) > 1 else "86"
MAN = "output/poster/%s/manifest.json" % SID
OUT = "temp/poster_generate_run.png"

old = json.load(open(MAN, encoding="utf-8"))
plan = old.get("plan") or {}
publish = old.get("publish") or {}
if not plan.get("pages"):
    print("manifest 里没有 pages，换一条素材")
    sys.exit(2)

fails = []


def ck(name, ok, detail=""):
    print(("  ✓ " if ok else "  ✗ ") + name + (("　" + detail) if detail else ""))
    if not ok:
        fails.append(name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1050})
    # confirm/alert 必须**在导航之前**挂：用 pg.evaluate 装完再 goto 会被页面加载冲掉，
    # 结果 headless 里 confirm 自动返回 false，「③ 出图」走到那句
    # `if (!confirm(tip)) return;` 就静默退出——不报错、不发请求，看着像按钮坏了（这次踩过）
    pg.add_init_script("""() => { window.__dialogs = [];
        window.confirm = m => { window.__dialogs.push('confirm: ' + String(m).slice(0, 60)); return true; };
        window.alert = m => { window.__dialogs.push('alert: ' + String(m).slice(0, 80)); }; }""")
    pg.on("dialog", lambda d: d.accept())      # 万一还有没打桩到的原生弹窗，一律接受
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
    pg.evaluate("([plan, pub]) => pkRenderPlan(plan, pub, 0)", [plan, publish])
    pg.wait_for_timeout(800)
    go = pg.evaluate("() => document.getElementById('pk-go-top').textContent.trim()")
    print("整理稿 %s · 真点「③ 出图」（按钮上写的是：%s）" % (SID, go))
    ck("点之前按钮就是 0 元", "0 元" in go and "0.5" not in go, go)

    t0 = time.time()
    # 点之前先记下 manifest 的时间戳：不这么做，第一次轮询会看到上一单的 done=true 立刻收工，
    # 然后所有断言都打在**旧成品**上——今天就是这么差点把没跑的东西报成"验证通过"的
    before = subprocess.run(["stat", "-f", "%m", MAN], capture_output=True, text=True).stdout.strip()
    pg.click("#pk-go-top")
    # 先等任务真的起来（running=true），最多 20 秒；起不来就是没点着，直接判失败
    started = pg.evaluate("""(before) => new Promise(res => {
        let n = 0; const t = setInterval(() => {
            fetch('/api/poster/status').then(r => r.json()).then(s => {
                if (s.running) { clearInterval(t); res('running'); }
                else if (s.done && String(s.task_id || '') && s.progress) { clearInterval(t); res('done:' + s.progress); }
                if (++n > 20) { clearInterval(t); res('timeout'); }
            });
        }, 1000); })""", before)
    print("  任务起来了：%s" % started[:90])
    print("  确认框问了：", " ／ ".join(pg.evaluate("() => window.__dialogs || []"))[:200])
    ck("点「③ 出图」后任务真的开始跑", started.startswith("running") or started.startswith("done:"))
    st = {}
    while time.time() - t0 < 420:
        st = pg.evaluate("""() => fetch('/api/poster/status').then(r => r.json())""")
        if st.get("done") and not st.get("running"):
            break
        time.sleep(3)
    dur = time.time() - t0
    print("  跑了 %.0f 秒 · progress：%s" % (dur, (st.get("progress") or "")[:120]))
    ck("任务跑完没报错", not st.get("error"), (st.get("error") or "")[:150])
    pg.wait_for_timeout(1200)
    pg.screenshot(path=OUT)
    b.close()

after = subprocess.run(["stat", "-f", "%m", MAN], capture_output=True, text=True).stdout.strip()
new = json.load(open(MAN, encoding="utf-8"))
ck("manifest 是这一单新写的（不是 10-14 那份旧的）", after != before,
   "时间戳 %s → %s · created_at=%s" % (before, after, new.get("created_at")))
fin = new.get("final") or {}
ts = new.get("typeset") or {}
print("manifest：页数 %s · 版面 %s · 排版引擎 %s"
      % (len(fin.get("pages") or new.get("pages") or []),
         [p.get("layout_key") for p in (new.get("plan") or {}).get("pages") or []],
         ts.get("engine") or new.get("engine")))
print("  底图来源：%s ｜ 叫 AI 画了几张：%s ｜ 用的帧：%s"
      % (new.get("base_source") or ("素材真帧" if ts else "?"),
         new.get("paid_images", "?"),
         [len(p.get("photos") or []) for p in (new.get("plan") or {}).get("pages") or []]))
ck("成品清单里每页版面都是 guide",
   all((p.get("layout_key") == "guide") for p in (new.get("plan") or {}).get("pages") or []))
ck("排版层回了 problems 清单（哪怕为空也要有这个键，缺了就是零件半路丢了）",
   "problems" in ts, str(ts.get("problems"))[:80])
files = subprocess.run(["ls", "output/poster/%s" % SID], capture_output=True, text=True).stdout.split()
pngs = [f for f in files if f.endswith(".png")]
print("  盘上成品：%s" % " ".join(sorted(pngs)))
ck("出了 poster_p*.png 页文件", any(f.startswith("poster_p") for f in pngs))
sizes = subprocess.run(
    ["python3", "-c", """import glob,sys
from PIL import Image
for f in sorted(glob.glob('output/poster/%s/poster_p*.png')):
    im=Image.open(f); print(f.split('/')[-1], im.width, im.height)""" % SID],
    capture_output=True, text=True).stdout.strip().splitlines()
for s in sizes:
    print("   ", s)
ck("每张成品尺寸正常（936×1664）",
   all(len(s.split()) == 3 and s.split()[1] == "936" and s.split()[2] == "1664" for s in sizes),
   "; ".join(sizes))
net = [e for e in errs if "favicon" not in e]
ck("console 无报错", not net, "; ".join(net[:2]))
print("POSTER_GENERATE_RUN_" + ("OK" if not fails else "FAIL：" + "、".join(fails)))
sys.exit(0 if not fails else 1)
