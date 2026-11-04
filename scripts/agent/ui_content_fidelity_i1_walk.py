#!/usr/bin/env python3
"""在隔离 Flask 上走海报编辑页：超量内容可见、切页后不丢，并留截图。"""
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "scripts" / "agent" / "poster_test_server.py"
SHOT = ROOT / "output" / "迭代验收" / "原意保留" / "I1" / "编辑页-超量草稿.png"


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="v2t_i1_ui_") as tmp:
        work = Path(tmp)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), "--port", str(port),
             "--db", str(work / "test.db"), "--poster-out", str(work / "poster")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = "http://127.0.0.1:%d" % port
            for _ in range(80):
                try:
                    urllib.request.urlopen(base + "/", timeout=1).close()
                    break
                except Exception:
                    if proc.poll() is not None:
                        raise RuntimeError("隔离服务启动失败")
                    time.sleep(0.25)
            else:
                raise RuntimeError("隔离服务未就绪")
            with sync_playwright() as play:
                browser = play.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(base, wait_until="domcontentloaded")
                page.evaluate("""() => {
                  const pages = Array.from({length: 7}, (_, i) => ({
                    title: '第' + (i + 1) + '页', subtitle: '', scene: '',
                    footer: i === 6 ? '末页来源：完整保留' : '', layout_key: 'guide',
                    cards: Array.from({length: i === 0 ? 6 : 1}, (_, j) => ({
                      t: '要点' + (j + 1), d: '条件' + (j + 1) + '不能丢', v: ''}))
                  }));
                  _pkId = 123456;
                  document.getElementById('pk-overlay').style.display = 'flex';
                  pkRenderPlan({title: pages[0].title, style_key: 'guide_ink',
                                footer: '末页来源：完整保留', pages: pages}, {});
                }""")
                before = page.evaluate("""() => ({pages:_pkPages.length,
                    cards:_pkPages[0].cards.length, last:_pkPages[6].footer,
                    errors:pkProblems(pkReadPayload())})""")
                page.evaluate("pkSwitchPage(6)")
                page.wait_for_timeout(500)
                after = page.evaluate("""() => ({pages:pkReadPayload().pages.length,
                    cards:pkReadPayload().pages[0].cards.length,
                    last:pkReadPayload().pages[6].footer,
                    flatten:document.querySelector('button[onclick="pkFlatten()"]') !== null})""")
                SHOT.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(SHOT), full_page=False)
                browser.close()
            assert not errors, errors
            assert before["pages"] == after["pages"] == 7, (before, after)
            assert before["cards"] == after["cards"] == 6, (before, after)
            assert before["last"] == after["last"] == "末页来源：完整保留", (before, after)
            assert before["errors"] and not after["flatten"], (before, after)
            print("ALL_OK 编辑页 7 页和首屏 6 条切页后未丢；截图：%s" % SHOT)
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
