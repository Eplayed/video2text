# -*- coding: utf-8 -*-
"""T17：定点修正 ASR 不覆盖原文，修订会使母稿版本失效。"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import content_store, guide_mother, source_corrections  # noqa: E402


with tempfile.TemporaryDirectory(prefix="v2t-source-fix-") as d:
    db = Path(d) / "test.db"
    raw = "联盟收集8代验卖。付费包有4个时隔行囊和2个12格包。"
    conn = content_store.connect(db)
    conn.execute("INSERT INTO videos (id,source_sheet,source_row,title,transcript,updated_at) "
                 "VALUES (1,'t',1,'背包',?,'2026-11-06')", (raw,))
    conn.execute("INSERT INTO ai_summaries "
                 "(id,video_id,summary_type,title,structured_data,status,source_video_ids,created_at,updated_at) "
                 "VALUES (9,1,'wechat_material','背包','{}','ai','[1]','2026-11-06','2026-11-06')")
    conn.commit()
    conn.close()

    before, rev_before = guide_mother.source_snapshot(db, 9)
    assert before[0]["text"] == raw
    patches = [{"old": "8代验卖", "new": "8袋燕麦", "evidence": "原视频 00:47 任务画面"},
               {"old": "4个时隔行囊", "new": "4个10格行囊", "evidence": "原视频 02:59 商城截图"}]
    out = source_corrections.save(db, 1, source_corrections.raw_hash(raw), patches)
    after, rev_after = guide_mother.source_snapshot(db, 9)
    assert rev_after != rev_before and "4个10格行囊" in after[0]["text"]
    assert after[0]["corrected"] and after[0]["raw_text"] == raw
    assert out["patch_count"] == 2
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT transcript FROM videos WHERE id=1").fetchone()[0] == raw
    conn.close()
    print("PASS 修订改变母稿源版本，原始转写保持原样")

    try:
        source_corrections.save(db, 1, source_corrections.raw_hash(raw),
                                [{"old": "不存在", "new": "测试", "evidence": "测试"}])
        raise AssertionError("应拒绝找不到的原片段")
    except source_corrections.CorrectionError:
        pass
    assert source_corrections.inspect(db, 1)["patches"] == patches
    print("PASS 原片段找不到时拒绝保存，旧修订保留")

    import web.app as webapp
    old_db = webapp.DB_PATH
    webapp.DB_PATH = db
    try:
        client = webapp.app.test_client()
        got = client.get("/api/source-corrections/1")
        assert got.status_code == 200 and got.json["effective_text"] == after[0]["text"]
        shown = client.get("/api/poster/source/9")
        assert shown.status_code == 200 and shown.json["sources"][0]["corrected"]
        assert shown.json["sources"][0]["raw_transcript"] == raw
        assert webapp._material_text({"id": 9, "video_id": 1,
                                      "source_video_ids": "[1]"}) == after[0]["text"]
        stale = client.post("/api/source-corrections/1", json={
            "base_hash": "0" * 64, "patches": patches})
        assert stale.status_code == 409
    finally:
        webapp.DB_PATH = old_db
    print("PASS 来源接口与出图事实闸同读有效文，旧版本写入被 409 拦截")

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE videos SET transcript=? WHERE id=1", (raw + " 新内容",))
    conn.commit()
    conn.close()
    try:
        guide_mother.source_snapshot(db, 9)
        raise AssertionError("原文更新后不应悄悄用旧修订")
    except guide_mother.MotherError as exc:
        assert "重新核对" in str(exc)
    print("PASS 原始转写更新时旧修订失效，生成链路明确报错")

print("ALL_OK")
