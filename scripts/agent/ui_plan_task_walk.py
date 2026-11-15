# -*- coding: utf-8 -*-
"""界面走查：排产表 → 采用素材 → 任务面板 → 整理中 / 失败 / 中断恢复 / 窄屏。

**全程连隔离服务，不碰 15801、不碰真实库**（Codex 第一单验收 R4 点名：
上一版这个脚本直连生产库与 15801，还会按 slot_key='setup' DELETE 真实选题）。
隔离方式：
  · 另起端口，DB 是 `output/video2text.db` 的一份临时副本；
  · 模型在 **openai 客户端边界**被打桩（`scripts/agent/plan_test_server.py`）——
    提示词仍由 `content_store._prompt()` 真构造，只是不发请求；
  · 造一条"没正文"的测试素材只写在这份临时库里，用来拍恢复入口；
  · 退出时（含异常）杀子进程、删临时目录，并断言真实库与真实服务没被动过。

桩在哪、真实在哪（交接要照抄这段）：
  真实：浏览器、页面、iframe 通信、Flask 路由、SQLite 读写、任务状态机、提示词构造；
  桩：模型返回内容与耗时（延迟用来拍「整理中」，抛异常用来拍「失败」）。
用法：python3 scripts/agent/ui_plan_task_walk.py
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "temp" / "plan-evidence-20261020"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROD_DB = str(ROOT / "output" / "video2text.db")
SLOT = "setup"
errs, fails = [], []


def ck(name, cond, detail=""):
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         (" — " + str(detail)) if detail else ""))
    if not cond:
        fails.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(db, port, delay=0.0, mode="ok", capture=None):
    cmd = [sys.executable, str(ROOT / "scripts" / "agent" / "plan_test_server.py"),
           "--db", db, "--port", str(port), "--delay", str(delay), "--mode", mode]
    if capture:
        cmd += ["--capture", capture]
    env = dict(os.environ, PYTHONHOME="", PYTHONPATH="")
    p = subprocess.Popen(cmd, cwd=str(ROOT / "web"), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
    deadline = time.time() + 40
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        if line.startswith("READY"):
            wait_port(port)
            READY_LINE.append(line.strip())
            ALIVE.append((p, port))
            return p
        if "REFUSED" in line:
            p.kill()
            raise RuntimeError(line.strip())
    p.kill()
    raise RuntimeError("隔离服务没起来")


READY_LINE = []      # 每台服务的 READY 行里带着它实际用的库路径，收尾拿来直证"没连生产库"
ALIVE = []           # (进程, 端口)：还活着的测试服务，测"重启"之前要先把它们全停掉


def stop_port_free(port, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            return True
        time.sleep(0.2)
    return False


def stop_all_servers():
    """**先停旧服务、确认端口释放，再启新服务**——这才叫重启。
    两边同时活着只能证明"另一个进程起来时会核对"，不能拿来宣称验证了单进程重启
    （Codex 第二轮点名这条措辞）。"""
    n = len(ALIVE)
    for p, port in ALIVE:
        try:
            os.killpg(os.getpgid(p.pid), 15)
        except Exception:
            p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()
        if not stop_port_free(port):
            raise RuntimeError("端口 %s 还没释放，这种状态不能算重启" % port)
    del ALIVE[:]
    return n


def wait_port(port, timeout=25.0):
    """READY 那行打印出来时 Flask 可能还没真 bind 上，先探一次 TCP 再往下走。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
            return True
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("端口 %s 没在监听" % port)


