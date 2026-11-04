#!/usr/bin/env python3
"""I2 路由：原始转写可读，旧母稿不能生成或签审。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import content_store, guide_mother  # noqa: E402
from web import app as wa  # noqa: E402


def check(label, ok):
    if not ok:
        raise AssertionError(label)
    print("PASS " + label)


def main():
    old_db, old_out, old_poster = wa.DB_PATH, wa.OUTPUT_DIR, wa.ai_poster.OUTPUT_DIR
    with tempfile.TemporaryDirectory(prefix="v2t-i2-route-") as tmp:
        root = Path(tmp)
        db = root / "case.db"
        out = root / "output"
        out.mkdir()
        conn = content_store.connect(db)
        conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES (1,'t',1,'视频标题','原文：两个小号拿到11点。','2026-11-04')")
        conn.execute("INSERT INTO ai_summaries "
                     "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                     "VALUES (1,1,'guide_article','整理稿','错误的旧整理稿','{}','draft','[1]','2026-11-04','2026-11-04')")
        conn.commit()
        conn.close()
        def extractor(seg, _title, _cfg):
            return {"points": [{"quote": "两个小号拿到11点", "display_text": "两个小号拿到11点",
                                "priority": "must", "subject": "两个小号",
                                "conditions": [], "modality": "fact"}]}
        mother = guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=extractor)
        wa.DB_PATH, wa.OUTPUT_DIR, wa.ai_poster.OUTPUT_DIR = db, out, out / "poster"
        try:
            client = wa.app.test_client()
            source = client.get("/api/poster/source/1").get_json()
            check("编辑页给原始转写和已定位母稿", source["sources"][0]["transcript"]
                  == "原文：两个小号拿到11点。" and source["mother"]["status"] == "complete"
                  and "错误的旧整理稿" not in json.dumps(source, ensure_ascii=False))
            plan = {"title": "两个小号", "source_revision": mother["source_revision"],
                    "mother_revision": mother["mother_revision"],
                    "cards": [{"t": "条件", "d": "两个小号拿到11点",
                               "point_ids": [mother["points"][0]["point_id"]]}]}
            folder = out / "poster" / "1"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({"id": 1, "summary_id": 1,
                "title": "旧包", "plan": plan, "package_hash": "oldhash"}, ensure_ascii=False))
            stage = out / "poster" / "_pending-test"
            stage.mkdir()
            (stage / "manifest.json").write_text(json.dumps({"title": "新包", "id": 1,
                "summary_id": 1, "plan": plan}, ensure_ascii=False), encoding="utf-8")
            wa._poster_promote_staged(1, stage, mother["source_revision"], mother["mother_revision"])
            check("匹配版本的暂存包整体替换旧包且保留编辑草稿",
                  json.loads((folder / "manifest.json").read_text())["title"] == "新包"
                  and not stage.exists())
            newer = out / "poster" / "_pending-stale"
            newer.mkdir()
            (newer / "manifest.json").write_text('{"title":"过期包"}', encoding="utf-8")
            conn = content_store.connect(db)
            conn.execute("UPDATE videos SET transcript='原文已改：两个小号拿到12点。' WHERE id=1")
            conn.commit()
            conn.close()
            try:
                wa._poster_promote_staged(1, newer, mother["source_revision"],
                                          mother["mother_revision"])
                raise AssertionError("过期包被放行")
            except wa.ai_poster.PosterError:
                pass
            check("旧任务晚返回不覆盖现有成品",
                  json.loads((folder / "manifest.json").read_text())["title"] == "新包")
            stale_source = client.get("/api/poster/source/1").get_json()
            check("源文更新后不展示旧母稿为当前版", stale_source["mother"] == {})
            response = client.post("/api/poster/generate", json={"summary_id": 1, "plan": plan})
            check("出图前版本闸拦旧计划且不启任务", response.status_code == 409
                  and not wa._poster_status["running"])
            review = client.get("/api/poster/review?summary_id=1").get_json()
            check("旧包签审状态失效", review.get("stale") is True
                  and review.get("reviewed") is False)
            signed = client.post("/api/poster/review", json={"summary_id": 1,
                                                              "reviewer": "测试员"})
            check("旧包不能重新签审", signed.status_code == 409)
        finally:
            wa.DB_PATH, wa.OUTPUT_DIR, wa.ai_poster.OUTPUT_DIR = old_db, old_out, old_poster
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
