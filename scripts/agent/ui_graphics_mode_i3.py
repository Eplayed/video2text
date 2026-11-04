#!/usr/bin/env python3
"""隔离界面走查：三类历史图文包显式标出旧版校验边界。"""
import json
import os
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
    old_env = os.environ.get("V2T_ORIGINAL_GRAPHICS")
    old_paths = (wa.DB_PATH, wa.ai_poster.OUTPUT_DIR, wa.wechat_graphics.OUTPUT_DIR,
                 wa.toutiao_channel.OUTPUT_DIR, wa.toutiao_graphics.OUTPUT_DIR)
    with tempfile.TemporaryDirectory(prefix="v2t-mode-ui-") as tmp:
        root = Path(tmp)
        db = root / "test.db"
        content_store.connect(db).close()
        for kind in ("poster", "wechat", "toutiao"):
            folder = root / kind / "1"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({
                "id": 1, "summary_id": 1, "title": "旧包示例", "images": [],
                "created_at": "2026-10-01", "url": "", "lint": []},
                ensure_ascii=False), encoding="utf-8")
        wa.DB_PATH = db
        wa.ai_poster.OUTPUT_DIR = root / "poster"
        wa.wechat_graphics.OUTPUT_DIR = root / "wechat"
        wa.toutiao_channel.OUTPUT_DIR = root / "toutiao"
        wa.toutiao_graphics.OUTPUT_DIR = root / "toutiao"
        os.environ["V2T_ORIGINAL_GRAPHICS"] = "off"
        server = make_server("127.0.0.1", 0, wa.app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto("http://127.0.0.1:%d/" % server.server_port, wait_until="domcontentloaded")
                page.evaluate("switchPage('wechat')")
                page.evaluate("openWechatDetail(1)")
                page.wait_for_function("document.getElementById('wx-img-count').textContent.includes('旧版，未做新版校验')")
                assert "旧版，未做新版校验" in page.locator("#wx-img-count").inner_text()
                out = ROOT / "output" / "迭代验收" / "原意保留" / "I3"
                out.mkdir(parents=True, exist_ok=True)
                shot = out / "旧版包只读提示-隔离界面.png"
                page.screenshot(path=str(shot), full_page=False)
                page.evaluate("switchPage('toutiao')")
                page.evaluate("openToutiaoDetail(1)")
                page.wait_for_function("document.getElementById('tt-img-count').textContent.includes('旧版，未做新版校验')")
                page.evaluate("switchPage('poster')")
                page.evaluate("openPosterDetail(1)")
                page.wait_for_function("document.getElementById('pk-detail-img-label').textContent.includes('旧版，未做新版校验')")
                assert page.request.get("http://127.0.0.1:%d/api/graphics/original-mode" % server.server_port).json()["enabled"] is False
                print("PASS 公众号、头条、海报详情均显示旧版校验边界；开关关闭")
                print("SCREENSHOT %s" % shot)
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            (wa.DB_PATH, wa.ai_poster.OUTPUT_DIR, wa.wechat_graphics.OUTPUT_DIR,
             wa.toutiao_channel.OUTPUT_DIR, wa.toutiao_graphics.OUTPUT_DIR) = old_paths
            if old_env is None:
                os.environ.pop("V2T_ORIGINAL_GRAPHICS", None)
            else:
                os.environ["V2T_ORIGINAL_GRAPHICS"] = old_env
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
