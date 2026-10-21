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
3. 凭据齐 → 成品 `publishable=true` 且逐条来源/结论落进临时 manifest；
4. 空凭据 / 闸口失效 → 图照样出，但 `publishable=false`、lint 里有"不可发布"，
   并且**生产 `output/poster/87/manifest.json` 的 mtime 与字节都没变**。

用法：env -u PYTHONHOME -u PYTHONPATH /usr/local/bin/python3 scripts/agent/ui_guide_gate_walk.py
      （产物截图默认落 temp/，可用 --out 改）
"""
import argparse
import hashlib
import json
import os
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
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
            pg.evaluate("(id) => openPosterDialog(id)", SID)
            pg.wait_for_timeout(1500)
            pg.evaluate("(plan) => { pkRenderPlan(plan, {}, 0); }", plan)
            pg.wait_for_timeout(600)
            pg.evaluate("window.confirm = () => true")
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
            ck("逐条凭据表单渲染出来了（每条一个出处框＋一个结论框，再加核验人）",
               n_inputs["src"] == len(hard) and n_inputs["res"] == len(hard) and n_inputs["by"],
               json.dumps(n_inputs))
            old_btn = pg.evaluate("""() => Array.from(document.querySelectorAll('#pk-result button'))
                .map(b => b.textContent.trim())""")
            ck("按钮不再写着「我已人工核实」（没记录就不许出现「已核实」字样）",
               not any("已核实" in b for b in old_btn) and any("凭据" in b for b in old_btn),
               "、".join(old_btn) or "无按钮")
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out)
            # 空凭据直接点：应当仍然出图，但整张标不可发布（这是"缺记录"那条判据）
            pg.evaluate("() => posterGenerate(true)")
            empty_txt = ""
            for _ in range(45):
                time.sleep(1)
                empty_txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "不可发布" in empty_txt or "已逐条留凭据" in empty_txt:
                    break
            ck("空凭据放行 → 界面上写「不可发布」，没写「已核实」",
               "不可发布" in empty_txt and "已逐条留凭据" not in empty_txt,
               line_with(empty_txt, "不可发布"))
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out.replace(".png", "_empty_ev.png"))
            # 再走一遍：这次逐条填上出处与结论
            pg.evaluate("""() => { document.getElementById('pk-result').innerHTML=''; }""")
            pg.evaluate("() => posterGenerate(false)")
            for _ in range(45):
                time.sleep(1)
                if pg.evaluate("() => document.querySelectorAll('.pke-src').length") >= 1:
                    break
            n = pg.evaluate("() => document.querySelectorAll('.pke-src').length")
            for i in range(n):
                pg.fill(".pke-src[data-i='%d']" % i, "本机实测：游戏内拍卖行/官方公告 2026-10-21")
                pg.fill(".pke-res[data-i='%d']" % i, "核实口径：这一档是举例，实际不作承诺")
            pg.fill("#pke-by", "走查脚本")
            pg.evaluate("() => posterGenerate(true)")
            ok_txt = ""
            for _ in range(60):
                time.sleep(1)
                ok_txt = pg.evaluate("() => (document.getElementById('pk-result')||{}).innerText || ''")
                if "已逐条留凭据" in ok_txt:
                    break
            ck("填齐凭据再放行 → 界面写「已逐条留凭据（核验人）」，不再写「不可发布」",
               "已逐条留凭据" in ok_txt and "不可发布" not in ok_txt, line_with(ok_txt, "已逐条留凭据"))
            pg.eval_on_selector("#pk-result", "e=>e.scrollIntoView({block:'center'})")
            pg.wait_for_timeout(300)
            pg.query_selector("#pk-result").screenshot(path=a.out.replace(".png", "_with_ev.png"))
            browser.close()

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
            ck("有凭据的放行才允许 publishable=true", tm.get("publishable") is True,
               str(tm.get("publishable")))
            ck("图真的画出来了（逐页 PNG 落在临时目录）",
               os.path.getsize(os.path.join(srv["poster_out"], str(SID), "poster.png")) > 20000,
               "%d 字节" % os.path.getsize(os.path.join(srv["poster_out"], str(SID), "poster.png")))
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
    print("\n截图：%s / %s / %s" % (a.out, a.out.replace(".png", "_empty_ev.png"),
                                   a.out.replace(".png", "_with_ev.png")))
    print("GUIDE_GATE_WALK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
