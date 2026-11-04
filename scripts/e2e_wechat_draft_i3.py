#!/usr/bin/env python3
"""公众号被拦草稿只读预览与原文失效。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import content_store, guide_mother  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db, old_out = wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR
    with tempfile.TemporaryDirectory(prefix="v2t-wx-draft-") as tmp:
        root = Path(tmp)
        db = root / "test.db"
        conn = content_store.connect(db)
        conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES (1,'t',1,'素材','小号拿到1点。','2026-11-04')")
        conn.execute("INSERT INTO ai_summaries "
                     "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                     "VALUES (1,1,'wechat_material','整理稿','旧稿','{}','draft','[1]','2026-11-04','2026-11-04')")
        conn.commit()
        conn.close()
        mother = guide_mother.ensure_mother(db, 1, {"api_key": "fake"}, extractor=lambda *_: {
            "points": [{"quote": "小号拿到1点", "display_text": "小号拿到1点",
                        "priority": "must", "subject": "小号", "conditions": [], "modality": "fact"}]})
        out = root / "wechat"
        folder = out / "_drafts" / "1"
        folder.mkdir(parents=True)
        (folder / "draft.json").write_text(json.dumps({"cards": [{"title": "小号拿点"}],
            "fidelity": {"mother_revision": mother["mother_revision"]}}, ensure_ascii=False))
        wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR = db, out
        try:
            client = wa.app.test_client()
            current = client.get("/api/wechat/draft/1").get_json()
            assert current["stale"] is False and current["draft"]["cards"][0]["title"] == "小号拿点"
            print("PASS 当前母稿的失败卡片可只读预览")
            conn = content_store.connect(db)
            conn.execute("UPDATE videos SET transcript='小号拿到2点。' WHERE id=1")
            conn.commit()
            conn.close()
            assert client.get("/api/wechat/draft/1").get_json()["stale"] is True
            print("PASS 原文变化后草稿显示过期")
            assert client.get("/api/wechat/draft/2").status_code == 404
            print("PASS 无草稿不返回旧内容")
        finally:
            wa.DB_PATH, wa.wechat_graphics.OUTPUT_DIR = old_db, old_out
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
