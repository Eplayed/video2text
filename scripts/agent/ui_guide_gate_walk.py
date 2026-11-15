# -*- coding: utf-8 -*-
"""事实闸的界面走查：headless Chrome 开真弹窗、点真按钮，**全程只连隔离服务**。

为什么改成隔离服务（2026-10-21 Codex 复核 P1）：这一版原来把 BASE 写死成
`http://127.0.0.1:15801` 并向 `/api/poster/generate` 发真 POST，安全前提是"闸口会拦下"。
可**测的就是闸口本身**——它一旦回归，这条测试就开始覆盖 `output/poster/87`、
甚至发收费请求；只看 manifest 的 mtime 是事后发现，防不住。现在：
· 起 `scripts/agent/poster_test_server.py`（临时库副本 + 临时成品目录 + 模型桩 + 独立端口），
  库路径撞上生产或成品目录落在仓库 `output/` 里 → 那个服务直接拒绝启动；
· `requests` 在 ai_poster 里被换成"一调就抛"的桩：这一趟真花不出钱；
· 反向验证（故意 `--gate off` 让闸口失效）也照样只写临时目录——**这才是这条测试该有的判据**。

四条断言线：
1. 后端拦：87 那份带「46亿」的文案提交即被拦，且回传**逐条**清单（`fact_hard`）；
2. 界面有出口：拦完之后逐条凭据表单真的渲染出来、能填、点得动；
   空凭据点「带这些凭据出图」→ 前端**逐格点名**（第N条没填出处/结论/核验人），
   **不发起任务**（2026-10-30 判据，旧版"空凭据照样出图"已废）；
3. 凭据齐 → **事实闸这一层**放行（`fact_gate.override.complete=true`）并逐条落进临时 manifest。
   注意凭据桩的写法有硬判据（2026-11-02 加严）：出处必须可点开、非通用列表页、
   与登记表来源对得上、带日期——纯文字「本机实测」那类过不了 `evidence_ok`，
   本脚本用一支反面（纯文字桩→任务照跑但整张不可发布）钉住这条加严在线。
   凭据桩只测字段契约（表单收不收、传不传、落不落 manifest），不代表真有人核过。
   且只填事实凭据≠能发：publishable 由行动依据等 error 档硬伤兜底压 False
   （编辑证据链缺项 10-23 起只是 warn，不再翻 publishable——断言别拿它当拦截理由）。
3b. 第二支夹具（干净文案 + 证据链补齐）：11-02 起 compose_source_note 会把台账
   未结风险的预览拼进 source_note，系统自己拼的披露文字也照常被事实闸扫描——
   「干净文案」一样会被拦，正路是凭据放行；补齐 provenance + 逐条凭据 → publishable=true。
4. 反向：故意让闸口失效 → 图照样出，但 `publishable=false`、lint 里有"不可发布"，
   并且**生产 `output/poster/87/manifest.json` 的 mtime 与字节都没变**。

本机网络注意：macOS 系统代理开着时 urllib 会拿它去连 127.0.0.1（proxy_bypass
返回 False），代理对回环地址回 502。本脚本 api() 显式禁代理直连；隔离服务
READY 打印与真正 listen 之间有时差，start_server 会等端口能连上再返回。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/agent/ui_guide_gate_walk.py
     （产物截图默认落 temp/，可用 --out 改）
"""
import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER = os.path.join(ROOT, "scripts", "agent", "poster_test_server.py")
PROD_MANIFEST = os.path.join(ROOT, "output", "poster", "87", "manifest.json")
SID = 87
NEEDLE = "46亿"
fails = []

# 凭据桩两种（2026-11-02 加严判据后，第二种才过 evidence_ok）：
# 纯文字出处＝反面，验证加严真的在线；可点开链接＋日期＝正面，验证字段契约走通。
# 正面桩的链接取 wow_claims.json meta.source_url 同源——evidence_ok 会对上登记来源。
TEXT_SRC = "本机实测：游戏内拍卖行/官方公告 2026-10-21"
TEXT_RES = "核实口径：这一档是举例，实际不作承诺"
STUB_SRC = "https://us.forums.blizzard.com/en/wow/t/wow-forever-beta-development-notes/2360696 走查桩（字段契约测试）"
STUB_RES = "走查桩结论：字段契约测试，不作承诺（2026-10-22）"

