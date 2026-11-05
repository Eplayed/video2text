#!/usr/bin/env python3
"""隔离演练两渠道成品替换、旧版归档、版本失效与暂停新任务。"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import content_store, guide_mother  # noqa: E402
from web import app as wa  # noqa: E402


def main():
    old_db, old_poster, old_env = wa.DB_PATH, wa.ai_poster.OUTPUT_DIR, os.environ.get(
        "V2T_ORIGINAL_GRAPHICS")
    old_connect = content_store.connect
    with tempfile.TemporaryDirectory(prefix="v2t-graphics-rollback-") as tmp:
        root = Path(tmp)
        db = root / "test.db"
        conn = content_store.connect(db)
        conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                     "VALUES (1,'t',1,'原文','两个小号拿到奖励。','2026-11-05')")
        conn.execute("INSERT INTO ai_summaries "
                     "(id,video_id,summary_type,title,content,structured_data,status,source_video_ids,created_at,updated_at) "
                     "VALUES (1,1,'guide_article','整理稿','旧稿','{}','draft','[1]','2026-11-05','2026-11-05')")
        conn.commit()
        conn.close()
        mother = guide_mother.ensure_mother(db, 1, {"api_key": "stub"}, extractor=lambda *_a: {
            "points": [{"quote": "两个小号拿到奖励", "display_text": "两个小号拿到奖励",
                        "priority": "must", "subject": "两个小号",
                        "conditions": [], "modality": "fact"}]})
        wa.DB_PATH = db
        wa.ai_poster.OUTPUT_DIR = root / "poster"
        try:
            for channel in ("poster", "wechat"):
                base = root / channel
                target = base / "1"
                target.mkdir(parents=True)
                old_bytes = json.dumps({"title": "上一版", "source_revision": mother["source_revision"],
                                        "mother_revision": mother["mother_revision"]},
                                       ensure_ascii=False).encode("utf-8")
                (target / "manifest.json").write_bytes(old_bytes)
                (target / "old.png").write_bytes(b"old-image")
                stage = base / "_pending-test"
                stage.mkdir()
                (stage / "manifest.json").write_text(json.dumps({
                    "title": "当前版", "source_revision": mother["source_revision"],
                    "mother_revision": mother["mother_revision"]}, ensure_ascii=False))
                (stage / "new.png").write_bytes(b"new-image")
                wa._poster_promote_staged(1, stage, mother["source_revision"],
                                          mother["mother_revision"], target=target)
                history = list((base / "_history" / "1").iterdir())
                assert len(history) == 1
                assert (history[0] / "manifest.json").read_bytes() == old_bytes
                assert (history[0] / "old.png").read_bytes() == b"old-image"
                assert json.loads((target / "manifest.json").read_text())["title"] == "当前版"
                assert (target / "new.png").read_bytes() == b"new-image"
                rehearsal = base / "_restore-check"
                shutil.copytree(history[0], rehearsal)
                assert (rehearsal / "manifest.json").read_bytes() == old_bytes
                shutil.rmtree(rehearsal)
                print("PASS %s 新旧包各自完整，旧版可从隔离快照还原" % channel)

            class CommitFails:
                def __init__(self, conn):
                    self.conn = conn
                def execute(self, *args, **kwargs):
                    return self.conn.execute(*args, **kwargs)
                def commit(self):
                    raise RuntimeError("隔离提交失败")
                def close(self):
                    self.conn.close()
            failing = root / "poster" / "_pending-commit-fail"
            failing.mkdir()
            (failing / "manifest.json").write_text('{"title":"不应接管"}')
            content_store.connect = lambda path: CommitFails(old_connect(path))
            try:
                try:
                    wa._poster_promote_staged(1, failing, mother["source_revision"],
                                              mother["mother_revision"])
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("事务提交失败应回滚成品替换")
            finally:
                content_store.connect = old_connect
            assert json.loads((root / "poster" / "1" / "manifest.json").read_text())["title"] == "当前版"
            assert len(list((root / "poster" / "_history" / "1").iterdir())) == 1
            print("PASS 数据库提交失败时当前包恢复，旧版归档不变")

            os.environ["V2T_ORIGINAL_GRAPHICS"] = "off"
            client = wa.app.test_client()
            assert client.get("/api/graphics/original-mode").get_json()["enabled"] is False
            assert client.post("/api/poster/plan", json={"summary_id": 1}).status_code == 409
            assert client.post("/api/wechat/generate", json={"summary_id": 1}).status_code == 409
            assert all((root / channel / "1" / "manifest.json").exists()
                       for channel in ("poster", "wechat"))
            print("PASS 暂停新模式只拒绝新任务，已保存两渠道快照未删除")

            conn = content_store.connect(db)
            conn.execute("UPDATE videos SET transcript='原文版本已变。' WHERE id=1")
            conn.commit()
            conn.close()
            stale = root / "poster" / "_pending-stale"
            stale.mkdir()
            (stale / "manifest.json").write_text('{"title":"迟到任务"}')
            try:
                wa._poster_promote_staged(1, stale, mother["source_revision"],
                                          mother["mother_revision"])
            except wa.ai_poster.PosterError:
                pass
            else:
                raise AssertionError("源版本变化后迟到任务不应覆盖当前包")
            assert json.loads((root / "poster" / "1" / "manifest.json").read_text())["title"] == "当前版"
            assert len(list((root / "poster" / "_history" / "1").iterdir())) == 1
            print("PASS 旧任务晚返回不覆盖当前包，也不污染旧版快照")
        finally:
            wa.DB_PATH, wa.ai_poster.OUTPUT_DIR = old_db, old_poster
            content_store.connect = old_connect
            if old_env is None:
                os.environ.pop("V2T_ORIGINAL_GRAPHICS", None)
            else:
                os.environ["V2T_ORIGINAL_GRAPHICS"] = old_env
    print("ALL_OK")


if __name__ == "__main__":
    sys.exit(main())
