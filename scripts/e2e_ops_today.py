#!/usr/bin/env python3
"""今日选题的未用候选，以及首页自定义选题复用排产生成。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import content_store, plan_series, plan_tasks
from web import app as web


def main():
    today = plan_series.cn_now().date().isoformat()
    with TemporaryDirectory(prefix="v2t-today-") as folder:
        root = Path(folder)
        db = root / "today.db"
        texts = [
            (1, "魔兽无限 地下城装备旧专题"),
            (2, "魔兽无限 地下城 装备掉落"),
            (3, "魔兽无限 地下城 副本路径"),
            (4, "正式服 地下城 首领装备"),
        ]
        conn = content_store.connect(db)
        for vid, title in texts:
            conn.execute("INSERT INTO videos(id,source_sheet,source_row,title,published_at,transcript,updated_at)"
                         " VALUES(?, '抖音视频数据', ?, ?, ?, ?, ?)",
                         (vid, vid, title, today, (title + " 魔兽无限 ") * 20, today))
        conn.commit()
        conn.close()
        content_store.save_summary(db, 1, "game_guide", {"title": texts[0][1]}, source_video_ids=[1])
        used = plan_series.new_slot(db, texts[0][1])
        plan_tasks.save_sources(db, used["slot_key"], "toutiao", "guide",
                                {"title": texts[0][1]}, content_store.get_videos_by_ids(db, [1]))
        generated = []

        def fake_generate(_keys, summary_type, combine, job, brief, *, video_ids, ai_config):
            assert combine and ai_config["model"] == "deepseek-v4-pro"
            generated.append((video_ids, brief["title"]))
            return [content_store.save_summary(db, video_ids[0], summary_type,
                                               {"title": brief["title"]}, source_video_ids=video_ids)]

        with patch.object(web, "DB_PATH", db), patch.object(web, "OUTPUT_DIR", root), \
                patch.object(web.ai_poster, "OUTPUT_DIR", root / "poster"), \
                patch.object(web, "_editorial_config", return_value={"model": "deepseek-v4-pro"}), \
                patch.object(web, "_original_graphics_enabled", return_value=True), \
                patch.object(web, "_run_generation", side_effect=fake_generate):
            client = web.app.test_client()
            data = client.get("/api/ops/today?channel=toutiao").get_json()
            assert {m["video_id"] for t in data["topics"] for m in t["materials"]} == {2, 3}, data
            assert [t["slot_key"] for t in data["existing"]] == [used["slot_key"]]
            before = len(plan_series.slots(db))
            result = client.get("/api/ops/today/custom", query_string={
                "channel": "toutiao", "theme": "地下城 装备"})
            assert result.status_code == 200
            hits = result.get_json()["materials"]
            question = client.get("/api/ops/today/custom", query_string={
                "channel": "toutiao", "theme": "如何拿装备掉落"})
            assert question.status_code == 200
            assert question.get_json()["materials"]
            assert len(plan_series.slots(db)) == before and {v["video_id"] for v in hits} >= {2, 3}
            body = {"channel": "toutiao", "theme": "地下城 装备", "video_ids": [2, 3]}
            for invalid in ({**body, "channel": "wrong"}, {**body, "video_ids": [2, 2]}):
                assert client.post("/api/ops/today/custom", json=invalid).status_code == 400
            assert client.post("/api/ops/today/custom", json={**body, "video_ids": [999]}).status_code == 409

            def finish():
                for _ in range(150):
                    options = client.get("/api/ops/today?channel=toutiao").get_json()
                    item = next(t for t in options["existing"] if t.get("custom"))
                    if not (item.get("job") or {}).get("running"):
                        assert not item.get("job"), item
                        return options, item
                    time.sleep(.03)
                raise AssertionError("自定义选题生成超时")

            assert client.post("/api/ops/today/custom", json=body).status_code == 202
            options, item = finish()
            assert not options["topics"] and item["summary_id"], options
            assert [r["video_id"] for r in item["materials"]] == [2, 3]
            assert client.post("/api/ops/today/custom", json=body).status_code == 202
            _, second = finish()
            assert second["summary_id"] == item["summary_id"] and generated == [([2, 3], "地下城 装备")]
            assert len(plan_series.slots(db)) == before + 1
            assert not client.get("/api/ops/today?channel=wechat").get_json()["topics"]
    print("OPS_TODAY_OK")


if __name__ == "__main__":
    main()