# 本机系统代理会劫持 urllib 对 127.0.0.1 的请求（proxy_bypass 返回 False，代理回 502）。
# 显式禁代理：环境变量清不掉 macOS 系统代理这一层。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def start_server(gate):
    """拉起隔离服务，从它的 READY 行里拿临时路径（不自己猜目录）。"""
    port = free_port()
    log = os.path.join(ROOT, "temp", "poster_srv_%s.log" % gate)
    p = subprocess.Popen([sys.executable, SERVER, "--port", str(port), "--gate", gate],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=ROOT, bufsize=1, universal_newlines=True)
    ready = ""
    t0 = time.time()
    while time.time() - t0 < 40:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                break
            continue
        print("       server| " + line.strip())
        if line.startswith("READY"):
            ready = line.strip()
            break
    if not ready:
        p.kill()
        raise RuntimeError("隔离服务没起来（%s）" % gate)
    parts = ready.split()
    fields = dict(kv.split("=", 1) for kv in parts[1:] if "=" in kv)
    fields["port"] = port
    fields["log"] = log
    # READY 是 print 出来的，真正 listen 在 app.run() 里——差着几毫秒到几百毫秒，
    # 紧跟着的 api() 会撞 connection refused（11-03 实测）。等端口真能连上再返回。
    ok = False
    t1 = time.time()
    while time.time() - t1 < 15:
        if p.poll() is not None:
            break
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            ok = True
            break
        except OSError:
            time.sleep(0.2)
    if not ok:
        p.kill()
        try:
            extra = (p.stdout.read() or "")[-400:]
        except Exception:
            extra = ""
        raise RuntimeError("隔离服务 READY 后 15 秒仍连不上（%s）%s"
                           % (gate, ("｜服务端输出：" + extra) if extra else ""))
    with open(log, "w", encoding="utf-8") as f:
        f.write(ready + "\n")
    return p, fields


