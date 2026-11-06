#!/usr/bin/env python3
"""隔离验证公众号草稿定点修改、版本保护与沿用草稿出图。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import content_store, guide_mother  # noqa: E402
from src.graphics.channels import wechat as wx  # noqa: E402
from web import app as wa  # noqa: E402


def checked(mother, cards, copy_text, digest, source_text, ready=True):
    plan = guide_mother.wechat_plan(cards, mother)
    revision = guide_mother.audit_revision(
        mother, plan, {"copy_text": copy_text, "digest": digest}, source_text)
    status = "pass" if ready else "fail"
    return {"ready": ready, "source_revision": mother["source_revision"],
            "mother_revision": mother["mother_revision"],
            "coverage": {"status": status, "issues": [], "input_revision": revision},
            "semantic": {"status": status, "issues": [], "input_revision": revision}}


def main():
    old_db, old_out, old_audit = wa.DB_PATH, wx.OUTPUT_DIR, guide_mother.audit_output
    old_render, old_cards = wx._render_cards, wx._llm_cards
    with tempfile.TemporaryDirectory(prefix="v2t-wx-edit-") as tmp:
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
        point_id = mother["points"][0]["point_id"]
        cards = [{"kind": "list", "title": "小号奖励", "subtitle": "旧描述", "section": "奖励说明",
                  "items": [{"name": "奖励", "desc": "旧内容", "tag": "可领"}],
                  "note": {"title": "提醒", "text": "以原文为准"}, "point_ids": [point_id]}]
        check_text = guide_mother.wechat_plan(cards, mother)["pages"][0]["cards"][0]["d"]
        assert all(x in check_text for x in ("奖励说明", "可领", "提醒", "以原文为准"))
        print("PASS 图面小节、标签与提醒均进入原意核对输入")
        source_text = "小号能得到奖励。"
        draft = {"cards": cards, "copy_text": "小号可以看奖励。", "digest": "奖励整理",
                 "source_note": "来源备注", "keyword_reply": "回复关键词",
                 "fidelity": checked(mother, cards, "小号可以看奖励。", "奖励整理", source_text,
                                     ready=False)}
        folder = root / "wechat" / "_drafts" / "1"
        folder.mkdir(parents=True)
        (folder / "first.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        wa.DB_PATH, wx.OUTPUT_DIR = db, root / "wechat"
        wa._wx_task_status["running"] = False
        calls = []

        def fake_audit(m, plan, publish, _cfg, source_text=""):
            calls.append((plan, publish))
            result = checked(m, [], publish["copy_text"], publish["digest"], source_text)
            revision = guide_mother.audit_revision(m, plan, publish, source_text)
            result["coverage"]["input_revision"] = revision
            result["semantic"]["input_revision"] = revision
            return result

        guide_mother.audit_output = fake_audit
        try:
            client = wa.app.test_client()
            before = client.get("/api/wechat/draft/1").get_json()
            revision = before["revision"]
            assert not before["stale"]
            denied = client.post("/api/wechat/draft/1/revise", json={"revision": revision,
                "edits": [{"path": "cards.0.point_ids", "value": "bad"}]})
            assert denied.status_code == 400 and not calls
            print("PASS 来源 ID 与结构不能通过定点编辑修改")
            denied_point = client.post("/api/wechat/draft/1/revise", json={
                "revision": revision, "edits": [],
                "point_edits": [{"card_index": 0, "point_ids": ["不存在的来源点"]}]})
            assert denied_point.status_code == 400 and not calls
            denied_index = client.post("/api/wechat/draft/1/revise", json={
                "revision": revision, "edits": [],
                "point_edits": [{"card_index": -1, "point_ids": [point_id]}]})
            assert denied_index.status_code == 400 and not calls
            print("PASS 来源点修订只接受当前母稿现有 ID 与有效卡片序号")

            response = client.post("/api/wechat/draft/1/revise", json={"revision": revision,
                "edits": [{"path": "cards.0.items.0.desc", "value": "奖励来自原文"}],
                "point_edits": [{"card_index": 0, "point_ids": [point_id]}]})
            assert response.status_code == 200, response.get_json()
            usage = response.get_json()["model_usage"]
            assert usage["channel"] == "wechat" and usage["stage"] == "revise"
            assert usage["summary_id"] == 1 and usage["request_count"] == 0
            after = client.get("/api/wechat/draft/1").get_json()
            assert after["revision"] != revision and after["draft"]["fidelity"]["ready"]
            assert after["draft"]["cards"][0]["items"][0]["desc"] == "奖励来自原文"
            assert after["draft"]["cards"][0]["subtitle"] == "旧描述"
            assert after["draft"]["cards"][0]["point_ids"] == [point_id]
            assert len(calls) == 1
            print("PASS 一处文字保存后重审；其他文字、来源 ID 保持不变")
            stale = client.post("/api/wechat/draft/1/revise", json={"revision": revision,
                "edits": [{"path": "copy_text", "value": "重复提交"}]})
            assert stale.status_code == 409 and len(calls) == 1
            print("PASS 旧草稿版本不可覆盖新版本")

            wx._llm_cards = lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("不应重新拆卡"))
            wx._render_cards = lambda *_a, **_kw: ([], [], [], [], [])
            guide_mother.audit_output = lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("已核内容不应重新调审核模型"))
            summary = content_store.get_summary(db, 1)
            m = wx.generate_graphics(summary, {}, mother=mother, source_text=source_text,
                                     output_root=root / "staged", prepared_data=after["draft"],
                                     prechecked_fidelity=after["draft"]["fidelity"])
            assert m["model"] == "edited-draft" and m["cards"][0]["items"][0]["desc"] == "奖励来自原文"
            assert m["title"] == "小号奖励" and m["source_note"] == "来源备注"
            print("PASS 沿用已核草稿直接渲染，不重新拆卡或调用审核模型")

            bad = json.loads(json.dumps(after["draft"], ensure_ascii=False))
            bad["cards"][0]["title"] = "未经核对的标题"
            try:
                wx.generate_graphics(summary, {}, mother=mother, source_text=source_text,
                                     output_root=root / "staged2", prepared_data=bad,
                                     prechecked_fidelity=after["draft"]["fidelity"])
            except wx.gate.GraphicsGateError as exc:
                assert "审核版本" in str(exc)
            else:
                raise AssertionError("未经核对的新文字被渲染")
            print("PASS 草稿文字与审核版本不一致时阻断出图")

            files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            old_check = json.loads(files[0].read_text(encoding="utf-8"))
            old_check["fidelity"]["semantic"]["input_revision"] = "旧规则"
            files[0].write_text(json.dumps(old_check, ensure_ascii=False), encoding="utf-8")
            outdated = client.get("/api/wechat/draft/1").get_json()
            assert outdated["audit_stale"] is True
            blocked = client.post("/api/wechat/generate", json={"summary_id": 1,
                "reuse_draft": True, "draft_revision": outdated["revision"]})
            assert blocked.status_code == 409 and not wa._wx_task_status["running"]
            guide_mother.audit_output = fake_audit
            renewed = client.post("/api/wechat/draft/1/revise", json={
                "revision": outdated["revision"], "edits": []})
            assert renewed.status_code == 200 and renewed.get_json()["ready"]
            assert client.get("/api/wechat/draft/1").get_json()["audit_stale"] is False
            print("PASS 旧审核版本预先阻断出图，可零修改重新核对")
        finally:
            wa.DB_PATH, wx.OUTPUT_DIR = old_db, old_out
            guide_mother.audit_output = old_audit
            wx._render_cards, wx._llm_cards = old_render, old_cards
            wa._wx_task_status["running"] = False
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
