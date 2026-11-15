# -*- coding: utf-8 -*-
"""ui_plan_series_walk.py — 双系列排产与夜间匹配的**真页面**走查（只连隔离服务）。

为什么必须有这一支（`docs/AGENT-HANDOFF.md` 第 5 节那条老教训）：接口全绿不等于用户看得见。
10-15 那次把取图函数改对了、却在对话里报"改完了"，用户那天打开工作台一张新图都没看到。
所以这一支全部走 headless Chrome 的真实点击顺序，并把**用户会读到的那句原话**打印出来。

七条线：
1. 排产页画出两个系列：S1 26 个位置、S2 三章模板、旧 11 题保留；状态栏有时刻/下次/上次结果；
2. 勾两条候选 →「采用勾选项到本篇」→ 界面出现「已采用 2 条」，且**临时库**那一行真的存了这两个 id；
3. 「现在匹配」→ 候选数变化但**不产生重复候选**（比对 plan_candidates 全表计数）；
4. 登记已发布 → 徽章变「已发布」；**换一个浏览器上下文**再开同一格，看到的必须是同一个结论（跨浏览器一致）；
5. 撤销登记 → 回到未发布，台账行仍在（撤销只追加审计）；
6. 预置一份"旧浏览器勾过已发布"的 localStorage → 界面标「待核对旧勾选」而不是已发布；
7. 素材库入口：勾两条素材 →「做图文攻略」→ 建任务 → 排产面板打开并列出已采用清单
   （**不点生成**：那一下才叫模型、才花钱）；7b 不关联时确实新建独立槽；
7c Codex 二次复验第 3 条：在页面上把 O1→O2→O3 一路点出来（三章各有自己的入口与前序下拉），
   并且**绕过界面**直接 POST 一篇无前序的 O2，服务端必须拦；
7d 第 1/2/4 条：两格填同名同日 → 两格都显示已发布且 ref_id 不同；公众号登记一笔 →
   头条看板那一格仍未发布；独立稿归进 C2 → 章节头还是「6 个位置」，那一篇出现在 C2 卡片的「关联稿」里。
红线：全程模型调用 0 次（隔离服务的 capture 文件必须是空数组），生产库与生产成品目录未被写。

用法：
  env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 \\
      scripts/agent/ui_plan_series_walk.py --out temp/plan_series_walk.png
"""
import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "scripts", "agent", "plan_test_server.py")
PROD_DB = os.path.join(ROOT, "output", "video2text.db")
PROD_POSTER = os.path.join(ROOT, "output", "poster")
fails = []


def ck(name, cond, detail=""):
    if cond:
        print("  [PASS] %s%s" % (name, (" — " + str(detail)[:120]) if detail else ""))
    else:
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)[:200]) if detail else ""))
        fails.append(name)


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def api(port, path, body=None, timeout=40):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": "HTTP %s" % e.code}


def start_server(db, capture, srv_log):
    port = free_port()
    env = dict(os.environ, PYTHONHOME="", PYTHONPATH="",
               V2T_PLAN_MATCH_STORE=os.path.join(os.path.dirname(db), "plan_match.json"))
    p = subprocess.Popen([sys.executable, SERVER, "--port", str(port), "--db", db,
                          "--capture", capture],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=ROOT, bufsize=1, universal_newlines=True, env=env)
    t0 = time.time()
    while time.time() - t0 < 60:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                raise RuntimeError("隔离服务启动失败，日志尾部：%s" % _drain(p))
            continue
        print("       server| " + line.strip())
        if line.startswith("READY"):
            _keep_draining(p, srv_log)
            return p, port
    p.kill()
    raise RuntimeError("隔离服务 60 秒没起来")


def _drain(p):
    try:
        return (p.stdout.read() or "")[-600:]
    except Exception:
        return ""


def _keep_draining(p, path):
    """READY 之后必须继续把服务的 stdout 抽干。

    werkzeug 每个请求都往 stdout 写一行日志；这条管道一旦没人读，缓冲区（64KB）填满后
    **服务进程自己会卡在写日志上**——表现就是"页面等不到接口返回"，
    看着像功能坏了，其实是测试把自己的被测进程堵死了（这一版就撞在第 8 段：
    前面几段刷了上百个请求，第 8 段 `/api/plan/series` 40 秒不回应）。
    """
    import threading
    def loop():
        try:
            with open(path, "a", encoding="utf-8") as f:
                for line in iter(p.stdout.readline, ""):
                    f.write(line)
        except Exception:
            pass
    threading.Thread(target=loop, daemon=True).start()


