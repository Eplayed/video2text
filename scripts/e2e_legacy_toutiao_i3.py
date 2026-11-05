#!/usr/bin/env python3
"""隔离验证单源头条图文不落入旧卡片化入口，多源旧合集仍兼容。"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import content_store  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db = wa.DB_PATH
    old_generate = wa.toutiao_graphics.generate_graphics
    old_mode = os.environ.get("V2T_ORIGINAL_GRAPHICS")
    with tempfile.TemporaryDirectory(prefix="v2t-tt-legacy-") as tmp:
        db = Path(tmp) / "test.db"
        conn = content_store.connect(db)
        for vid in (1, 2):
            conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                         "VALUES (?,'t',?,'素材','原文。','2026-11-05')", (vid, vid))
        for sid, ids in ((1, [1]), (2, [1, 2])):
            conn.execute("INSERT INTO ai_summaries "
                         "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                         "VALUES (?,1,'toutiao_mix','整理稿','内容','{}','ai',?,'2026-11-05','2026-11-05')",
                         (sid, json.dumps(ids)))
        conn.commit()
        conn.close()
        wa.DB_PATH = db
        os.environ["V2T_ORIGINAL_GRAPHICS"] = "on"
        wa._tt_task_status["running"] = False
        calls = []
        wa.toutiao_graphics.generate_graphics = lambda summary, *_a, **_kw: (
            calls.append(summary["id"]), {"id": summary["id"], "title": "旧合集", "images": []})[1]
        try:
            client = wa.app.test_client()
            single = client.post("/api/toutiao/generate", json={"summary_id": 1})
            assert single.status_code == 409 and "AI 海报" in single.get_json()["error"]
            assert calls == [] and not wa._tt_task_status["running"]
            print("PASS 单源新素材在旧头条入口前置拦截，无模型调用")
            multi = client.post("/api/toutiao/generate", json={"summary_id": 2})
            assert multi.status_code == 200 and multi.get_json()["status"] == "started"
            for _ in range(50):
                if not wa._tt_task_status["running"]:
                    break
                time.sleep(0.02)
            assert calls == [2] and wa._tt_task_status["package"]["id"] == 2
            print("PASS 多源旧合集仍可走旧入口")
        finally:
            wa.DB_PATH = old_db
            wa.toutiao_graphics.generate_graphics = old_generate
            wa._tt_task_status["running"] = False
            if old_mode is None:
                os.environ.pop("V2T_ORIGINAL_GRAPHICS", None)
            else:
                os.environ["V2T_ORIGINAL_GRAPHICS"] = old_mode
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
