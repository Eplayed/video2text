#!/usr/bin/env python3
"""隔离界面走查：请求次数和未知费用与服务端账本同源。"""
import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import content_store  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db = wa.DB_PATH
    with tempfile.TemporaryDirectory(prefix="v2t-usage-ui-") as tmp:
        db = Path(tmp) / "test.db"
        content_store.connect(db).close()
        wa.DB_PATH = db
        server = make_server("127.0.0.1", 0, wa.app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                usage = {"run_id": "a" * 32, "status": "failed", "request_count": 2,
                         "tokens_known": 30, "tokens_unknown_count": 1,
                         "cost_yuan": None, "cost_status": "unknown", "calls": [
                             {"requested_model": "unit-model", "response_model": "unit-model"},
                             {"requested_model": "unit-model", "response_model": ""}]}
                status = {"running": False, "done": True, "summary_id": 1,
                          "error": "原意核对未通过", "plan": None,
                          "model_usage": usage}
                page.route("**/api/poster/status**", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(status, ensure_ascii=False)))
                package = {"id": 1, "summary_id": 1, "title": "隔离公众号图文",
                           "images": [], "copy_text": "原文信息", "model_usage": usage}
                page.route("**/api/wechat/1", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps(package, ensure_ascii=False)))
                page.goto("http://127.0.0.1:%d/" % server.server_port,
                          wait_until="domcontentloaded")
                page.evaluate("openPosterDialog(1)")
                page.wait_for_function("document.getElementById('pk-result').innerText.includes('费用未知')")
                poster_text = page.locator("#pk-overlay").inner_text()
                assert "上次任务模型请求 2 次" in poster_text
                assert "已知 30 token，另有 1 次未返回用量" in poster_text
                assert "0 元”只指底图与排版" in poster_text
                assert "底图 0 元" in page.locator("#pk-go-top").inner_text()
                assert "费用说明：上述金额只算底图" in page.evaluate("posterGenerate.toString()")
                out = ROOT / "output" / "迭代验收" / "原意保留" / "I3"
                out.mkdir(parents=True, exist_ok=True)
                poster_shot = out / "海报模型用量-隔离界面.png"
                page.screenshot(path=str(poster_shot), full_page=False)
                page.evaluate("closePosterDialog()")
                page.evaluate("switchPage('wechat')")
                page.evaluate("openWechatDetail(1)")
                page.wait_for_function("document.getElementById('wx-model-usage').innerText.includes('费用未知')")
                assert "本包生成步骤模型请求 2 次" in page.locator("#wx-model-usage").inner_text()
                wechat_shot = out / "公众号模型用量-隔离界面.png"
                page.screenshot(path=str(wechat_shot), full_page=False)
                assert not errors, errors
                print("PASS 海报/公众号界面按服务端实际请求次数显示，费用未知不写 0 元")
                print("SCREENSHOTS %s %s" % (poster_shot, wechat_shot))
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            wa.DB_PATH = old_db
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
