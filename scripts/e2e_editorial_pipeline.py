"""回归 #121：来源对齐、补充覆盖、职业分页、自动修稿后才选图。"""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from src import ai_poster, blizzard_news, claim_check, content_store, guide_mother, model_usage
from src import asr_fix, editorial_model, poster_content, source_corrections, term_glossary
from web import app as wa

TITLE = "魔兽世界无限：猎人、萨满与术士调整"
PRIMARY = "猎人宠物生命值从150%修正为115%。"
OFFICIAL = {"body": "【Druid】\nNature's Swiftness makes Wild Growth instant.\n\n"
                    "【Warrior】\nSweeping Strikes retains its charge if no second target is hit.",
            "official_date": "November 10, 2026",
            "official_url": "https://news.blizzard.com/en-us/article/24315281/forever-hotfixes"}
CASE = {
    "Druid": ("德鲁伊", "自然迅捷可让野性成长瞬发。"),
    "Warrior": ("战士", "横扫攻击未命中第二目标时保留层数。"),
    "": ("猎人", PRIMARY),
}


def test_asr():
    prompts = []

    class Whisper:
        def transcribe(self, _path, **options):
            prompts.append(options["initial_prompt"])
            return iter([]), None

    with patch.object(main, "HAS_FASTER_WHISPER", True), patch.object(main, "_get_asr_model", return_value=Whisper()):
        with asr_fix.material_context(TITLE):
            main.transcribe_audio("fixture.wav")
    assert {"奥术射击", "召唤雄鹰", "风暴打击", "火焰箭"} <= set(prompts[0].split("、"))
    assert "工作流" not in prompts[0]
    assert "风暴打击" not in term_glossary.asr_prompt(context="魔兽世界无限 德鲁伊")