def stop_server(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def api(port, path, body=None, timeout=120):
    url = "http://127.0.0.1:%d%s" % (port, path)
    req = urllib.request.Request(url,
                                 data=(json.dumps(body).encode("utf-8") if body is not None else None),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_done(port, want_error=True, tries=60):
    """轮询到任务结束。注意：click 与查结果不能挤在一个请求里，Vue/后台都是异步的。"""
    st = {}
    for _ in range(tries):
        time.sleep(1)
        st = api(port, "/api/poster/status")
        if st.get("running"):
            continue
        if st.get("error") or not want_error:
            return st
        if st.get("package"):
            return st
    return st


def line_with(text, needle):
    """从一格里取出真正命中那句话——只打第一行会打出图注/页头，看着像断言没在判这件事。"""
    for ln in str(text or "").split("\n"):
        if needle in ln:
            return ln.strip()[:90]
    return str(text or "").split("\n")[0][:90]


def sha(path):
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/guide_gate_walk.png")
    a = ap.parse_args()
    if not os.path.isfile(PROD_MANIFEST):
        print("夹具不在：%s" % PROD_MANIFEST)
        return 2
    before_mtime = os.path.getmtime(PROD_MANIFEST)
    before_sha = sha(PROD_MANIFEST)
    m = json.load(open(PROD_MANIFEST, encoding="utf-8"))
    plan = m.get("plan") or {}
    pub = m.get("publish") or {}
    if NEEDLE not in json.dumps(plan, ensure_ascii=False):
        print("夹具本身不含「%s」，这一轮断言没有意义（成品文案变了？）" % NEEDLE)
        return 2

    print("── 起隔离服务（闸口开）──")
    proc, srv = start_server("on")
    port = srv["port"]
    try:
        print("   临时库=%s\n   临时成品目录=%s" % (srv["db"], srv["poster_out"]))
        ck("服务确实没在用生产库/生产成品目录",
           srv["db"] != os.path.join(ROOT, "output", "video2text.db")
           and str(ROOT) + "/output" not in srv["poster_out"], srv["poster_out"])
        _ = api(port, "/api/poster/status")

        print("── 1. 后端：提交 87 的成品文案必须被拦，并回传逐条清单 ──")
        api(port, "/api/poster/generate", {"summary_id": SID, "plan": plan, "publish": pub,
                                          "text_mode": m.get("text_mode") or "typeset",
                                          "review": False, "copies": 1})
        st = wait_done(port, want_error=True)
        err = str(st.get("error") or "")
        ck("后端按事实闸拦下", "待核事实进了成品" in err, err[:90])
        ck("拦下的原因点名「%s」" % NEEDLE, NEEDLE in err, err[:60])
        hard = st.get("fact_hard") or []
        ck("回传了逐条清单（前端按条开输入框，不是一句总括）", len(hard) >= 1,
           "%d 条" % len(hard))

        print("── 2. 界面：拦完之后逐条凭据表单要真的能填、能点 ──")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            pg = browser.new_page(viewport={"width": 1500, "height": 1000})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % port, wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            # 这条隔离夹具只提交了 generate，没跑收费的「① 拆文案」，因此服务端
            # status 本来没有 plan。给本次 status 回包补上已保存的 87 底稿，才能
            # 真正验证 pkHydrate 在「错误 + 有版面」时不会吞掉编辑表单。
            # 只拦重开弹窗的这一次 GET，后续请求仍走隔离服务。
            seeded = api(port, "/api/poster/status?summary_id=%d" % SID)
            seeded["plan"] = {"plan": plan, "publish": pub}
            status_url = "**/api/poster/status?summary_id=%d" % SID
            pg.route(status_url, lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(seeded, ensure_ascii=False)))
            pg.evaluate("(id) => openPosterDialog(id)", SID)
            pg.wait_for_timeout(1500)
            pg.unroute(status_url)
            # 重开弹窗不吞版面（2026-11-03 修复）：上一次任务失败时 pkHydrate 不再提前
            # return——错误出口（逐条凭据表单）与版面编辑表单必须同时都在。
            # 必须在手动 pkRenderPlan 之前断言，否则分不出是 pkHydrate 恢复的还是脚本渲染的。
            hyd = pg.evaluate("""() => ({
                hasForm: !!document.getElementById('pk-title'),
                fail: ((document.getElementById('pk-result')||{}).innerText || '').indexOf('待核事实') >= 0,
                inputs: document.querySelectorAll('.pke-src').length})""")
            ck("重开弹窗：错误出口与版面同时恢复（pkHydrate 不再提前 return）",
               hyd["hasForm"] and hyd["fail"] and hyd["inputs"] >= 1,
               "版面表单=%s 错误出口=%s 凭据格=%d" % (hyd["hasForm"], hyd["fail"], hyd["inputs"]))
            pg.evaluate("(plan) => { pkRenderPlan(plan, {}, 0); }", plan)
            pg.wait_for_timeout(600)
            title_edit = pg.evaluate("""() => {
              const el = document.getElementById('pk-title-tt-0');
              if (!el) return false;
              const old = el.value;
              el.value = '人工改过的发布标题';
              const ok = pkReadPublish().tt_titles[0] === el.value;
              el.value = old;
              return ok;
            }""")
            ck("发布标题可以直接编辑并进入当前出包文案，不必重拆", title_edit)
            jump = pg.evaluate("""() => {
              pkJumpInput('pk-title-xhs-0');
              const titleOk = _pkChannel === 'xhs' && document.activeElement.id === 'pk-title-xhs-0';
              pkSetChannel('toutiao');
              pkJumpInput('pk-copy', 'xhs');
              const copyOk = _pkChannel === 'xhs' && document.activeElement.id === 'pk-copy';
              pkSetChannel('toutiao');
              return titleOk && copyOk;
            }""")
            ck("跨渠道的标题和配文错误按钮能切档定位到编辑框", jump)
            page_jump = pg.evaluate("""async () => {
              if (_pkPages.length < 2) return false;
              const before = document.getElementById('pk-result').innerHTML;
              pkJumpPageInput(2, 'pk-title');
              await new Promise(r => setTimeout(r, 450));
              const ok = _pkCur === 1 && document.activeElement.id === 'pk-title'
                && document.getElementById('pk-result').innerHTML === before;
              pkSwitchEditPage(0);
              return ok && document.getElementById('pk-result').innerHTML === before;
            }""")
            ck("第二页错误可定位且不覆盖逐条凭据面板", page_jump)
            pg.evaluate("window.confirm = () => true")
            # 空凭据的点名走 alert：拦下来看内容，别让它自动 dismiss 后什么也没留
            pg.evaluate("window.__walkAlerts = []; window.alert = function(m){ window.__walkAlerts.push(String(m)); }")
            pg.evaluate("() => posterGenerate(false)")
            txt = ""
            for _ in range(45):
                time.sleep(1)
                txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "待核事实" in txt:
                    break
            ck("界面上看得见拦下的提示（不是「还在转圈」）", "待核事实进了成品" in txt,
               line_with(txt, "待核事实进了成品"))
            n_inputs = pg.evaluate("""() => ({
                src: document.querySelectorAll('.pke-src').length,
                res: document.querySelectorAll('.pke-res').length,
                by: !!document.getElementById('pke-by')})""")
            # 表单行数必须与**本次 UI 提交**的拦截条数自洽：界面回读表单会归一化文案，
            # 与第 1 段 API 直交的原始 plan 拦截条数可能不同（实测 9 vs 14），
            # 旧断言拿两段不同提交的结果互比，天生会误报。
            m_hard = re.search(r"有\s*(\d+)\s*处待核事实", txt)
            n_msg = int(m_hard.group(1)) if m_hard else -1
            ck("逐条凭据表单渲染出来了（每条一个出处框＋一个结论框，再加核验人）",
               n_msg >= 1 and n_inputs["src"] == n_msg and n_inputs["res"] == n_msg and n_inputs["by"],
               "界面报 %s 条、出处/结论格各 %d/%d、核验人框 %s"
               % (n_msg, n_inputs["src"], n_inputs["res"], bool(n_inputs["by"])))
            old_btn = pg.evaluate("""() => Array.from(document.querySelectorAll('#pk-result button'))
                .map(b => b.textContent.trim())""")
            ck("按钮不再写着「我已人工核实」（没记录就不许出现「已核实」字样）",
               not any("已核实" in b for b in old_btn) and any("凭据" in b for b in old_btn),
               "、".join(old_btn) or "无按钮")
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out)

            # 空凭据直接点（10-30 判据）：前端逐格点名、**不发起任务**——
            # 旧版"空凭据照样出图、标不可发布"已废，这里不许再等那个结果。
            pg.evaluate("() => posterGenerate(true)")
            alerts = []
            for _ in range(10):
                time.sleep(0.5)
                alerts = pg.evaluate("() => window.__walkAlerts || []")
                if alerts:
                    break
            ck("空凭据点「带这些凭据出图」→ 当场点名缺格（出处/结论/核验人）",
               bool(alerts) and "凭据没填全" in alerts[-1]
               and "没填出处" in alerts[-1] and "没填核验人" in alerts[-1],
               (alerts[-1].replace("\n", " ")[:90] if alerts else "没有 alert（点了可能空跑）"))
            still_form = pg.evaluate("""() => ({
                txt: (document.getElementById('pk-result')||{}).innerText || '',
                n: document.querySelectorAll('.pke-src').length})""")
            ck("空凭据没有发起任务：凭据表单原样还在，没有「任务已提交」的空跑",
               "待核事实" in still_form["txt"] and still_form["n"] == n_msg
               and "任务已提交" not in still_form["txt"], "表单 %d 行" % still_form["n"])
            pg.query_selector("#pk-result").screenshot(path=a.out.replace(".png", "_empty_ev.png"))

            # 反面：纯文字出处（旧式桩）填满了三格也过不了 11-02 加严——
            # 任务照跑（前端只验非空），但整张不可发布。钉住"加严判据真的接在放行链上"。
            for i in range(n_msg):
                pg.fill(".pke-src[data-i='%d']" % i, TEXT_SRC)
                pg.fill(".pke-res[data-i='%d']" % i, TEXT_RES)
            pg.fill("#pke-by", "走查脚本")
            pg.evaluate("() => posterGenerate(true)")
            loose_txt = ""
            for _ in range(60):
                time.sleep(1)
                loose_txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "不可发布" in loose_txt or "已逐条留凭据" in loose_txt:
                    break
            ck("纯文字出处 → 任务照跑但整张标不可发布（11-02 加严在线，不放没依据的闸）",
               "不可发布" in loose_txt and "已逐条留凭据" not in loose_txt,
               line_with(loose_txt, "不可发布"))
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out.replace(".png", "_loose_ev.png"))

            # 正面：可点开链接＋日期的凭据桩 → 事实闸这一层放行
            pg.evaluate("() => { document.getElementById('pk-result').innerHTML=''; }")
            pg.evaluate("() => posterGenerate(false)")
            for _ in range(45):
                time.sleep(1)
                if pg.evaluate("() => document.querySelectorAll('.pke-src').length") >= 1:
                    break
            n = pg.evaluate("() => document.querySelectorAll('.pke-src').length")
            for i in range(n):
                pg.fill(".pke-src[data-i='%d']" % i, STUB_SRC)
                pg.fill(".pke-res[data-i='%d']" % i, STUB_RES)
            pg.fill("#pke-by", "走查脚本")
            pg.evaluate("() => posterGenerate(true)")
            ok_txt = ""
            for _ in range(60):
                time.sleep(1)
                ok_txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "已逐条留凭据" in ok_txt or "不可发布" in ok_txt or "不能出图" in ok_txt:
                    break
            ck("格式满足入口规则的测试凭据 → 界面记录 complete（不代表来源内容真被核过）",
               "已逐条留凭据" in ok_txt, line_with(ok_txt, "已逐条留凭据"))
            # 界面那处新增（actBlockHtml）要有渲染证据，不能只算"构建通过"：
            # 结果区里那句「行动依据：N 条能照做｜M 条待核」是后端 manifest.action_gate 原样带过来的，
            # 前端不自己比字符串——所以这一条同时验了"接线通了"和"没在前端抄一份判据"。
            ck("行动依据那块真的渲染在结果区（不是只落在 manifest 里）",
               "行动依据：" in ok_txt and "待核" in ok_txt, line_with(ok_txt, "行动依据："))
            # 只填事实凭据**不等于**这张能发：行动依据这类 error 档硬伤照旧压 publishable。
            # 结果区那一句由进度末尾的 ⛔、证据链那一格、行动依据那一格三处来源拼——
            # 任一处说了"还不行"就算界面尽到了告知义务（断在进度那半句上会因时序飘）。
            ck("但整张仍被标为不行：行动依据这类硬伤还挡着（不为旧断言绕闸）",
               "不可发布" in ok_txt,
               line_with(ok_txt, "不可发布") or line_with(ok_txt, "行动依据"))
            ck("缺项说的是别的原因，不是「事实没凭据」这一条",
               ("编辑证据链" in ok_txt) or ("行动依据" in ok_txt) or ("证据链缺" in ok_txt),
               "见截图")
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out.replace(".png", "_with_ev.png"))
            browser.close()
            ck("页面没有未处理的 JS 错误", not errs,
               "%d 条%s" % (len(errs), ("：" + errs[0][:90]) if errs else ""))

        print("── 3. 落盘：临时成品目录里的 manifest 要带上逐条凭据 ──")
        tmp_mf = os.path.join(srv["poster_out"], str(SID), "manifest.json")
        ck("成品只写到临时目录（生产 output/poster 没被碰）", os.path.isfile(tmp_mf), tmp_mf)
        if os.path.isfile(tmp_mf):
            tm = json.load(open(tmp_mf, encoding="utf-8"))
            fg = tm.get("fact_gate") or {}
            ov = fg.get("override") or {}
            ck("manifest 里逐条凭据齐了（complete）", ov.get("complete") is True,
               "missing=%d" % len(ov.get("missing") or []))
            ck("每条都有出处与结论、核验人留名",
               all(r.get("ok") for r in (ov.get("rows") or [])) and bool(ov.get("checked_by")),
               json.dumps((ov.get("rows") or [{}])[0], ensure_ascii=False)[:110])
            ck("但 publishable=false——行动依据这类 error 档硬伤还挡着（真会发错的那道不松）",
               tm.get("publishable") is False, str(tm.get("publishable")))
            ck("lint 里点名了编辑证据链缺项（当前为 warn，不冒充发布硬拦）",
               any("编辑证据链" in x for x in (tm.get("lint") or [])),
               str([x for x in (tm.get("lint") or []) if "证据链" in x][:1])[:110])
            ck("图真的画出来了（逐页 PNG 落在临时目录）",
               os.path.getsize(os.path.join(srv["poster_out"], str(SID), "poster.png")) > 20000,
               "%d 字节" % os.path.getsize(os.path.join(srv["poster_out"], str(SID), "poster.png")))
        print("── 3b. 第二支夹具：证据链补齐 + 逐条凭据放行 → 才允许 publishable=true ──")
        # 这一支的存在理由：不能只测"缺门时挡"，也要测"门都过时真的放行"，
        # 否则闸口写成一律拦也照样绿。11-02 之后「干净文案」这条路已经不存在——
        # compose_source_note 会把台账未结风险的预览拼进 source_note（系统自己的披露文字），
        # 其中的机制数值照常被事实闸拦下，所以正路是凭据放行：先无 force 拿逐条清单，
        # 再带凭据（可点开链接＋日期）重提。plan 是人改过的文案（不含被点名的数值与专名），
        # 证据链走 /api/plan/provenance 写进**临时库**——生产库一个字节不动。
        clean_plan = {"title": "会不会删档：测试期该怎么决定", "subtitle": "先想清楚再花钱",
                      "style_key": "guide_night", "footer": "内容整理自公开分享，以游戏内为准。",
                      "pages": [{"kicker": "无限服 · 决策", "title": "会不会删档：测试期该怎么决定",
                                 "layout_key": "guide", "style_key": "guide_night",
                                 "cards": [{"t": "先看官方怎么写删档", "d": "公告里写没写清，比谁说的都重要。"},
                                           {"t": "花钱前想一件事", "d": "这轮能不能带进正式服，答案没出就先别买。"},
                                           {"t": "拿不准就等", "d": "等官方补口径不丢人，钱花了才被动。"}]}]}
        clean_pub = {"copy_text": "这轮测试到底删不删档，官方口径还没写全；先别急着花钱，"
                                  "等口径明确再决定也不迟。以游戏内实际为准。",
                     "digest": "删档口径没定，先别花钱。", "source_note": "整理自公开公告与一条视频。",
                     "tt_titles": ["会不会删档？先别急着花钱", "测试期这钱要不要出"]}
        api(port, "/api/plan/provenance",
            {"slot_key": "summary:%d" % SID, "channel": "toutiao", "form": "guide", "version": 1,
             "summary_id": SID,
             "question": "这轮测试会不会删档，388 元该不该现在花",
             "contribution": "把官方公告与一条口播的口径并列出来，标清哪句有出处、哪句只是博主说法",
             "contribution_evidence": "官方公告页 + 本条素材转写（走查桩，字段契约测试）",
             "claims": [{"text": "官方公告未写清是否删档", "evidence": "官方公告页",
                         "evidence_kind": "官方一手页面", "verifier": "走查脚本",
                         "vdate": "2026-10-22", "status": "已核"}],
             "materials": [{"url": "https://wow.blizzard.cn/news/", "author": "暴雪官方",
                            "usage": "事实来源", "rights_basis": "官方公告转述，标来源与日期",
                            "review": "可发布"},
                           {"ref": "self:walk", "author": "本站", "usage": "我们自己做的",
                            "rights_basis": "版面与说明图是我们自己排的", "review": "可发布"}]})
        gen_body = {"summary_id": SID, "plan": clean_plan, "publish": clean_pub,
                    "text_mode": "typeset", "review": False, "copies": 1, "form": "guide"}
        api(port, "/api/poster/generate", gen_body)
        st3 = wait_done(port)
        hard3 = st3.get("fact_hard") or []
        if hard3:
            # 台账披露带进来的待核条目：逐条凭据放行（与界面那条路同源）
            gen_body.update({"force": True, "fact_checked_by": "走查脚本",
                             "fact_evidence": [{"item": str(h), "source": STUB_SRC, "result": STUB_RES}
                                               for h in hard3]})
            api(port, "/api/poster/generate", gen_body)
            st3 = wait_done(port)
        pkg3 = st3.get("package") or {}
        tm3 = {}
        mf3 = os.path.join(srv["poster_out"], str(SID), "manifest.json")
        if os.path.isfile(mf3):
            tm3 = json.load(open(mf3, encoding="utf-8"))
        ck("证据链补齐 + 逐条凭据放行 → publishable=true（闸口不是一律拦）",
           not st3.get("error") and pkg3.get("publishable") is True and tm3.get("publishable") is True,
           "pkg=%s manifest=%s 缺项=%s error=%s" % (
               pkg3.get("publishable"), tm3.get("publishable"),
               str([g.get("field") for g in ((tm3.get("provenance") or {}).get("gaps") or [])])[:60],
               str(st3.get("error") or "")[:60]))
        ck("行动依据这一层在干净文案下不报越界",
           not ((tm3.get("action_gate") or {}).get("overflow") or []),
           str(((tm3.get("action_gate") or {}).get("overflow") or [""])[0])[:90])
        ck("内部待核清单照样带着（稿子里没落地的条目不因为这张能发就消失）",
           bool(((tm3.get("action_gate") or {}).get("stats") or {}).get("pending", 0)) or
           "action_gate" in tm3, str(tm3.get("action_gate", {}).get("stats")))

    finally:
        stop_server(proc)

    print("── 4. 反向：故意让闸口失效，验证「闸坏了也写不到生产」──")
    proc2, srv2 = start_server("off")
    try:
        api(srv2["port"], "/api/poster/generate", {"summary_id": SID, "plan": plan, "publish": pub,
                                                  "text_mode": m.get("text_mode") or "typeset",
                                                  "review": False, "copies": 1})
        st2 = wait_done(srv2["port"], want_error=False)
        pkg = st2.get("package") or {}
        ck("闸口失效时这一趟确实出了图（说明原来那条「安全前提」并不成立）",
           bool(pkg.get("id")) and not st2.get("error"),
           str(st2.get("error") or "")[:80] or "id=%s" % pkg.get("id"))
        alt = os.path.join(srv2["poster_out"], str(SID), "manifest.json")
        ck("失效版本写的也是临时目录，不是生产 output/poster", os.path.isfile(alt), alt)
    finally:
        stop_server(proc2)

    after_mtime = os.path.getmtime(PROD_MANIFEST)
    after_sha = sha(PROD_MANIFEST)
    ck("生产 87 的 manifest 字节没变（mtime + sha256 双核）",
       after_mtime == before_mtime and after_sha == before_sha,
       "mtime %s→%s sha %s→%s" % (int(before_mtime), int(after_mtime), before_sha[:8], after_sha[:8]))
    prod_files = [f for f in os.listdir(os.path.join(ROOT, "output", "poster")) if f == "99999"]
    ck("没在生产成品目录里留下测试目录", not prod_files, str(prod_files))
    if fails:
        print("\n发现 %d 处问题：\n  - %s" % (len(fails), "\n  - ".join(fails)))
        print("截图：%s" % a.out)
        return 1
    print("\n截图：%s / %s / %s / %s" % (a.out, a.out.replace(".png", "_empty_ev.png"),
                                       a.out.replace(".png", "_loose_ev.png"),
                                       a.out.replace(".png", "_with_ev.png")))
    print("GUIDE_GATE_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
