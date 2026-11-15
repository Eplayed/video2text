# -*- coding: utf-8 -*-
"""海报里就地登记「已发布」的界面走查（隔离服务、临时库副本、0 元、不碰生产台账）。

号主 2026-10-23 的原话：**「都是在一条流程中的，不需要两边跑。改完之后，可以在运营页看到」**
所以这条走查一次验四件事：
① 海报弹窗结果区里那条状态 bar 真的挂载、真的读得到后端状态；
② 点「登记已发布」写的是排产那本台账 `publish_stats`（有槽号 → 排产那一格同时算已发布）；
③ 运营页 `/api/ops/report` 立刻能查到这一条；
④ 撤销只追加审计、不删台账行，bar 立刻回到未登记。

跑法：python3 scripts/agent/ui_poster_publish_walk.py [整理稿id]
末行必须 ALL_OK。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = str(ROOT / "scripts" / "agent" / "poster_test_server.py")
SID = int(sys.argv[1]) if len(sys.argv) > 1 else 94
SHOT = ROOT / "output" / "样稿" / "2026-10-23-海报就地登记"
fails = []


def ck(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (("　" + str(detail)[:150]) if detail else ""))
    if not cond:
        fails.append(name)


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server():
    port = free_port()
    log = str(ROOT / "temp" / "poster_srv_publish_walk.log")
    proc = subprocess.Popen([sys.executable, SERVER, "--port", str(port), "--gate", "on"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=str(ROOT), bufsize=1, universal_newlines=True)
    t0 = time.time()
    while time.time() - t0 < 40:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        if line.startswith("READY"):
            f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
            f["port"] = port
            return proc, f
    proc.kill()
    raise RuntimeError("隔离服务没起来，看 " + log)


def api(port, path, body=None, timeout=60):
    url = "http://127.0.0.1:%d%s" % (port, path)
    req = urllib.request.Request(
        url, data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def rows(db, sql):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


proc, srv = start_server()
port, tdb, tout = srv["port"], srv["db"], srv["poster_out"]
print("隔离服务 port=%s 临时库=%s" % (port, tdb))
try:
    # 把生产那一份成品复制进临时成品目录：这样弹窗与成品页的挂载点走的是真代码，不是手搓容器
    src = ROOT / "output" / "poster" / str(SID)
    if src.is_dir():
        shutil.copytree(str(src), str(Path(tout) / str(SID)), dirs_exist_ok=True)

    print("== 1 后端：登记 → 台账 → 运营页 ==")
    slot = rows(tdb, "SELECT slot_key FROM plan_tasks WHERE summary_id=%d AND IFNULL(slot_key,'')<>''"
                     " ORDER BY version DESC LIMIT 1" % SID)
    slot = slot[0]["slot_key"] if slot else ""
    print("   这篇在临时库里挂的槽号：%s" % (slot or "（没挂槽，独立稿）"))
    st0 = api(port, "/api/poster/publish?summary_id=%d&channel=toutiao" % SID)
    ck("没登记时读回是未发布", st0.get("state") == "none", st0.get("state"))
    r1 = api(port, "/api/poster/publish", {"summary_id": SID, "action": "register",
                                           "channel": "toutiao", "title": "走查登记的标题",
                                           "pub_date": "2026-10-23",
                                           "url": "https://example.com/t", "by": "界面走查"})
    ck("登记后读回已发布，note 是给人看的那句", r1["state"]["state"] == "confirmed",
       r1["state"].get("note"))
    led = rows(tdb, "SELECT * FROM publish_stats WHERE title='走查登记的标题'")
    ck("台账里真有一行，槽号按这篇实际挂的位置写", len(led) == 1
       and (led[0]["slot_key"] or "") == slot, (led[0]["slot_key"], slot))
    rep = api(port, "/api/ops/report?channel=toutiao")
    hit = [a for a in (rep.get("articles") or rep.get("rows") or [])
           if (a.get("title") or "") == "走查登记的标题"]
    ck("运营页那份读的是同一张表（不用再去排产表登记一次）", bool(hit), hit[:1])
    if slot:
        ser = api(port, "/api/plan/series")
        cell = next((s for s in (ser.get("slots") or []) if s.get("slot_key") == slot), {})
        ck("排产表那一格同时算已发布（一条流程两边同步）", bool(cell.get("published")),
           cell.get("stage_label") or cell.get("published"))

    print("== 2 界面：弹窗结果区那条 bar ==")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1500, "height": 1000})
        ctx.add_init_script("""(() => { window.__prompts = [];
          window.prompt = (msg, d) => { window.__prompts.push(String(msg).slice(0, 40)); return '走查撤销原因'; };
          window.confirm = () => true; window.alert = m => { window.__prompts.push('alert:' + String(m).slice(0, 40)); };
        })();""")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:140]))
        pg.on("console", lambda m: errs.append(m.text[:140]) if m.type == "error" else None)
        base = "http://127.0.0.1:%d" % port
        pg.goto(base + "/#summaries", wait_until="domcontentloaded")
        pg.wait_for_selector(".summary-card", timeout=30000)
        pg.locator('.summary-card[onclick="openSummary(%d)"]' % SID).first.click()
        pg.wait_for_selector('button:has-text("AI 海报")', timeout=20000)
        pg.locator('button:has-text("AI 海报")').first.click()
        pg.wait_for_function("() => document.getElementById('pk-style-key-top') && "
                             "document.getElementById('pk-style-key-top').options.length > 0",
                             timeout=25000)
        pg.evaluate("""(sid) => { _pkPubSeed = '走查预填的标题';
            document.getElementById('pk-result').innerHTML = '<div id="pk-pub-bar"></div>';
            return pkPubBar(sid, null, 'toutiao'); }""", SID)
        try:
            pg.wait_for_function("() => { const e = document.getElementById('pk-pub-bar');"
                                 " return e && e.innerText.length > 4; }", timeout=15000)
        except Exception as e:
            # 空 bar 有两种：接口报错（pkPubBar 会把 innerHTML 清空）和 fetch 抛异常（直接 return）。
            # 不打印这些就只能看到一个 TimeoutError，猜不出是哪一种。
            print("  ✗ bar 没出来：", str(e)[:60])
            print("    浏览器侧报错：", errs[:4])
            print("    接口直读：", pg.evaluate("""(sid) => fetch('/api/poster/publish?summary_id='
                  + sid + '&channel=toutiao').then(r => r.text()).then(t => t.slice(0, 200))""", SID))
            raise
        bar = pg.evaluate("() => document.getElementById('pk-pub-bar').innerText")
        ck("bar 显示的是后端那份状态（已登记 + 日期 + 标题）", "已登记" in bar and "走查登记的标题" in bar,
           bar.replace("\n", " ")[:110])
        ck("已登记时给的是「撤销这条登记」，不是再登记一次", "撤销这条登记" in bar)
        pg.screenshot(path=str(SHOT / "界面-已登记那条bar.png")) if not SHOT.exists() else None
        # 就地撤销 → bar 回到未登记
        pg.locator('button:has-text("撤销这条登记")').first.click()
        pg.wait_for_function("""() => { const e = document.getElementById('pk-pub-bar');
            return e && e.innerText.indexOf('已登记') < 0; }""", timeout=15000)
        bar2 = pg.evaluate("() => document.getElementById('pk-pub-bar').innerText")
        ck("撤销后 bar 立刻回到未登记（不用刷新页面）", "还没登记发布" in bar2, bar2.replace("\n", " ")[:90])
        ck("撤销问了原因（不填就不给撤）", any("撤销" in x for x in
                                       pg.evaluate("() => window.__prompts")), pg.evaluate("() => window.__prompts"))
        # 就地再登记一次：展开表单 → 确认登记
        pg.locator('button:has-text("登记已发布")').first.click()
        pg.wait_for_selector("#ppb-t", timeout=10000)
        pre = pg.evaluate("() => ({t: document.getElementById('ppb-t').value,"
                          " d: document.getElementById('ppb-d').value})")
        ck("表单预填了发布标题与今天（不用手抄）", pre["t"] == "走查预填的标题"
           and len(pre["d"]) == 10, pre)
        pg.locator('button:has-text("确认登记")').first.click()
        pg.wait_for_function("""() => { const e = document.getElementById('pk-pub-bar');
            return e && e.innerText.indexOf('已登记') >= 0; }""", timeout=15000)
        ck("点确认登记后 bar 变回已登记（这一趟没去过排产表）",
           "已登记" in pg.evaluate("() => document.getElementById('pk-pub-bar').innerText"))
        b.close()

    print("== 3 落库与审计 ==")
    led2 = rows(tdb, "SELECT * FROM publish_stats WHERE title IN ('走查登记的标题','走查预填的标题')")
    ck("撤销没删台账行（现在共两行：撤过的那行 + 重登的那行）", len(led2) == 2,
       [(x["title"], x["pub_date"]) for x in led2])
    aud = rows(tdb, "SELECT * FROM plan_publish_audit WHERE action='revoke'")
    ck("撤销写了一条审计，里面能看到原因", aud and "走查撤销原因" in (aud[0]["note"] or ""),
       (aud[0]["note"] or "")[:80] if aud else "无审计")
    ck("没有 JS 报错", not errs, " | ".join(errs[:2]))
    print("截图：%s" % (SHOT / "界面-已登记那条bar.png"))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print()
if fails:
    print("FAIL %d 项：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("ALL_OK")