def test_pipeline(root):
    db = root / "test.db"
    conn = content_store.connect(db)
    try:
        content_store.init_db(conn)
        conn.execute("INSERT INTO videos(id, aweme_id, source_sheet, source_row, title, transcript, updated_at) "
                     "VALUES(1, 'BV123', 'B站视频', 1, ?, ?, '2026-11-12')", (TITLE, PRIMARY))
        conn.execute("INSERT INTO videos(id, aweme_id, source_sheet, source_row, title, transcript, published_at, updated_at) "
                     "VALUES(2, 'nga2', 'NGA无限板块', 2, '猎人职业热修', ?, '2026-11-11', '2026-11-12')",
                     ("https://worldofwarcraft.blizzard.com/en-us/news/24315281",))
        conn.execute("INSERT INTO ai_summaries(id, video_id, summary_type, title, content, structured_data,"
                     "created_at, updated_at) VALUES(1, 1, 'game_guide', ?, ?, ?, '2026-11-12', '2026-11-12')",
                     (TITLE, PRIMARY, json.dumps({"editorial_supplements": [{"id": 2, "sheet": "NGA无限板块"}]})))
        conn.commit()
        raw = "萨满、风怒图腾和副魔可以同时触发。冷血提高了2秒。"
        clean = source_corrections.effective_text(conn, 1, raw, TITLE)
        assert clean == raw.replace("副魔", "附魔")
    finally:
        conn.close()

    events = []

    def extract(segment, _title, _config):
        key = segment.get("topic_group") or ""
        owner, display = CASE[key]
        quote = segment["text"].strip().splitlines()[-1]
        return {"points": [{"quote": quote, "display_text": display, "subject": owner,
                            "priority": "must" if not key else "support", "modality": "fact"}]}

    def plan(_summary, _config, *, mother, feedback=None, **options):
        assert options.get("frames") is None
        events.append("修稿" if feedback else "拆页")
        points = mother["points"]
        pages = []
        for point in points:
            owner, text = CASE[point.get("topic_group") or ""]
            pages.append({"title": owner + "调整", "layout_key": "guide",
                          "cards": [{"t": "调整要点", "d": text, "point_ids": [point["point_id"]]}]})
        if not feedback:
            pages[1]["cards"], pages[2]["cards"] = pages[2]["cards"], pages[1]["cards"]
            pages[1]["ask"] = {"q": "猎人宠物生命值是多少？", "options": ["115%"]}
        return {**pages[0], "pages": pages}, {}, [], []

    def audit(mother, _out, _cfg, _src):
        assert len(mother["points"]) == 3
        events.append("校对")
        return {"issues": []}

    def frames(_summary, _config):
        assert events == ["拆页", "修稿", "校对"], events
        events.append("配图")
        return [{"i": 1, "file": "/frame/shaman.jpg", "subject": "萨满角色"},
                {"i": 2, "file": "/frame/hunter.jpg", "subject": "猎人角色"}]

    original = guide_mother.audit_output
    with patch.object(wa, "DB_PATH", db), patch.object(wa, "OUTPUT_DIR", root), \
            patch.object(ai_poster, "OUTPUT_DIR", root / "poster"), \
            patch.object(model_usage, "RUN_DIR", root / "runs"), \
            patch.object(wa, "_editorial_config", return_value={"editorial": True, "model": "test"}), \
            patch.object(blizzard_news, "forever_hotfix_from_reference", return_value=OFFICIAL) as fetch, \
            patch.object(guide_mother, "_extract_segment", side_effect=extract), \
            patch.object(ai_poster, "build_text_plan", side_effect=plan), \
            patch.object(wa, "_guide_frames_for", side_effect=frames), \
            patch.object(guide_mother, "_model_compare", side_effect=AssertionError("覆盖失败不应校对语义")), \
            patch.object(guide_mother, "audit_output", side_effect=lambda m, p, u, c, **kw:
                         original(m, p, u, c, checker=(audit if events[-1] == "修稿" else None), **kw)):
        client = wa.app.test_client()
        response = client.post("/api/poster/plan", json={"summary_id": 1, "text_mode": "typeset"})
        assert response.status_code == 200, response.get_json()
        for _ in range(250):
            status = client.get("/api/poster/status?summary_id=1").get_json()
            if not status["running"]:
                break
            time.sleep(.02)
        assert not status["running"] and not status.get("error"), status.get("error")
        draft = status["plan"]
        assert draft["fidelity"]["ready"] and not draft["violations"], draft.get("violations")
        assert draft["supplements"][0]["pages"] == [2, 3]
        assert draft["supplements"][0]["status"] == "official"
        pages = draft["plan"]["pages"]
        assert pages[0]["photos"][0]["frame"] == "F2" and not any(p.get("photos") for p in pages[1:])
        assert events == ["拆页", "修稿", "校对", "配图"]
        sources = client.get("/api/poster/source/1").get_json()["sources"]
        assert sources[1]["role"] == "official_supplement" and sources[1]["official_date"] == OFFICIAL["official_date"]
        wa._prepare_poster_sources(wa._poster_get_summary(1))
        assert fetch.call_count == 1


def main_test():
    test_asr()
    assert not claim_check.check_fields([("卡片", PRIMARY)])["hard"]
    assert claim_check.check_fields([("卡片", "兽人命令使猎人宠物伤害提高80%")])["hard"]
    assert not claim_check.check_fields([("卡片", "左手第一间拿道具")])["hard"]
    assert not claim_check.check_fields([("卡片", "经验统计 按睡袋增益，非亡灵约2.46万经验，亡灵约1.58万经验")])["hard"]
    assert not poster_content.frame_related({"title": "术士改动"}, "萨满角色")
    assert not poster_content.frame_related({"title": "萨满风暴打击"}, "萨满漩涡武器技能")
    cards = [{"t": "步骤", "d": "保留完整条件和步骤。", "point_ids": [str(i)]} for i in range(7)]
    overfull = {"title": "攻略要点", "cards": cards, "layout_key": "guide"}
    assert editorial_model.reflow_poster_pages(
        overfull, ai_poster.TS_LIMITS, 5, 6, ai_poster.plan_chars)
    assert [card for page in overfull["pages"] for card in page["cards"]] == cards
    assert not ai_poster.lint_plan(overfull, ai_poster.TS_LIMITS)
    with tempfile.TemporaryDirectory(prefix="editorial-pipeline-") as folder:
        test_pipeline(Path(folder))
    print("ALL_OK 术语入口、官方补充冻结和覆盖、串页修稿、配图顺序及旧登记误拦")


if __name__ == "__main__":
    main_test()
