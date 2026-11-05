#!/usr/bin/env python3
"""隔离界面走查：单源整理稿引导到母稿，旧多源整理稿保留旧入口。"""
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
    with tempfile.TemporaryDirectory(prefix="v2t-legacy-tt-ui-") as tmp:
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
                page.goto("http://127.0.0.1:%d/" % server.server_port,
                          wait_until="domcontentloaded")
                def mock_summary(route):
                    summary_id = int(route.request.url.rsplit("/", 1)[-1])
                    source_ids = [1] if summary_id == 1 else [1, 2]
                    body = {
                        "id": summary_id, "summary_type": "toutiao_mix",
                        "title": "隔离测试：素材原意", "content": "原素材的核心信息。",
                        "video_id": 1, "source_video_ids": json.dumps(source_ids),
                        "video_title": "视频素材", "transcript": "原文核心信息。",
                        "status": "ai", "model": "test",
                    }
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(body, ensure_ascii=False))
                page.route("**/api/content/summaries/*", mock_summary)
                page.evaluate("openSummary(1)")
                page.locator("#modal-actions .mc-hint").wait_for()
                actions = page.locator("#modal-actions")
                assert "单条素材请点「AI 海报」走原文核对" in actions.inner_text()
                assert "旧合集卡片" not in actions.inner_text()
                out = ROOT / "output" / "迭代验收" / "原意保留" / "I3"
                out.mkdir(parents=True, exist_ok=True)
                shot = out / "单源图文入口-隔离界面.png"
                page.screenshot(path=str(shot), full_page=False)
                page.evaluate("openSummary(2)")
                page.locator("#modal-actions button", has_text="旧合集卡片").wait_for()
                assert "单条素材请点" not in actions.inner_text()
                print("PASS 单源只引导到 AI 海报；多源保留旧合集卡片按钮")
                print("SCREENSHOT %s" % shot)
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            wa.DB_PATH = old_db
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