def api(port, path, body=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def shot(pg, name, sel=None, fr=None):
    path = str(OUT / ("plan_%s.png" % name))
    el = (fr or pg).query_selector(sel) if sel else pg
    if not el:
        print("   ! 找不到 %s，%s 没拍成" % (sel, name))
        return None
    if el is not pg:
        box = el.bounding_box()
        if not box or box["width"] < 2 or box["height"] < 2:
            print("   ! %s 是 0 尺寸，%s 不拍" % (sel, name))
            return None
    el.screenshot(path=path)
    print("   截图 plan_%s.png" % name)
    return path


def video_title(db, vid):
    con = sqlite3.connect(db)
    row = con.execute("SELECT title FROM videos WHERE id=?", (vid,)).fetchone()
    con.close()
    return (row[0] if row else "") or ""


def pick_extra_video(db, exclude):
    """临时库里再找一条有正文、且不在 exclude 里的素材，用来制造"采用清单变了"。"""
    con = sqlite3.connect(db)
    q = ("SELECT id FROM videos WHERE length(transcript)>300 AND id NOT IN (%s)"
         % ",".join(str(int(x)) for x in (exclude or [0])))
    row = con.execute(q).fetchone()
    con.close()
    return int(row[0])


def frame_of(pg):
    for f in pg.frames:
        if "schedule.html" in (f.url or ""):
            return f
    return None


def main():
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="plan_walk_")
    db = os.path.join(tmp, "t.db")
    shutil_copy(PROD_DB, db)
    # 造一条"没正文"的测试素材（只存在于这份临时库）——用来拍恢复入口，不动真实库
    con = sqlite3.connect(db)
    con.execute("INSERT OR REPLACE INTO videos (id,source_sheet,source_row,title,author,transcript,"
                "published_at,updated_at,status) VALUES (999120,'E2E',99001,'测试：这条没有正文',"
                "'测试号','','2026-10-18','2026-10-18 10:00:00','未开始')")
    for vid, title, body in ((999121, "测试：这条正文会被抹掉", "正文正文正文正文正文正文正文正文"),
                             (999122, "测试：整理中与失败两格用这条", "正文正文正文正文正文正文正文正文")):
        con.execute("INSERT OR REPLACE INTO videos (id,source_sheet,source_row,title,author,"
                    "transcript,published_at,updated_at,status)"
                    " VALUES (?,'E2E',?,?, '测试号',?,'2026-10-18',"
                    "'2026-10-18 10:00:00','已完成')",
                    (vid, 99000 + vid - 999000, title, body))
    con.commit(); con.close()
    prod_stat = os.stat(PROD_DB)
    srvs = []
    port = None
    try:
        port = free_port()
        srvs.append(start_server(db, port, delay=0.0, capture=os.path.join(tmp, "prompts.json")))
        B = "http://127.0.0.1:%d" % port
        print("隔离服务：%s（库 %s）" % (B, db))

        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=CHROME, headless=True)
            pg = br.new_page(viewport={"width": 1280, "height": 1700})
            pg.on("pageerror", lambda e: errs.append("pageerror: " + e))
            pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
            pg.goto(B + "/#schedule", wait_until="load")
            pg.wait_for_timeout(6000)
            fr = frame_of(pg)
            ck("隔离服务上 iframe 排产页加载出来", bool(fr))
            if not fr:
                return 1
            fr.wait_for_selector(".card[data-item='%s']" % SLOT, timeout=15000)
            pg.wait_for_timeout(3000)
            card = ".card[data-item='%s']" % card_sel_id()

            print("\n== 1 采用一条素材 ==")
            adopt_sel = "%s .ref [data-act='adopt']" % card
            fr.click(adopt_sel)
            pg.wait_for_timeout(2000)
            ck("按钮变已采用", fr.text_content(adopt_sel) == "已采用", fr.text_content(adopt_sel))

            print("\n== 2 点主入口 → 面板 → 生成（桩延迟 0，跑完即 done）==")
            fr.click("%s [data-act='task']" % card)
            pg.wait_for_timeout(1500)
            ck("面板打开", bool(pg.query_selector("#pt-overlay.show")))
            g = pg.query_selector("#pt-overlay [onclick^='ptGenerate(false)']")
            ck("没稿时主按钮是「生成这一版」", bool(g))
            g.click()
            pg.wait_for_timeout(4000)
            st = pg.evaluate("() => (_pt.task||{}).status")
            ck("跑完状态是 done", st == "done", st)
            ck("面板出现「继续处理这篇稿」",
               "继续处理这篇稿" in pg.eval_on_selector("#pt-acts", "e => e.innerText"))
            shot(pg, "08_panel_done", "#pt-overlay .modal-box")
            pg.evaluate("() => ptClose()")

            print("\n== 3 R3：选题要求真进了模型提示词（在 openai 客户端边界截获）==")
            cap = os.path.join(tmp, "prompts.json")
            prompts = []
            for _ in range(12):          # 桩那边每秒落一次盘，等一下别读到空文件
                if os.path.exists(cap):
                    try:
                        prompts = json.load(open(cap))
                    except Exception:
                        prompts = []
                    if prompts:
                        break
                time.sleep(0.5)
            title = fr.evaluate("() => itemById('%s').title" % SLOT)
            hook = fr.evaluate("() => itemById('%s').hook" % SLOT)
            last = prompts[-1]["prompt"] if prompts else ""
            ck("桩收到了一次真实请求", len(prompts) >= 1, "%d 次" % len(prompts))
            ck("提示词里带上了这一题的标题", title[:12] in last, title[:24])
            ck("提示词里带上了「必须有的那一格」(hook)", hook[:10] in last, hook[:24])
            ck("提示词写明要求只用于取舍与角度、不许当事实来源", "不是事实来源" in last)

            print("\n== 4 R2：三版本并存时，卡片与面板都落最新版 ==")
            before = api(port, "/api/plan/tasks?slot_key=" + SLOT)[1]["tasks"]
            stored = list(before[0]["video_ids"]) if before else []
            extra = pick_extra_video(db, stored)          # 另找一条有正文的素材，保证清单真的变了
            code, j = api(port, "/api/plan/task", {"slot_key": SLOT, "channel": "toutiao",
                                                   "form": "guide", "video_ids": stored + [extra]})
            vers = [t["version"] for t in api(port, "/api/plan/tasks?slot_key=" + SLOT)[1]["tasks"]]
            ck("已开工版改采用清单 → 开新版本而不是覆盖旧行（R1）",
               2 in vers and len(vers) >= 2, vers)
            pg.reload(wait_until="load")
            pg.wait_for_timeout(6000)
            fr = frame_of(pg)
            fr.wait_for_selector(".card[data-item='%s']" % SLOT, timeout=15000)
            pg.wait_for_timeout(3000)
            picked = fr.evaluate("""() => { const t=taskFor('%s','toutiao','guide');
                return t ? {v:t.version, ids:t.video_ids} : null; }""" % SLOT)
            ck("卡片挑中的是 version 最大的那条", picked and picked["v"] == max(vers), picked)
            fr.click("%s [data-act='task']" % card)
            pg.wait_for_timeout(1500)
            meta = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            ck("面板标题行也报最新版（第 N 版）", "第 %d 版" % max(vers) in meta,
               meta.split("\n")[0])
            # 把 v2 跑成 done，再改一次清单才会开出 v3（pending 那一版是就地改，不涨版本）
            g = api(port, "/api/plan/task/generate", {"slot_key": SLOT, "channel": "toutiao",
                                                      "form": "guide", "video_ids": stored + [extra]})
            for _ in range(40):
                time.sleep(0.5)
                if api(port, "/api/plan/task/%s" % g[1]["task_id"])[1]["task"]["status"] != "running":
                    break
            extra2 = pick_extra_video(db, stored + [extra])
            api(port, "/api/plan/task", {"slot_key": SLOT, "channel": "toutiao",
                                         "form": "guide", "video_ids": stored + [extra2]})
            vers = [t["version"] for t in api(port, "/api/plan/tasks?slot_key=" + SLOT)[1]["tasks"]]
            ck("三版本并存（1/2/3）", sorted(vers) == [1, 2, 3], sorted(vers))
            pg.evaluate("() => ptRefresh(true)")     # 面板要重读服务端那一版（它不会自己知道多了 v3）
            pg.wait_for_timeout(1500)
            xt = video_title(db, extra2)
            meta_v = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            ck("面板重读后落在第 3 版", "第 3 版" in meta_v, meta_v.split("\n")[0])
            ck("面板素材清单跟着最新版（含刚加的那条 %s）" % extra2,
               xt[:12] in pg.eval_on_selector("#pt-materials", "e => e.innerText"), xt[:24])
            picked3 = fr.evaluate("""() => { const t=taskFor('%s','toutiao','guide');
                return t ? t.version : null; }""" % SLOT)
            ck("卡片也落到第 3 版（重新拉过任务后）", picked3 == 3, picked3)
            shot(pg, "09_panel_v2", "#pt-overlay .modal-box")

            print("\n== 4b T3：页面上取消最后一条采用（已开工版之后必须得到空清单的新版）==")
            # Codex 点名"接口测试 + 一次真实页面点击"。接口侧在 e2e G 段与反例第二项，这一格补页面侧。
            # 用**另一题**做，免得改到 SLOT 那题的版本数把后面几节的前提挪掉。
            pg.evaluate("() => ptClose()")     # 面板盖着视口，不关掉就点不到 iframe 里的卡片
            pg.wait_for_timeout(800)
            pairs = fr.evaluate("""() => { const out=[];
                document.querySelectorAll('.card[data-item]').forEach(c => {
                  const id=c.dataset.item; if(id==='%s') return;
                  const b=c.querySelector(".ref [data-act='adopt']"); if(!b) return;
                  const r=(refsOf(id)[Number(b.dataset.ri)]||{});
                  if(r.db_id) out.push([id, Number(r.db_id)]);
                });
                return out; }""" % SLOT)
            con3 = sqlite3.connect(db)
            other = pick_vid = None
            for cid, vid in pairs:
                row = con3.execute("SELECT length(transcript) FROM videos WHERE id=?",
                                   (vid,)).fetchone()
                if row and row[0] and row[0] > 300:      # 首条素材得有正文，否则后面跑不成 done
                    other, pick_vid = cid, vid
                    break
            con3.close()
            ck("找到一题来点「取消最后一条」（首条素材有正文）", bool(other),
               "候选 %d 题" % len(pairs))
            if not other:
                return 1
            c2 = ".card[data-item='%s']" % other
            a2 = "%s .ref [data-act='adopt']" % c2
            fr.click(a2)
            pg.wait_for_timeout(2500)
            ck("另一题的「采用」点下去变已采用", fr.text_content(a2) == "已采用",
               fr.text_content(a2))
            ts = api(port, "/api/plan/tasks?slot_key=" + other)[1]["tasks"]
            ids1 = (ts[0]["video_ids"] if ts else [])
            ck("采用清单存进了服务端（不是只画在页面上）", ids1 == [pick_vid], ids1)
            chn, frm = ts[0]["channel"], ts[0]["form"]
            code, gj = api(port, "/api/plan/task/generate",
                           {"slot_key": other, "channel": chn, "form": frm, "video_ids": ids1})
            st_gen = gj.get("mode")
            for _ in range(40):
                if st_gen != "started":
                    break
                time.sleep(0.5)
                st_gen = api(port, "/api/plan/task/%s" % gj["task_id"])[1]["task"]["status"]
            ck("这一题先跑出一版 done（制造「已开工版本」这个前提）", st_gen == "done", st_gen)
            un = "%s .adopted [data-act='unadopt']" % c2
            ck("已采用栏里那颗「取消采用」真的渲染出来了", bool(fr.query_selector(un)))
            old = api(port, "/api/plan/tasks?slot_key=" + other)[1]["tasks"]
            old_v1 = [t for t in old if t["version"] == 1][0]
            fr.click(un)
            pg.wait_for_timeout(3000)
            after = api(port, "/api/plan/tasks?slot_key=" + other)[1]["tasks"]
            cur = [t for t in after if t["version"] == max(x["version"] for x in after)][0]
            ck("取消最后一条 → 开新版，清单是空的（不把旧素材复制回来）",
               cur["version"] == 2 and cur["video_ids"] == [] and (cur.get("sources") or []) == [],
               {"v": cur["version"], "ids": cur["video_ids"], "srcs": len(cur.get("sources") or [])})
            v1 = [t for t in after if t["version"] == 1][0]
            ck("done 那一版原样冻住（清单/状态/稿件号都没动）",
               v1["video_ids"] == old_v1["video_ids"] and v1["status"] == "done"
               and v1["summary_id"] == old_v1["summary_id"],
               {"ids": v1["video_ids"], "st": v1["status"], "sid": v1["summary_id"]})
            ck("页面上那条已从已采用栏消失、按钮回到「采用」",
               not fr.query_selector(un) and fr.text_content(a2) == "采用",
               fr.text_content(a2))
            shot(pg, "17_cancel_last_adopt", c2, fr)
            # 下面几节要靠面板点生成，先把 SLOT 那题的面板重新打开
            fr.click("%s [data-act='task']" % card)
            pg.wait_for_timeout(1500)

            print("\n== 5 缺正文 → 422 恢复入口（真点，不起任务）==")
            # 存一条当时能用的，再在**临时库**里把它的正文抹掉（真跑对应"素材被清了/转写没跑成"），
            # 生成那一步才会真判成"没有可用素材"。上一版直接存 999120 是存不进去的——保存端就 422 了。
            code, j = api(port, "/api/plan/task", {"slot_key": SLOT, "channel": "toutiao",
                                                   "form": "guide", "video_ids": [999121]})
            con2 = sqlite3.connect(db)
            con2.execute("UPDATE videos SET transcript='' WHERE id=999121")
            con2.commit(); con2.close()
            pg.evaluate("() => { _pt.video_ids=[999121]; _pt.task=null; ptRefresh(true); }")
            pg.wait_for_timeout(1500)
            g2 = pg.query_selector("#pt-overlay [onclick^='ptGenerate(false)']")
            if g2:
                g2.click()
                pg.wait_for_timeout(1500)
                prob = pg.eval_on_selector("#pt-problems", "e => e.innerText")
                ck("界面列出具体缺项（没有转写正文）", "没有转写正文" in prob, prob.replace("\n", " ")[:80])
                ck("给了可点的恢复入口（去补转写）", "去素材库开这条补转写" in prob)
                shot(pg, "10_blocked", "#pt-overlay .modal-box")
            else:
                ck("找到生成按钮以测恢复入口", False, "当前动作区没那颗")

            print("\n== 6 整理中：桩延迟 6 秒，拍进度并最终更新 ==")
            pg.evaluate("() => ptClose()")
            port2 = free_port()
            srvs.append(start_server(db, port2, delay=6.0))
            pg.goto("http://127.0.0.1:%d/#schedule" % port2, wait_until="load")
            pg.wait_for_timeout(6000)
            fr = frame_of(pg)
            fr.wait_for_selector(".card[data-item='%s']" % SLOT, timeout=15000)
            pg.wait_for_timeout(2500)
            fr.click(".card[data-item='%s'] [data-act='task']" % SLOT)
            pg.wait_for_timeout(1200)
            # 这一版换成一条正文齐的素材（999121 的正文在上一格被抹掉了，不能再用）
            api(port2, "/api/plan/task", {"slot_key": SLOT, "channel": "toutiao",
                                          "form": "guide", "video_ids": [999122]})
            pg.evaluate("() => ptRefresh(true)")
            pg.wait_for_timeout(1000)
            g3 = pg.query_selector("#pt-overlay [onclick^='ptGenerate(false)'], #pt-overlay [onclick^='ptGenerate(true)']")
            g3.click()
            pg.wait_for_timeout(2200)
            prog = pg.eval_on_selector("#pt-prog", "e => e.innerText")
            meta2 = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            ck("生成中面板显示状态=整理中", "整理中" in meta2, meta2.split("\n")[0])
            ck("生成中有实时进度文案", len(prog) > 4, prog)
            card_go = fr.evaluate("() => document.querySelector(\".card[data-item='%s'] [data-act='task']\").textContent" % SLOT)
            ck("卡片主入口同步显示「整理中…」（不是继续处理旧稿）", card_go.strip() == "整理中…", card_go)
            shot(pg, "11_running", "#pt-overlay .modal-box")
            # 刷新一次：R6 —— 进度轮询要自动续上、最终更新
            pg.reload(wait_until="load")
            pg.wait_for_timeout(5000)
            fr = frame_of(pg)
            fr.wait_for_selector(".card[data-item='%s']" % SLOT, timeout=15000)
            pg.wait_for_timeout(2000)
            fr.click(".card[data-item='%s'] [data-act='task']" % SLOT)
            pg.wait_for_timeout(1200)
            meta3 = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            ck("刷新后重开面板仍接着轮（状态还是整理中或已跑完）",
               ("整理中" in meta3) or ("已有稿" in meta3), meta3.split("\n")[0])
            deadline = time.time() + 25
            while time.time() < deadline:
                pg.wait_for_timeout(1000)
                if "继续处理这篇稿" in pg.eval_on_selector("#pt-acts", "e => e.innerText"):
                    break
            ck("进度最终更新成已有稿 + 出现「继续处理这篇稿」",
               "继续处理这篇稿" in pg.eval_on_selector("#pt-acts", "e => e.innerText"))
            card_go2 = fr.evaluate("() => document.querySelector(\".card[data-item='%s'] [data-act='task']\").textContent" % SLOT)
            ck("跑完后卡片主入口回到「继续处理」", card_go2.strip() == "继续处理", card_go2)
            shot(pg, "12_after_running", "#pt-overlay .modal-box")
            pg.evaluate("() => ptClose()")

            print("\n== 6b 窄屏 375px ==")
            # 量窄屏要**单独打开那张页**：工作台外壳有最小宽度（375 视口下文档仍 1020），
            # 在 iframe 里量到的 714 是外壳给的宽度，不是手机视口——上一版就在这儿自欺过。
            pg.set_viewport_size({"width": 375, "height": 820})
            pg.goto(B + "/static/schedule.html", wait_until="load")
            pg.wait_for_timeout(5000)
            fr3 = pg.main_frame
            fr3.wait_for_selector(".card[data-item='%s']" % SLOT, timeout=15000)
            pg.wait_for_timeout(2000)
            sw = fr3.evaluate("() => document.documentElement.scrollWidth")
            ck("375px 单独打开排产页不横向溢出", sw <= 376, "文档宽 %s" % sw)
            on = fr3.evaluate("() => [...document.querySelectorAll(\".card[data-item='%s'] .chip.on\")]"
                              ".map(e=>e.textContent)" % SLOT)
            ck("375px 下渠道/形式选中态还在", len(on) == 2, on)
            h = fr3.evaluate("() => { const b=document.querySelector(\".card[data-item='%s'] [data-act='task']\");"
                             " const r=b.getBoundingClientRect();"
                             " return {w:Math.round(r.width), h:Math.round(r.height)}; }" % SLOT)
            ck("主入口那颗没被挤成竖排一字", 60 <= h["w"] and h["h"] <= 40, h)
            shot(pg, "14_card_375", ".card[data-item='%s']" % SLOT)
            # 面板那张：?task= 直接打开（绕开非响应式外壳），点开的仍是同一份服务端状态
            q = urllib.parse.quote(json.dumps(
                {"v2t": "openTask", "slot_key": SLOT, "channel": "toutiao", "form": "guide",
                 "title": "窄屏面板取证", "plan_date": "2026-11-10"}))
            pg.goto(B + "/?task=" + q, wait_until="load")
            pg.wait_for_timeout(5000)
            geo = pg.evaluate("() => { const b=document.querySelector('#pt-overlay .modal-box');"
                              " if(!b) return null; return {scrollW:Math.round(b.scrollWidth),"
                              " clientW:Math.round(b.clientWidth), w:Math.round(b.getBoundingClientRect().width)}; }")
            ck("375px 下面板开得到且自身不横向溢出",
               bool(geo) and geo["scrollW"] <= geo["clientW"] + 1, geo)
            shot(pg, "15_panel_375", "#pt-overlay .modal-box")
            pg.set_viewport_size({"width": 1280, "height": 1700})
            br.close()

        print("\n== 7 失败态与中断恢复（另起一个抛异常的隔离服务）==")
        port3 = free_port()
        srvs.append(start_server(db, port3, mode="raise"))
        code, j = api(port3, "/api/plan/task", {"slot_key": "fail1", "channel": "toutiao",
                                                "form": "guide", "video_ids": [999122]})
        tid = j["task"]["id"]
        code, j = api(port3, "/api/plan/task/generate", {"slot_key": "fail1", "channel": "toutiao",
                                                         "form": "guide", "video_ids": [999122]})
        time.sleep(2.5)
        st3 = api(port3, "/api/plan/task/%d" % tid)[1]["task"]
        ck("生成抛异常 → 服务端落 failed 且带原因",
           st3["status"] == "failed" and "模型超时" in (st3.get("error") or ""),
           "%s / %s" % (st3["status"], (st3.get("error") or "")[:40]))
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=CHROME, headless=True)
            pg = br.new_page(viewport={"width": 1280, "height": 1400})
            pg.on("pageerror", lambda e: errs.append("pageerror: " + e))
            pg.goto("http://127.0.0.1:%d/#schedule" % port3, wait_until="load")
            pg.wait_for_timeout(6000)
            fr = frame_of(pg)
            fr.wait_for_selector("#list .card", timeout=15000)
            pg.wait_for_timeout(2500)
            fr.click(".card[data-item='%s'] [data-act='task']" % SLOT)
            pg.wait_for_timeout(1500)
            # 面板这一路走的是 setup 那道题；失败态用 fail1 那道：直接开面板
            pg.goto("http://127.0.0.1:%d/?task=%s" % (port3, urllib.parse.quote(json.dumps(
                {"v2t": "openTask", "slot_key": "fail1", "channel": "toutiao", "form": "guide",
                 "title": "失败态测试", "plan_date": "2026-11-05"}))), wait_until="load")
            try:
                pg.wait_for_function("() => _pt && _pt.task", timeout=12000)
            except Exception:
                # 第一版这里直接判失败；先记一笔"要重开一次才拿得到"，再决定是不是缺陷
                print("   ! ?task= 打开后 12 秒内没拿到任务行，重开一次面板再看")
                pg.evaluate("() => ptRefresh(true)")
                pg.wait_for_timeout(2500)
            meta4 = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            prog4 = pg.eval_on_selector("#pt-prog", "e => e.innerText")
            ck("面板状态显示失败", "失败" in meta4, meta4.split("\n")[0])
            ck("面板把失败原因原样摆出来", "模型超时" in prog4, prog4[:60])
            shot(pg, "13_failed", "#pt-overlay .modal-box")
            br.close()

        # 中断恢复（R5）：库里塞一行 running → **把还活着的测试服务全停掉** →
        # 再用同一份临时库起新的一台 → 该版应被标 aborted。停干净才算重启。
        con = sqlite3.connect(db)
        con.execute("INSERT INTO plan_tasks (slot_key,channel,form,title,video_ids,sources,version,"
                    "status,error,created_at,updated_at) VALUES ('crash','toutiao','guide','中断测试',"
                    "'[999122]','[]',1,'running','','2026-10-20 00:00:00','2026-10-20 00:00:00')")
        con.commit(); con.close()
        stopped = stop_all_servers()
        ck("重启前旧服务全停了（%d 台，端口逐个确认已释放）" % stopped, stopped > 0, "%d 台" % stopped)
        port4 = free_port()
        srvs.append(start_server(db, port4))
        got = api(port4, "/api/plan/tasks?slot_key=crash")[1]["tasks"]
        ck("重启后卡在 running 的那版被核对成 aborted（不再永久 409）",
           got and got[0]["status"] == "aborted", got and got[0]["status"])
        ck("aborted 那行写清了人该怎么继续", "先回素材库看" in (got[0].get("error") or ""),
           (got[0].get("error") or "")[:50])
        code, j = api(port4, "/api/plan/task/generate", {"slot_key": "crash", "channel": "toutiao",
                                                          "form": "guide", "video_ids": [999122]})
        ck("aborted 之后允许人工重试（显式起，不自动重付费）", code == 200 and j.get("mode") == "started",
           "%s / %s" % (code, j.get("mode")))

        print("\n== 8 T4：中断那一版在**页面上**看得见说明（不只 API 有文案）==")
        # 再造一个 aborted：塞 running → 再起一台（同库）→ 核对成 aborted，然后真开面板读字
        con3 = sqlite3.connect(db)
        con3.execute("INSERT INTO plan_tasks (slot_key,channel,form,title,video_ids,sources,version,"
                     "status,error,created_at,updated_at) VALUES ('crash2','toutiao','guide','中断可见性测试',"
                     "'[999122]','[]',1,'running','','2026-10-20 00:00:00','2026-10-20 00:00:00')")
        con3.commit(); con3.close()
        stop_all_servers()          # 同样先停干净，这一台才是"重启后的那一个进程"
        port5 = free_port()
        srvs.append(start_server(db, port5))
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=CHROME, headless=True)
            pg = br.new_page(viewport={"width": 1280, "height": 1200})
            pg.on("pageerror", lambda e: errs.append("pageerror: " + e))
            q = urllib.parse.quote(json.dumps(
                {"v2t": "openTask", "slot_key": "crash2", "channel": "toutiao", "form": "guide",
                 "title": "中断可见性测试", "plan_date": "2026-11-05"}))
            pg.goto("http://127.0.0.1:%d/?task=%s" % (port5, q), wait_until="load")
            pg.wait_for_timeout(6000)
            try:
                pg.wait_for_function("() => _pt && _pt.task", timeout=12000)
            except Exception:
                pg.evaluate("() => ptRefresh(true)")
                pg.wait_for_timeout(2500)
            meta5 = pg.eval_on_selector("#pt-meta", "e => e.innerText")
            prog5 = pg.eval_on_selector("#pt-prog", "e => e.innerText")
            acts5 = pg.eval_on_selector("#pt-acts", "e => e.innerText").replace("\n", " / ")
            ck("状态那一格写的是「已中断，待核对」，不是空白也不是「失败」",
               "已中断，待核对" in meta5, meta5.split("\n")[0])
            ck("页面上摆出中断原因（该写空行的那条补上了）",
               "中断原因" in prog5 and "先回素材库看" in prog5, prog5[:70])
            ck("给的是核对入口 + 写明重试会再消耗额度，不是直接一个生成按钮",
               "去整理稿列表核对" in acts5 and "核对后重试（可能再次消耗额度）" in acts5
               and "生成这一版" not in acts5, acts5)
            shot(pg, "16_aborted_ui", "#pt-overlay .modal-box")
            br.close()
    finally:
        # 只收 ALIVE 里还活着的（srvs 里那些已经在 stop_all_servers 里 wait 掉了，
        # 再对同一个 pid 组发信号会碰上 pid 复用，那是真会误伤别人的）
        for p, _port in ALIVE:
            try:
                os.killpg(os.getpgid(p.pid), 15)
            except Exception:
                pass
        print("\n== 收尾与隔离自证 ==")
        # 直证隔离：每台服务的 READY 行都自报它用的库路径，全都在临时目录里才算隔离成功。
        # （只比 mtime/大小**不等于**内容没变，所以这条才是主判据，下面那条只作旁证。）
        bad_db = [l for l in READY_LINE if "plan_walk" not in l]
        ck("每一台测试服务用的都是临时库副本（%d 台，无一连生产库）" % len(READY_LINE),
           bool(READY_LINE) and not bad_db, bad_db or [l.split("db=")[-1].split()[0] for l in READY_LINE][:3])
        now = os.stat(PROD_DB)
        ck("真实库文件 mtime/大小与开跑前一致（旁证，不宣称逐字节一致）",
           (now.st_mtime, now.st_size) == (prod_stat.st_mtime, prod_stat.st_size),
           "%s / %s vs %s / %s" % (int(now.st_mtime), now.st_size,
                                   int(prod_stat.st_mtime), prod_stat.st_size))
        con3 = sqlite3.connect(PROD_DB)
        try:
            n_task = con3.execute("SELECT COUNT(*) FROM plan_tasks").fetchone()[0]
        except sqlite3.OperationalError:
            n_task = -1
        con3.close()
        ck("生产库 plan_tasks 里没留下本次的行", n_task in (0, -1), "现有 %s 行" % n_task)
        try:
            live = urllib.request.urlopen("http://127.0.0.1:15801/api/stats", timeout=3).status
            print("   生产服务 15801 仍在（HTTP %s），本次未重启、未写入" % live)
        except Exception as e:
            print("   ! 生产服务没应答：%s" % e)
        subprocess.run(["rm", "-rf", tmp])
        print("   临时目录已删：%s" % tmp)
        print("\n页面报错：", [e for e in errs if "favicon" not in e] or "无")
        print("FAIL %d 项%s" % (len(fails), ("：" + "、".join(fails)) if fails else ""))
    return 1 if fails else 0


def card_sel_id():
    return SLOT


def shutil_copy(a, b):
    import shutil
    shutil.copyfile(a, b)


if __name__ == "__main__":
    sys.exit(main())
