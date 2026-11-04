#!/usr/bin/env python3
"""隔离界面走查：公众号失败草稿定点保存、复审与沿用草稿出图参数。"""
import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.e2e_wechat_draft_revise_i3 import checked  # noqa: E402
from src import content_store, guide_mother  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db, old_out, old_audit = wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR, guide_mother.audit_output
    with tempfile.TemporaryDirectory(prefix="v2t-wx-ui-") as tmp:
        root = Path(tmp)
        db = root / "test.db"
        conn = content_store.connect(db)
        conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES (1,'t',1,'素材','小号能得到奖励。','2026-11-04')")
        conn.execute("INSERT INTO ai_summaries "
                     "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                     "VALUES (1,1,'wechat_material','整理稿','小号能得到奖励。','{}','ai','[1]','2026-11-04','2026-11-04')")
        conn.commit()
        conn.close()
        mother = guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=lambda *_: {
            "points": [{"quote": "小号能得到奖励", "display_text": "小号能得到奖励",
                        "priority": "must", "subject": "小号", "conditions": [], "modality": "fact"}]})
        cards = [{"kind": "list", "title": "小号奖励", "subtitle": "旧描述", "items": [
            {"name": "奖励", "desc": "旧内容"}], "point_ids": [mother["points"][0]["point_id"]]}]
        draft = {"cards": cards, "copy_text": "小号可以看奖励。", "digest": "奖励整理",
                 "fidelity": checked(mother, cards, "小号可以看奖励。", "奖励整理",
                                     "小号能得到奖励。", ready=False)}
        folder = root / "wechat" / "_drafts" / "1"
        folder.mkdir(parents=True)
        (folder / "first.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR = db, root / "wechat"
        wa._wx_task_status["running"] = False

        def fake_audit(m, plan, publish, _cfg, source_text=""):
            result = checked(m, [], publish["copy_text"], publish["digest"], source_text)
            revision = guide_mother.audit_revision(m, plan, publish, source_text)
            result["coverage"]["input_revision"] = revision
            result["semantic"]["input_revision"] = revision
            return result

        guide_mother.audit_output = fake_audit
        server = make_server("127.0.0.1", 0, wa.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sent = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto("http://127.0.0.1:%d/" % server.server_port, wait_until="domcontentloaded")
                page.evaluate("wxShowBlockedDraft(1)")
                page.locator("#wx-blocked-draft").wait_for()
                assert "原意核对未过" in page.locator("#wx-blocked-draft h3").inner_text()
                page.locator("#wx-blocked-draft details").first.click()
                field = page.locator('[data-wx-path="cards.0.items.0.desc"]')
                field.fill("奖励来自原文")
                page.get_by_role("button", name="保存修改并重审").click()
                page.get_by_text("原意核对通过，可以渲染图片。").wait_for()
                assert page.locator('[data-wx-path="cards.0.items.0.desc"]').input_value() == "奖励来自原文"
                assert page.locator('[data-wx-path="cards.0.subtitle"]').input_value() == "旧描述"
                assert page.get_by_role("button", name="用这份已核草稿渲染图片").count() == 1
                print("PASS 弹窗可定点改文、保存复审并显示通过状态")
                out = ROOT / "output" / "迭代验收" / "原意保留" / "I3"
                out.mkdir(parents=True, exist_ok=True)
                shot = out / "公众号草稿定点修改-隔离界面.png"
                page.locator("#wx-blocked-draft").screenshot(path=str(shot))
                page.evaluate("document.getElementById('wx-gen-tpl').value='minimal'")

                def intercept(route):
                    sent.append(route.request.post_data_json)
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"status": "started", "summary_id": 1}))

                page.route("**/api/wechat/generate", intercept)
                page.get_by_role("button", name="用这份已核草稿渲染图片").click()
                page.wait_for_timeout(300)
                assert len(sent) == 1 and sent[0]["reuse_draft"] is True
                assert sent[0]["template"] == "minimal" and sent[0]["draft_revision"]
                print("PASS 出图提交携带已核草稿版本与所选模板；未触发真实生成")
                print("SCREENSHOT %s" % shot)
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR = old_db, old_out
            guide_mother.audit_output = old_audit
            wa._wx_task_status["running"] = False
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