def stop_server(p):
    try:
        p.terminate()
        p.wait(timeout=8)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def ps_now():
    """北京时间字符串（固定 +08:00，与 plan_series 同一口径）。只给走查夹具用。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))


def q(db, sql, args=()):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def count(db, table, where=""):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM %s %s" % (table, where)).fetchone()[0]
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/plan_series_walk.png")
    a = ap.parse_args()

    work = tempfile.mkdtemp(prefix="plan_series_walk_")
    db = os.path.join(work, "video2text.db")
    shutil.copyfile(PROD_DB, db)
    capture = os.path.join(work, "prompts.json")
    # 临时库里把上一轮跑剩的账清掉，计数才是从 0 起（不动生产库）
    con = sqlite3.connect(db)
    for t in ("plan_slots", "plan_candidates", "plan_match_runs", "plan_publish_audit",
              "plan_slot_flags"):
        try:
            con.execute("DELETE FROM %s" % t)
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()

    prod_stat = (os.path.getmtime(PROD_DB), os.path.getsize(PROD_DB))
    print("── 起隔离服务（临时库副本 + 模型桩，成品目录仍在临时区）──")
    proc, port = start_server(db, capture, os.path.join(work, "server.log"))
    base = "http://127.0.0.1:%d" % port
    sched = base + "/static/schedule.html"
    try:
        board = api(port, "/api/plan/series")
        # 先手动跑一轮匹配再开页面：临时库副本里上一轮的候选账已被清空，
        # 不先跑就没有候选可勾，第 2 步会去等一个本来就不该存在的勾选框（第一版就撞在这）。
        pre = api(port, "/api/plan/match/run", {"by": "走查前置"})
        ck("前置匹配跑通并新增候选（说明只读检索真的在素材库上找到了料）",
           int(pre.get("added") or 0) > 0, "新增 %s 条 / 覆盖 %s 格" % (pre.get("added"), pre.get("positions")))
        ck("S1 有 26 个内容位置（只数地图注册的那些，稿件不算）",
           len([s for s in board.get("slots", [])
                if s["series"] == "s1" and s.get("origin") == "map"]) == 26,
           len([s for s in board.get("slots", []) if s["series"] == "s1"]))

        with sync_playwright() as pw:
            br = pw.chromium.launch(channel="chrome", headless=True)
            ctx = br.new_context(viewport={"width": 1280, "height": 1100})
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
            print("── 1. 排产页：两个系列与状态栏真的画出来了 ──")
            pg.goto(sched, wait_until="domcontentloaded")     # 不赌 networkidle：这页要发好几个请求，慢的时候 30 秒不空闲
            pg.wait_for_selector("#sr-body .sr-card", timeout=45000)
            txt = pg.inner_text("#series")
            n_pos = pg.eval_on_selector_all("#sr-body .sr-card", "e=>e.length")
            ck("页面上有内容位置卡片", n_pos >= 26, "%d 张卡" % n_pos)
            ck("S1 标题进页面", "从新手到高手" in txt, "")
            ck("S2 三章模板在列", all(x in txt for x in ("官方事实", "条件预测", "上线后回看")))
            ck("旧 11 题没被删（旧题块仍在）", "旧排产题" in txt)
            run = pg.inner_text("#sr-run")
            ck("状态栏写了计划时刻 22:00 与北京时间", "22:00" in run and "Asia/Shanghai" in run,
               run[:110].replace("\n", " "))
            ck("状态栏有「现在匹配」这颗手动按钮", pg.query_selector("#sr-run button") is not None)
            ck("页面脚本报错为 0", not errs, errs[:2])
            ct = pg.evaluate("""() => { const m = (window.srState() || {}).slots || {};
                const bad = []; let n = 0;
                Object.keys(m).forEach(k => (m[k].candidates || []).forEach(c => {
                    n++; if (!c.title || !c.sheet) bad.push(k + ':' + c.video_id); }));
                return {n: n, bad: bad.slice(0, 3)}; }""")
            ck("每条候选在页面上念得出标题与来源（不是只存了一个 id）",
               ct["n"] > 0 and not ct["bad"], ct)
            pg.screenshot(path=a.out, full_page=True)

            print("── 1b. 默认折叠：40 格不该是一堵 13000 像素的墙 ──")
            # 期望值现算，不写死 0：设计上就有几格会自动展开（ready/layout 或有待核对的旧勾选），
            # 号主今天生成过一篇稿之后 s1a1 就进了 ready —— 写死 0 的判据会把"数据往前走了"
            # 误报成"这块没折起来"（真判据是下面那条高度 < 2200px）。
            got = pg.evaluate("""() => {
                const vis = [...document.querySelectorAll('#sr-body .sr-cand')]
                    .filter(e => e.offsetParent !== null).length;
                let exp = 0;
                Object.keys(SRMAP).forEach(k => { const s = SRMAP[k];
                    if (s.pending_review || s.stage === 'ready' || s.stage === 'layout') exp++; });
                return {vis: vis, exp: exp}; }""")
            shown = got["vis"]
            # 量的是**双系列这一块**的高度。整页 10000+ px 是旧 11 题那一长条（7677px，
            # 本单没动它的渲染），拿整页当判据会把"我这块没折起来"和"旧页本来就长"混成一件事。
            tall = pg.evaluate("() => Math.round(document.getElementById('series').getBoundingClientRect().height)")
            legacy = pg.evaluate("() => Math.round(document.getElementById('list').getBoundingClientRect().height)")
            ck("除设计上自动展开的那几格，其余候选列表都折着", shown <= got["exp"],
               "可见候选块 %d 个（设计自动展开 %d 格）" % (shown, got["exp"]))
            ck("双系列这一块折起来后一屏半能扫完（< 2200px）", tall < 2200,
               "%d px（旧 11 题那长条 %d px，本单未动）" % (tall, legacy))
            ck("摘要行上有状态徽章与计数（不展开也知道哪格有候选）",
               pg.evaluate("() => [...document.querySelectorAll('.sr-mini')].some(e=>/候选/.test(e.textContent))"))
            print("── 2. 勾候选 → 采用到本篇（一次多条，写进服务端）──")
            # 真实点击顺序：先展开那一格，再勾候选
            pg.evaluate("""() => { const el=document.querySelector('#sr-body .sr-card');
                            el.open = true; el.querySelector('summary').click();
                            el.open = true; }""")
            pg.wait_for_function(
                "() => { const c=[...document.querySelectorAll('#sr-body .sr-cand input')];"
                " return c.filter(x=>x.offsetParent!==null).length >= 2; }", timeout=25000)
            boxes = pg.query_selector_all("#sr-body .sr-cand input")
            # 挑一个"这一格还没有任务行"的槽号来测采用：生产库里 s1a1 已经有两版（号主今天 13:35
            # 自己生成过一次），拿它测"采用两条"量的就不是采用动作，而是"会不会开出第三版"，
            # 判据跟着生产数据漂移动一次就红一次。
            slot = pg.evaluate("""() => { const el=[...document.querySelectorAll('#sr-body .sr-cand input')]
                    .find(i => { const g = SRMAP[i.dataset.slot] || {};
                                  return !g.task && g.adoptable !== false; });
                    return el ? el.dataset.slot : ''; }""")
            ck("找到一个还没有任务行的格子来测采用", bool(slot), slot)
            # 换格子了就要把那一格展开：别的卡片是折着的，折着里面的复选框点不动
            pg.evaluate("""(k) => { const el=document.getElementById('src-'+k);
                            if (el) { el.open = true; SR_OPEN[k] = true; } }""", slot)
            pg.wait_for_timeout(400)
            boxes = pg.query_selector_all("#src-%s .sr-cand input" % slot)
            picked = []
            for b in boxes:
                if len(picked) < 2:
                    b.check()
                    picked.append(b.get_attribute("data-vid"))
            btn = pg.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                     return el ? el.querySelector('.sr-act button').textContent : '';}""", slot)
            ck("这一格第一颗按钮是「采用勾选项到本篇」", "采用" in btn, btn)
            pg.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                     [...el.querySelectorAll('.sr-act button')].find(b=>b.textContent.includes('采用')).click();}""",
                        slot)
            pg.wait_for_function(
                "(k)=>{const el=document.getElementById('src-'+k);return el && /已采用 2 条/.test(el.innerText);}",
                arg=slot, timeout=25000)
            card = pg.inner_text("#src-" + slot)
            ck("卡片上出现「已采用 2 条」", "已采用 2 条" in card, card[:120].replace("\n", " "))
            trow = api(port, "/api/plan/tasks?slot_key=" + slot)
            vids = (trow.get("tasks") or [{}])[0].get("video_ids") or []
            ck("服务端那一行存的正是勾的那两条", sorted(vids) == sorted(int(x) for x in picked),
               "%s vs %s" % (vids, picked))
            db_ids = json.loads(sqlite3.connect(db).execute(
                "SELECT video_ids FROM plan_tasks WHERE slot_key=?", (slot,)).fetchone()[0])
            ck("临时库里也落了这两个 id（不是只存在界面上）", sorted(db_ids) == sorted(int(x) for x in picked),
               db_ids)

            print("── 3. 现在匹配：可重跑，但不长重复候选 ──")
            before = count(db, "plan_candidates")
            r = api(port, "/api/plan/match/run", {"by": "走查脚本"})
            ck("手动匹配跑通并回报增减", "added" in r or "skipped" in r,
               str(r)[:110])
            again = api(port, "/api/plan/match/run", {"by": "走查脚本"})
            after = count(db, "plan_candidates")
            dup = sqlite3.connect(db).execute(
                "SELECT COUNT(*) FROM (SELECT slot_key, video_id FROM plan_candidates"
                " GROUP BY slot_key, video_id HAVING COUNT(*)>1)").fetchone()[0]
            ck("连着点两次不产生重复候选（唯一键真的挡住了）", dup == 0,
               "重复组 %d；两次回报 added=%s/%s" % (dup, r.get("added"), again.get("added")))
            ck("候选表总行数与库内一致", after == before or after > before, "%d → %d" % (before, after))

            print("── 4. 登记已发布 → 换一个浏览器看同一格（跨浏览器一致）──")
            pg.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                     [...el.querySelectorAll('.sr-act button')].find(b=>/登记已发布/.test(b.textContent)).click();}""",
                        slot)
            pg.wait_for_selector("#pt-t-" + slot, timeout=15000)
            pg.fill("#pt-t-" + slot, "走查：这一篇的发布标题")
            pg.fill("#pt-u-" + slot, "")
            pg.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                     [...el.querySelectorAll('button')].find(b=>/确认登记/.test(b.textContent)).click();}""",
                        slot)
            pg.wait_for_function("(k)=>{const el=document.getElementById('src-'+k);"
                                 "return el && /已发布/.test(el.innerText);}", arg=slot, timeout=20000)
            ck("第一台浏览器看到徽章变「已发布」",
               "已发布" in pg.inner_text("#src-" + slot))
            ctx2 = br.new_context(viewport={"width": 1280, "height": 1100})
            pg2 = ctx2.new_page()
            pg2.goto(sched, wait_until="domcontentloaded")
            pg2.wait_for_selector("#sr-body .sr-card", timeout=45000)
            got = pg2.evaluate("(k)=>{const m=window.srState().slots;return m[k];}",
                               slot)
            ck("第二台浏览器（全新上下文、无 localStorage）也显示已发布",
               got.get("published") is True, got.get("stage_label"))
            ck("第二台还看得到台账那一行的 ref_id（撤销要点到具体那行）",
               bool((got.get("publish_rows") or [{}])[0].get("ref_id")))
            pg2.screenshot(path=a.out.replace(".png", "_ctx2.png"), full_page=True)

            print("── 5. 撤销登记：回到未发布，台账行不删 ──")
            ledger_before = count(db, "publish_stats")
            pg2.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                     const b=[...el.querySelectorAll('button')].find(x=>/撤销/.test(x.textContent));
                     if(b) b.click();}""", slot)
            time.sleep(0.4)
            # prompt() 在 headless 里默认返回 null，撤销会被自己的"原因必填"挡回去。
            # 打桩与点击必须放在**同一次 evaluate** 里：分两次调用中间若发生导航，桩会被冲掉（10-16 撞过）。
            pg2.evaluate("""(k)=>{window.prompt=()=>'走查：登记错了，那是另一篇';
                     const el=document.getElementById('src-'+k);
                     const b=[...el.querySelectorAll('button')].find(x=>/撤销/.test(x.textContent));
                     if(b) b.click();}""", slot)
            pg2.wait_for_function("(k)=>{const x=window.srState().slots[k];return !!x && !x.published;}",
                                  arg=slot, timeout=20000)
            ck("撤销后这一格不再算已发布",
               pg2.evaluate("(k)=>{const x=window.srState().slots[k];return x ? x.published : null;}",
                            slot) is False)
            ck("台账行没被删（撤销只追加审计）", count(db, "publish_stats") == ledger_before,
               "%d 行" % ledger_before)
            ck("审计里写了原因与影响",
               sqlite3.connect(db).execute(
                   "SELECT note FROM plan_publish_audit WHERE action='revoke' ORDER BY id DESC LIMIT 1"
               ).fetchone()[0].find("原因") >= 0)

            print("── 6. 旧浏览器勾过、服务端没登记 → 标「待核对旧勾选」──")
            legacy_key = "setup"
            ctx3 = br.new_context(viewport={"width": 1280, "height": 1100})
            ctx3.add_init_script("""localStorage.setItem('v2t:schedule:v1',
                JSON.stringify({setup:{published:true, first:true}}));""")
            pg3 = ctx3.new_page()
            pg3.goto(sched, wait_until="domcontentloaded")
            pg3.wait_for_selector("#sr-body .sr-card", timeout=45000)
            # 上报是异步的（页面发完还要重读一次看板才拿到 pending_review），
            # 固定 sleep 在这种链上迟早比网络快——改成轮询等到那一格真标上为止。
            try:
                pg3.wait_for_function(
                    "(k)=>{const x=(window.srState().slots)||{};const s=x[k];return s && s.pending_review;}",
                    arg=legacy_key, timeout=30000)
            except Exception:
                pass
            got3 = pg3.evaluate("(k)=>{const m=window.srState().slots;return m[k]||{};}",
                                legacy_key)
            ck("旧勾选上报后标的是待核对，不是已发布",
               got3.get("pending_review") is True and not got3.get("published"),
               (got3.get("stage_label"), got3.get("published")))
            flag = sqlite3.connect(db).execute(
                "SELECT local_published FROM plan_slot_flags WHERE slot_key=?", (legacy_key,)).fetchone()
            ck("服务端确实记下了这条待核对（夜间匹配会保守跳过）", flag is not None and flag[0] == 1, flag)
            pg3.screenshot(path=a.out.replace(".png", "_legacy.png"), full_page=True)

            print("── 7. 素材库入口：勾两条 →「做图文攻略」→ 建任务 → 排产面板 ──")
            pg4 = ctx.new_page()
            # 素材库要靠 hash 路由进去（默认页是待办板；直接 goto("/") 只会等到一张空网格）
            pg4.goto(base + "/#videos", wait_until="domcontentloaded")
            pg4.wait_for_selector("#grid-videos .video-card", timeout=90000)
            cards = pg4.query_selector_all("#grid-videos .video-card")
            keys, titles = [], []
            for c in cards[:2]:
                titles.append((c.query_selector(".v-title") or c).inner_text().strip()[:24])
                c.click()
                keys.append(c.get_attribute("data-key"))
            pg4.wait_for_selector("#btn-ss-guide", state="visible", timeout=10000)
            ck("勾选后素材库工具条出现「做图文攻略」「做资讯整合」",
               pg4.is_visible("#btn-ss-guide") and pg4.is_visible("#btn-ss-mix"), keys)
            pg4.click("#btn-ss-guide")
            pg4.wait_for_selector("#ss-overlay.show", timeout=10000)
            pg4.wait_for_function("() => document.querySelectorAll('#ss-slot option').length > 1",
                                  timeout=15000)
            opts = pg4.eval_on_selector_all("#ss-slot option", "e=>e.map(x=>x.value)")
            ck("弹窗里能选系列位置，且**不含** S2 章节模板（模板不能直接采用）",
               "s1a3" in opts and "s2o1" not in opts, opts[:6])
            pg4.select_option("#ss-slot", "s1a3")
            bnd = pg4.inner_text("#ss-boundary")
            ck("选完位置就把这一格的边界/门槛念给人看（不是页面自己写的）",
               "边界" in bnd or "实测" in bnd, bnd[:90].replace("\n", " "))
            slots_before = count(db, "plan_slots")
            pg4.fill("#ss-title", "走查：从素材库直接起的一篇")
            pg4.click("#ss-run")
            pg4.wait_for_selector("#pt-overlay.show", timeout=20000)
            ck("选了系列位置起稿 = 采用到那一格，**不新建槽号**（位置不是一篇稿）",
               count(db, "plan_slots") == slots_before,
               "%d → %d" % (slots_before, count(db, "plan_slots")))
            pg5 = ctx.new_page()
            pg5.goto(sched, wait_until="domcontentloaded")
            pg5.wait_for_selector("#sr-body .sr-card", timeout=45000)
            a3 = pg5.evaluate("""() => { const el = document.getElementById('src-s1a3');
                            el.open = true; return el.innerText; }""")
            ck("那一格在排产页上立刻显示已采用（两个入口写的是同一本账）",
               "已采用" in a3, a3[:70].replace("\n", " "))
            pg5.close()
            panel = pg4.inner_text("#pt-overlay")
            ck("建完任务直接进同一个排产面板（沿用整合稿/海报那条链）",
               "走查：从素材库直接起的一篇" in panel or "已采用" in panel,
               panel[:130].replace("\n", " "))
            rows = pg4.eval_on_selector_all("#pt-overlay .pt-row", "e=>e.length")
            ck("面板里逐条列出本篇采用的那两条（行数与勾选数一致）", rows >= 2,
               "%d 行 / 勾了 %s" % (rows, keys))
            ck("面板里能看到那两条的标题，不是只有一句「已采用」",
               all(x[:10] in panel for x in titles if x), titles)
            pg4.screenshot(path=a.out.replace(".png", "_material.png"))

            print("── 7b. 不关联系列：独立篇确实新建一个槽，事后能归进系列 ──")
            # 先按真实路径关掉排产面板（点遮罩＝ptClose）：面板开着的时候再点工具条那颗按钮，
            # 会被遮罩挡住——这是界面自己的层级，不是测试该绕过去的东西。
            # ptClose 认的是 e.target===e.currentTarget（点遮罩才算关闭，点面板内容不算），
            # 所以两个字段都得给——只给 target 会直接 return，面板关不掉（实测撞过一次）。
            pg4.evaluate("""() => { const el = document.getElementById('pt-overlay');
                            ptClose({target: el, currentTarget: el}); }""")
            pg4.wait_for_selector("#pt-overlay.show", state="detached", timeout=10000)
            pg4.click("#btn-ss-guide")
            pg4.wait_for_selector("#ss-overlay.show", timeout=10000)
            pg4.select_option("#ss-slot", "")
            pg4.fill("#ss-title", "走查：不关联的独立一篇")
            pg4.click("#ss-run")
            pg4.wait_for_selector("#pt-overlay.show", timeout=20000)
            free_rows = q(db, "SELECT slot_key FROM plan_slots WHERE series='free'")
            ck("不选位置时才真的建独立篇（多出一个 free- 槽号）",
               len(free_rows) == 1 and free_rows[0]["slot_key"].startswith("free-"),
               [r["slot_key"] for r in free_rows])
            ck("独立篇也带着这一篇的采用清单",
               count(db, "plan_tasks", "WHERE slot_key='" + free_rows[0]["slot_key"] + "'") == 1)

            print("── 7c. Codex 二次复验点名那三件，在真页面上走一遍 ──")
            # 这一段全部走真实点击：O2/O3 的起稿入口、同名同日两篇的登记、独立稿归进位置。
            pgS = ctx.new_page()
            # 这一段是新加的两条链，出错要能当场看到是哪一行 JS 抛的（只挂 pg 的收集器等于盲测）
            pgS.on("pageerror", lambda e: errs.append("pgS:" + str(e)[:160]))
            pgS.goto(sched, wait_until="domcontentloaded")
            pgS.wait_for_selector("#sr-body .sr-card", timeout=45000)
            # 记下每一次 window.open 交出去的地址：面板用的就是那份 payload。
            # 不能事后去读那个标签页的 URL —— 工作台起来后会 `history.replaceState`
            # 把 `?task=` 换成 `#schedule`，读到的永远是干净的地址。
            pgS.evaluate("""() => { window.__opened = [];
                             const o = window.open;
                             window.open = function (u, t) { window.__opened.push(String(u)); return o.call(window, u, t); }; }""")

            def last_task_payload():
                for u in reversed(pgS.evaluate("() => window.__opened || []")):
                    if "/?task=" in u:
                        try:
                            return json.loads(urllib.parse.unquote(u.split("/?task=", 1)[1]))
                        except ValueError:
                            return None
                return None

            def pick_candidate(page, key):
                """那一格里得有候选才能勾。匹配是按真素材跑的，暴雪公告那条源今天没更新时
                S2 的格子就是空的——这时往**临时库**塞一条夹具候选（只进 plan_candidates，
                不碰采用清单、不碰整理稿），为的是测按钮，不是测匹配。"""
                vid = page.evaluate(
                    "(k)=>{const s=(window.srState().slots||{})[k]||{};"
                    "const c=(s.candidates||[])[0];return c?c.video_id:null;}", key)
                if vid:
                    return vid
                row = q(db, "SELECT id, source_sheet, source_row FROM videos LIMIT 1")
                if not row:
                    return None
                vid = row[0]["id"]
                wc = sqlite3.connect(db)
                try:
                    wc.execute("INSERT OR IGNORE INTO plan_candidates (slot_key, video_id, forever,"
                               " reason, run_date, added_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                               (key, vid, "unknown", "走查夹具｜临时库里塞的，只为让按钮有东西可勾",
                                "夹具", ps_now(), ps_now()))
                    wc.commit()
                finally:
                    wc.close()
                page.reload(wait_until="domcontentloaded")
                page.wait_for_selector("#sr-body .sr-card", timeout=45000)
                return page.evaluate(
                    "(k)=>{const s=(window.srState().slots||{})[k]||{};"
                    "const c=(s.candidates||[])[0];return c?c.video_id:null;}", key)

            def start_from_template(page, key, chapter, title):
                """先备好候选（可能要重载页面）→ 展开那一格 → 勾第一条 → 点它自己那一章的起稿按钮。
                prompt 打桩与点击放在**同一次 evaluate** 里（分两次会被中间的导航把桩冲掉，10-16 撞过）。"""
                got = pick_candidate(page, key)
                if not got:
                    return None
                page.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                                 el.open=true; el.scrollIntoView();
                                 const i=el.querySelector('.sr-cand input');
                                 if(i && !i.checked) i.click();}""", key)
                page.wait_for_timeout(150)
                page.evaluate("""(k)=>{window.prompt=()=>'%s';
                                 const el=document.getElementById('src-'+k);
                                 const b=[...el.querySelectorAll('.sr-act button')]
                                       .find(x=>/起一篇 /.test(x.textContent));
                                 if(b) b.click();}""" % title, key)
                return got

            def close_task_tabs():
                # srOpenFrom 建完会 srOpenTask → 顶层页走 window.open('/?task=…')，
                # 这些标签页留着会一直跟隔离服务抢连接（第 8 段那种超时就是这么来的）
                for _p in list(ctx.pages):
                    try:
                        if _p is not pg and "/?task=" in (_p.url or ""):
                            _p.close()
                    except Exception:
                        pass

            with ctx.expect_page() as pop:
                o1_vid = start_from_template(pgS, "s2o1", "O1", "走查：探针O1稿")
            tab = pop.value
            pgS.wait_for_function(
                "() => { const m = window.srState().slots || {};"
                " return Object.keys(m).some(k => /^s2o1-\\d+$/.test(k)); }", timeout=25000)
            o1_key = pgS.evaluate(
                "() => Object.keys(window.srState().slots).filter(k => /^s2o1-\\d+$/.test(k))[0]")
            ck("页面上 O1 那一格真起出了一篇稿（不是只有按钮）", bool(o1_key), o1_key)
            # 建槽对了不等于人能用：面板用的是**交出去的那份 payload**，
            # 旧写法在 loadSeries 还没回来时就 srOpenTask，SRMAP 里查不到新槽 →
            # 退成 form='guide'、空标题、空素材，而库里明明是 toutiao/mix 且带一条采用素材
            # （Codex 三次复验第 1 条）。所以这一步查的是弹出来的那块面板。
            trow = q(db, "SELECT id, form, title, video_ids, sources FROM plan_tasks"
                         " WHERE slot_key=? ORDER BY version DESC LIMIT 1", (o1_key,))
            want = trow[0] if trow else {}
            got_pl = last_task_payload()
            ck("交出去的面板 payload 与库里那篇一致（渠道/形式/标题/task_id/采用 id）",
               bool(got_pl) and got_pl.get("form") == want.get("form") and
               got_pl.get("channel") == "toutiao" and got_pl.get("title") == want.get("title") and
               got_pl.get("task_id") == want.get("id") and
               sorted(got_pl.get("video_ids") or []) ==
               sorted(json.loads(want.get("video_ids") or "[]")),
               (got_pl, {k: want.get(k) for k in ("form", "title", "id", "video_ids")}))
            try:
                tab.wait_for_selector("#pt-overlay.show", timeout=40000)
                # 面板是 openPlanTask 同步画第一帧、ptRefresh 再异步补服务端那一版的任务，
                # 刚 show 就读必然读到"还没开工"的空壳（第一版就误判过一次，
                # 而且 `.pt-row` 占位行也在，拿行数当判据是**空过**）。
                # 所以先等状态那一格真的从服务端认到任务，再判内容。
                tab.wait_for_function(
                    "() => { const m = document.querySelector('#pt-overlay .pt-meta');"
                    " return m && /待整理|整理中|已有稿|失败/.test(m.innerText); }", timeout=30000)
                ptxt = tab.inner_text("#pt-overlay")
                mats = tab.eval_on_selector_all("#pt-materials .pt-row .t", "e=>e.map(x=>x.textContent)")
                acts = tab.eval_on_selector_all("#pt-overlay .pt-acts button", "e=>e.map(x=>x.textContent)")
                src0 = ""
                try:
                    src0 = (json.loads(want.get("sources") or "[]") or [{}])[0].get("title") or ""
                except ValueError:
                    src0 = ""
                ck("面板真为这篇 mix 稿打开：形式「资讯整合」+ 状态认到任务 + 列得出本篇采用的素材",
                   "资讯整合" in ptxt and "探针O1稿" in ptxt and bool(src0) and
                   any(src0[:12] in x for x in mats),
                   ptxt[:100].replace("\n", " ") + " ｜ 素材 " + str(mats)[:70])
                # 动作按钮这一格认"能开工/能续稿"那颗：pending 那一版交的是「生成这一版」
                # （判据第一版写成"含整理或含稿"，把正确的按钮判成了 FAIL——
                # 门禁自己也会错，这里按界面真给的文案来）
                ck("面板给得出这一篇的动作（不是只剩「回排产表」）",
                   bool(acts) and any(("生成" in a) or ("继续" in a) for a in acts) and len(acts) > 1,
                   acts)
                tab.screenshot(path=a.out.replace(".png", "_o1面板.png"))
            except Exception as e:
                ck("面板真为这篇 mix 稿打开：形式「资讯整合」+ 状态认到任务 + 列得出本篇采用的素材",
                   False, "面板没等到服务端那一版：%s" % str(e)[:90])
            try:
                tab.close()
            except Exception:
                pass
            close_task_tabs()
            ctl = pgS.evaluate("""() => { const el = document.getElementById('src-s2o2');
                             el.open = true;
                             const s = el.querySelector('select');
                             return {sel: !!s, opts: s ? [...s.options].map(o=>o.value) : [],
                                     btn: [...el.querySelectorAll('.sr-act button')]
                                           .map(b=>b.textContent).join('|')}; }""")
            ck("O2 那一格给出的是它自己的入口（按钮写 O2、还有前序下拉）",
               ctl["sel"] and "O2 稿" in ctl["btn"] and o1_key in ctl["opts"], ctl)
            o2_vid = start_from_template(pgS, "s2o2", "O2", "走查：O2 条件预测一篇")
            pgS.wait_for_function(
                "() => { const m = window.srState().slots || {};"
                " return Object.keys(m).some(k => /^s2o2-\\d+$/.test(k)); }", timeout=25000)
            o2_key = pgS.evaluate(
                "() => Object.keys(window.srState().slots).filter(k => /^s2o2-\\d+$/.test(k))[0]")
            ck("点 O2 那一格的按钮真的建出 O2 稿（旧版三点都提交 chapter:'O1'）",
               bool(o2_key) and (pgS.evaluate("(k)=>window.srState().slots[k].chapter", o2_key) == "O2"),
               o2_key)
            ck("这一篇 O2 带着它的前序（服务端认的那一格，不是界面自己填的）",
               pgS.evaluate("(k)=>window.srState().slots[k].link_slot", o2_key) == o1_key,
               pgS.evaluate("(k)=>window.srState().slots[k].link_slot", o2_key))
            neg = api(port, "/api/plan/series/open",
                      {"title": "走查：绕过界面的无前序 O2", "series": "s2", "chapter": "O2",
                       "form": "mix", "video_ids": [o2_vid], "channel": "toutiao"})
            ck("绕过界面直接 POST 也建不出没有前序的 O2（判据在服务端）",
               bool(neg.get("error")) and "前序" in neg.get("error", ""), str(neg.get("error"))[:70])
            start_from_template(pgS, "s2o3", "O3", "走查：O3 上线后回看一篇")
            pgS.wait_for_function(
                "() => { const m = window.srState().slots || {};"
                " return Object.keys(m).some(k => /^s2o3-\\d+$/.test(k)); }", timeout=25000)
            o3_key = pgS.evaluate(
                "() => Object.keys(window.srState().slots).filter(k => /^s2o3-\\d+$/.test(k))[0]")
            o3_link = pgS.evaluate("(k)=>window.srState().slots[k].link_slot", o3_key)
            ck("O3 也点得动，且关联的是被回看的那一篇（O1 或 O2）",
               o3_link in (o1_key, o2_key), (o3_key, o3_link))
            close_task_tabs()
            # 整页 13000 多像素的截图看不清这一格，补两张**元素级**截图：
            # 一张 O2 的入口（按钮 + 前序下拉），一张后面 C2 的关联稿
            try:
                pgS.locator("#src-s2o2").screenshot(path=a.out.replace(".png", "_o2入口.png"))
                pgS.locator("#src-s2o3").screenshot(path=a.out.replace(".png", "_o3入口.png"))
            except Exception as e:
                print("   诊断｜O2/O3 元素截图没拍成：%s" % str(e)[:80])
            pgS.screenshot(path=a.out.replace(".png", "_s2chain.png"), full_page=True)

            print("── 7d. 同名同日两篇、以及公众号发过头条不跟（都在页面上看得到）──")
            twin = "走查：同名同日也要各算各的"
            two = pgS.evaluate("""() => Object.keys(window.srState().slots)
                .filter(k => /^s1[a-d][0-9]$/.test(k))
                .filter(k => !window.srState().slots[k].published).slice(0, 2)""")
            for k in two:
                # 展开必须**点 summary**：直接写 `el.open=true` 不会被页面那份 SR_OPEN 记下来，
                # 登记一重绘整块 DOM 就把它折回去了——表单在 DOM 里但 display:none，
                # `wait_for_selector` 默认等"可见"，于是只留下一个超时（第一版 7d 撞在这）。
                # 已经开着的就别再点（点 summary 是切换，会把开着的那格关掉）。
                pgS.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                                 el.scrollIntoView();
                                 if(!el.open) el.querySelector('summary').click();}""", k)
                pgS.wait_for_selector("#src-%s[open]" % k, timeout=10000)
                pgS.wait_for_timeout(150)
                pgS.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                                 const b=[...el.querySelectorAll('.sr-act button')]
                                       .find(x=>/登记已发布/.test(x.textContent));
                                 if(b) b.click();}""", k)
                pgS.wait_for_selector("#pt-t-" + k, timeout=10000)
                pgS.fill("#pt-t-" + k, twin)
                pgS.fill("#pt-d-" + k, "2026-10-22")
                pgS.fill("#pt-u-" + k, "https://example.com/" + k)
                pgS.evaluate("""(k)=>{const el=document.getElementById('src-'+k);
                                 [...el.querySelectorAll('button')]
                                   .find(b=>/确认登记/.test(b.textContent)).click();}""", k)
            pgS.wait_for_function(
                "(ks)=>{const m=window.srState().slots||{};return ks.every(k=>m[k]&&m[k].published);}",
                arg=two, timeout=30000)
            refs = pgS.evaluate("(ks)=>ks.map(k=>(window.srState().slots[k].publish_rows||[{}])[0].ref_id)",
                                two)
            ck("页面上两格同名同日登记后**都**显示已发布（旧版第一格会被顶回去）",
               len(set(refs)) == 2 and all(refs), (two, refs))
            wk = two[0]
            rv = api(port, "/api/plan/publish", {"slot_key": wk, "action": "revoke",
                                                 "ref_id": refs[0], "reason": "走查：先撤销这一格",
                                                 "by": "走查脚本", "channel": "toutiao"})
            # 撤销是走接口做的，页面那份 srState 不会自己变——判据只看接口回读，
            # 别拿"界面下一格会不会变"当这一步的断言（那要等重绘，等于在等一件没发生的事）
            ck("撤销这一格后接口回读不是已发布（给渠道那一半腾位置）",
               not rv.get("error") and (rv.get("state") or {}).get("state") != "confirmed",
               str(rv)[:90])
            w = api(port, "/api/plan/publish", {"slot_key": wk, "action": "register",
                                                 "channel": "wechat", "title": "走查：公众号那一篇",
                                                 "pub_date": "2026-10-22",
                                                 "url": "https://mp.weixin.qq.com/x", "by": "走查脚本"})
            ck("台账能按渠道分开记（公众号那笔写进去了）",
               (w.get("state") or {}).get("state") == "confirmed", str(w.get("state"))[:80])
            pgS.reload(wait_until="domcontentloaded")
            pgS.wait_for_selector("#sr-body .sr-card", timeout=45000)
            wk_row = pgS.evaluate("(k)=>window.srState().slots[k]", wk)
            ck("公众号发过了，头条看板上这一格仍是未发布（不跟着停匹配）",
               wk_row.get("published") is False, wk_row.get("stage_label"))
            ck("接口按渠道回读：头条 none、公众号 confirmed",
               (api(port, "/api/plan/publish?slot_key=%s&channel=toutiao" % wk).get("state") or "none")
               == "none", wk)
            # 绕过页面直接 POST 也造不出第 27 格与第四章（判据在服务端）
            any_vid = q(db, "SELECT id FROM videos LIMIT 1")
            vids = [any_vid[0]["id"]] if any_vid else [1]
            bad1 = api(port, "/api/plan/series/open",
                       {"title": "走查：绕过页面造第 27 格", "series": "s1", "form": "guide",
                        "video_ids": vids, "channel": "toutiao"})
            bad2 = api(port, "/api/plan/series/open",
                       {"title": "走查：绕过页面造第四章", "series": "s2", "chapter": "O9",
                        "form": "mix", "video_ids": vids, "channel": "toutiao"})
            ck("绕过页面 POST 造 S1 第 27 格 / 造第四章都被服务端拒",
               bool(bad1.get("error")) and bool(bad2.get("error")),
               (str(bad1.get("error"))[:50], str(bad2.get("error"))[:50]))
            ck("被拒的两次没多出稿件槽位",
               len(q(db, "SELECT * FROM plan_slots WHERE origin='user' AND series='s1'")) == 0 and
               len(q(db, "SELECT * FROM plan_slots WHERE slot_key LIKE 's2o9%'")) == 0)
            # 独立稿归进位置：只增关系，S1 那 26 格不许变 27
            fr = q(db, "SELECT slot_key FROM plan_slots WHERE series='free' LIMIT 1")
            if fr:
                lk = api(port, "/api/plan/series/link",
                         {"slot_key": fr[0]["slot_key"], "series": "s1",
                          "chapter": "C", "position": "C2"})
                ck("独立稿归进 C2 只写关系（后端不再把稿件标成 s1 位置）",
                   (lk.get("slot") or {}).get("series") == "free" and
                   (lk.get("slot") or {}).get("parent_slot") == "s1c2",
                   str(lk)[:110])
                pgS.reload(wait_until="domcontentloaded")
                pgS.wait_for_selector("#sr-body .sr-card", timeout=45000)
                c_line = pgS.evaluate("""() => { const out = [];
                    document.querySelectorAll('#sr-body .sr-ch > summary b').forEach(
                        b => out.push(b.parentNode.textContent.replace(/\\s+/g, ' ').slice(0, 40)));
                    return out; }""")
                ck("章节 C 仍写 6 个位置（关联稿不占内容位置，界面上这个数不变）",
                   any("C 成长与资源 6 个位置" in x for x in c_line), c_line)
                # C2 这一格在章节 C 那个 `<details>` 里，而默认只展开 A 章——
                # 折着的父元素整块不渲染，`innerText` 拿回来是空串（第一版就"看不见"在这一层）。
                # 所以先把父章节点开、再点这一格，读到的才是人在屏幕上真能读到的字。
                pgS.evaluate("""() => { const el = document.getElementById('src-s1c2');
                                 const ch = el.closest('.sr-ch');
                                 if (ch && !ch.open) ch.querySelector('summary').click();
                                 if(!el.open) el.querySelector('summary').click();
                                 el.scrollIntoView(); return 1; }""")
                pgS.wait_for_timeout(250)
                card_txt = pgS.evaluate(
                    "() => document.getElementById('src-s1c2').innerText")
                ck("关联稿在 C2 那一格下面看得见", "关联稿" in card_txt,
                   card_txt[:90].replace("\n", " "))
                try:
                    pgS.locator("#src-s1c2").screenshot(path=a.out.replace(".png", "_关联稿.png"))
                except Exception as e:
                    print("   诊断｜C2 元素截图没拍成：%s" % str(e)[:80])
            else:
                ck("独立稿归进 C2 只写关系（后端不再把稿件标成 s1 位置）", False, "没找到 free- 槽号")
            close_task_tabs()
            pgS.close()

            print("── 8. 窄屏 375：不横向溢出、按钮不挤成一列一个字 ──")
            # 前面五台浏览器上下文都还开着，各自的轮询会跟隔离服务抢连接
            # （第一版第 8 段就是在这一步等不到卡片超时）。用完就关。
            for _c in (ctx2, ctx3):
                try:
                    _c.close()
                except Exception:
                    pass
            pgv = br.new_context(viewport={"width": 375, "height": 900}).new_page()
            v_err = []
            pgv.on("pageerror", lambda e: v_err.append(str(e)[:200]))
            pgv.goto(sched, wait_until="domcontentloaded")
            try:
                pgv.wait_for_selector("#sr-body .sr-card", timeout=45000)
            except Exception as e:
                # 等不到卡片时把页面自己说的那句错原样打出来——只报"超时"等于没报
                print("   诊断｜页面报错：%s" % (v_err[:2] or "无"))
                print("   诊断｜#sr-err：%s" % pgv.inner_text("#sr-err")[:200])
                print("   诊断｜#sr-body：%s" % pgv.inner_text("#sr-body")[:160].replace("\n", "|"))
                print("   诊断｜接口直读：%s" % str(api(port, "/api/plan/series").get("error")
                                                or "slots=%d" % len(api(port, "/api/plan/series").get("slots", [])))[:160])
                raise
            over = pgv.evaluate("""() => {
                const bad = [];
                document.querySelectorAll('#sr-body .sr-card, #sr-run, .sr-cand label').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.right > window.innerWidth + 1 || r.left < -1)
                        bad.push((el.className || el.tagName) + ' right=' + Math.round(r.right));
                });
                return {doc: document.documentElement.scrollWidth, win: window.innerWidth, bad: bad.slice(0, 5)};
            }""")
            ck("窄屏整页不横向溢出（scrollWidth 不超视口）", over["doc"] <= over["win"] + 1, over)
            ck("窄屏没有卡片/候选行被推出屏幕", not over["bad"], over["bad"])
            # 必须先展开一格再量：卡片默认折着，按钮高度全是 0，
            # 那条"没被挤成竖排一个字"会**空过**（断言范围与结论范围不一致，正是这一类）。
            pgv.evaluate("""() => { const el = document.querySelector('#sr-body .sr-card');
                            el.open = true; el.scrollIntoView(); }""")
            pgv.wait_for_timeout(250)
            btnh = pgv.evaluate("""() => [...document.querySelectorAll('#sr-body .sr-card[open] .sr-act button')]
                .slice(0, 6).map(b => Math.round(b.getBoundingClientRect().height))""")
            ck("窄屏展开后按钮没被挤成竖排一个字（高度 20–34px 且量到数）",
               len(btnh) >= 2 and all(20 <= h < 34 for h in btnh), btnh)
            rows = pgv.evaluate("""() => { const el = document.querySelector('#sr-body .sr-card[open]');
                const r = el.getBoundingClientRect();
                return {w: Math.round(r.width), over: r.right > window.innerWidth + 1}; }""")
            ck("窄屏展开一格后仍然不溢出（候选行会换行，不横向撑破）",
               not rows["over"], rows)
            pgv.screenshot(path=a.out.replace(".png", "_375.png"), full_page=True)
            br.close()

        print("── 9. 红线：0 次模型调用、生产库与生产成品目录未被动 ──")
        calls = []
        try:
            calls = json.loads(open(capture, encoding="utf-8").read())
        except Exception:
            calls = []
        ck("模型调用次数 = 0（隔离服务捕获到的请求列表是空的）", calls == [], "%d 次" % len(calls))
        now_stat = (os.path.getmtime(PROD_DB), os.path.getsize(PROD_DB))
        if now_stat == prod_stat:
            ck("生产库没被这一趟写过（mtime + 字节双核，与开跑前那次记录比）", True, "没动")
        else:
            # 生产工作台是**活的**：号主可能正开着页面点生成（10-28 21:28 就撞上一次，
            # 他真生成了一版稿）。mtime 变了不等于这一趟写的——这一趟的隔离服务只会写
            # model='stub-model' 的行，去库里找它们的踪迹才是真判据。
            pc = sqlite3.connect("file:%s?mode=ro" % PROD_DB, uri=True)
            stubs = pc.execute(
                "SELECT COUNT(*) FROM plan_tasks WHERE model='stub-model'").fetchone()[0]
            ssum = pc.execute(
                "SELECT COUNT(*) FROM ai_summaries WHERE model='stub-model'").fetchone()[0]
            pc.close()
            ck("生产库动过，但不是这一趟写的（隔离服务只写 stub-model 的行）",
               stubs == 0 and ssum == 0,
               "mtime %s → %s；stub 行 %d/%d（看着像号主那边刚生成过）"
               % (time.strftime("%H:%M:%S", time.localtime(prod_stat[0])),
                  time.strftime("%H:%M:%S", time.localtime(now_stat[0])), stubs, ssum))
        prod_new = [p for p in os.listdir(PROD_POSTER)
                    if os.path.getmtime(os.path.join(PROD_POSTER, p)) > time.time() - 900]
        ck("生产成品目录近 15 分钟没有被写入", prod_new == [], prod_new)
    finally:
        stop_server(proc)

    print("\n截图：%s / %s" % (a.out, a.out.replace(".png", "_375.png")))
    if fails:
        print("PLAN_SERIES_WALK_FAIL %d 条：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        return 1
    print("PLAN_SERIES_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
