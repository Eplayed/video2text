# -*- coding: utf-8 -*-
"""为 ASR 原文保存可追溯的定点修订；原始 transcript 永不覆盖。"""
import hashlib
import json
import sqlite3
from datetime import datetime


class CorrectionError(ValueError):
    pass


def raw_hash(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _table_exists(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='video_source_corrections'").fetchone())


def _apply(raw_text, patches):
    text = raw_text
    if not isinstance(patches, list) or not 1 <= len(patches) <= 60:
        raise CorrectionError("请提交 1–60 处定点修订")
    seen = set()
    for i, patch in enumerate(patches, 1):
        if not isinstance(patch, dict):
            raise CorrectionError("第 %d 处修订格式错误" % i)
        old, new = patch.get("old"), patch.get("new")
        evidence = patch.get("evidence")
        if (not isinstance(old, str) or not old or len(old) > 300
                or not isinstance(new, str) or not new or len(new) > 300
                or old == new or not isinstance(evidence, str)
                or not evidence.strip() or len(evidence) > 500):
            raise CorrectionError("第 %d 处修订需提供原片段、新片段及证据位置" % i)
        if old in seen or text.count(old) != 1:
            raise CorrectionError("第 %d 处原片段不唯一或已失效，请重新核对原始转写" % i)
        seen.add(old)
        text = text.replace(old, new, 1)
    return text


def effective_text(conn, video_id, raw_text):
    """只读：修订不匹配当前 ASR 时拦截，不静默回退旧原文。"""
    if not _table_exists(conn):
        return raw_text
    row = conn.execute("SELECT base_hash, patches_json FROM video_source_corrections "
                       "WHERE video_id=?", (int(video_id),)).fetchone()
    if not row:
        return raw_text
    if row["base_hash"] != raw_hash(raw_text):
        raise CorrectionError("素材 %d 的原始转写已更新，定点修订须重新核对" % int(video_id))
    try:
        patches = json.loads(row["patches_json"])
    except (TypeError, ValueError):
        raise CorrectionError("素材 %d 的定点修订记录损坏，请先修复" % int(video_id))
    return _apply(raw_text, patches)


def inspect(db_path, video_id):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id, title, transcript FROM videos WHERE id=?",
                           (int(video_id),)).fetchone()
        if not row:
            raise CorrectionError("素材不存在")
        raw = str(row["transcript"] or "")
        saved = (conn.execute("SELECT base_hash, patches_json, updated_at "
                              "FROM video_source_corrections WHERE video_id=?",
                              (int(video_id),)).fetchone()
                 if _table_exists(conn) else None)
        patches = json.loads(saved["patches_json"]) if saved else []
        stale = bool(saved and saved["base_hash"] != raw_hash(raw))
        effective = "" if stale else _apply(raw, patches) if patches else raw
        return {"video_id": int(video_id), "title": row["title"],
                "raw_text": raw, "base_hash": raw_hash(raw),
                "patches": patches, "effective_text": effective,
                "stale": stale, "updated_at": saved["updated_at"] if saved else ""}
    finally:
        conn.close()


def save(db_path, video_id, base_hash, patches):
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT transcript FROM videos WHERE id=?",
                           (int(video_id),)).fetchone()
        if not row or not str(row["transcript"] or "").strip():
            raise CorrectionError("素材不存在或没有原始转写")
        raw = str(row["transcript"])
        if base_hash != raw_hash(raw):
            raise CorrectionError("原始转写版本已变化，请重新读取再修订")
        effective = _apply(raw, patches)
        conn.execute("CREATE TABLE IF NOT EXISTS video_source_corrections ("
                     "video_id INTEGER PRIMARY KEY, base_hash TEXT NOT NULL, "
                     "patches_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("INSERT INTO video_source_corrections "
                     "(video_id,base_hash,patches_json,updated_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(video_id) DO UPDATE SET base_hash=excluded.base_hash, "
                     "patches_json=excluded.patches_json, updated_at=excluded.updated_at",
                     (int(video_id), base_hash,
                      json.dumps(patches, ensure_ascii=False), now))
        conn.commit()
        return {"video_id": int(video_id), "base_hash": base_hash,
                "effective_hash": raw_hash(effective), "patch_count": len(patches),
                "updated_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
