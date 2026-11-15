"""SQLite content store for videos and AI-ready summaries."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl


BASE_COLUMNS = {
    "链接": 1,
    "状态": 2,
    "视频ID": 3,
    "作者": 4,
    "发布时间": 5,
    "标题": 6,
    "视频文案ASR": 7,
    "标签": 8,
    "封面URL": 9,
    "视频链接": 10,
    "口播原文": 11,
    "AI优化文案": 12,
    "AI备选标题": 13,
    "关键词摘要": 14,
    "备注": 15,
}


# 连接等锁上限（sqlite3 原默认就是 5.0 秒，写出来免得以为没设）
DB_TIMEOUT_SEC = 5.0


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=DB_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """只读连接：跑 SELECT 用，不建表、不加列、不可能抢到写锁。

    为什么要有这一档（2026-10-14 事故）：`connect()` 每次打开都跑一遍 init_db
    （CREATE TABLE / ALTER TABLE 都是**写操作**），于是"读一条列表"也算写库。
    自动同步那一轮把 `_db_lock` 按住十几分钟，界面侧的读接口又都排在同一把锁后面，
    浏览器等到超时就把「我的订阅」渲染成 0 个——号主看到的"数据全没了"是这么来的。
    读一律走这个口：`mode=ro` 打不开就报错，绝不会顺手改库结构。
    文件还不存在时退回 `connect()`（建一张空表再读，行为和从前一致，
    免得 `_alive_subs` 这类没做存在性判断的调用点在新机器上抛异常）。
    """
    path = Path(db_path)
    if not path.exists():
        return connect(path)
    uri = "file:%s?mode=ro" % str(path).replace("?", "%3f")
    conn = sqlite3.connect(uri, uri=True, timeout=DB_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aweme_id TEXT,
            source_sheet TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            source_url TEXT,
            video_url TEXT,
            cover_url TEXT,
            author TEXT,
            title TEXT,
            status TEXT,
            published_at TEXT,
            tags TEXT,
            transcript TEXT,
            ai_copy TEXT,
            keywords TEXT,
            remark TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(source_sheet, source_row)
        );

        CREATE INDEX IF NOT EXISTS idx_videos_aweme_id ON videos(aweme_id);
        CREATE INDEX IF NOT EXISTS idx_videos_author ON videos(author);
        CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);

        CREATE TABLE IF NOT EXISTS ai_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            summary_type TEXT NOT NULL,
            title TEXT NOT NULL,
            outline TEXT,
            content TEXT,
            keywords TEXT,
            structured_data TEXT,
            model TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            source_video_ids TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ai_summaries_type ON ai_summaries(summary_type);
        CREATE INDEX IF NOT EXISTS idx_ai_summaries_video_id ON ai_summaries(video_id);
        """
    )
    _ensure_column(conn, "ai_summaries", "source_video_ids", "TEXT")
    _ensure_column(conn, "ai_summaries", "structured_data", "TEXT")
    # 分类打标字段（Excel 重同步不会覆盖这些列）
    _ensure_column(conn, "videos", "category", "TEXT")
    _ensure_column(conn, "videos", "ai_tags", "TEXT")
    _ensure_column(conn, "videos", "game", "TEXT")
    # 实体标识与来源（SQLite 权威源第一步：author_sec_uid 直查替代 URL 反查；
    # source 区分 single_link/subscription 入口。Excel 重同步同样不覆盖这两列）
    _ensure_column(conn, "videos", "author_sec_uid", "TEXT DEFAULT ''")
    _ensure_column(conn, "videos", "source", "TEXT DEFAULT ''")
    # B 站素材专用三列：抖音/微信用不到（值为空），但选题排序与素材库卡片要显示
    # 时长与播放量，字幕状态还得区分「字幕 / AI字幕 / 无字幕 / 无字幕（仅简介）」，
    # 让写手知道这条素材的正文是从哪来的。
    _ensure_column(conn, "videos", "view_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "videos", "duration_sec", "INTEGER DEFAULT 0")
    _ensure_column(conn, "videos", "subs_status", "TEXT DEFAULT ''")
    # 抖音图文专用两列：图不是抽帧、也没有转写，正文只有作者自己写的文案，
    # 所以图要单独存路径（JSON 数组，落 output/douyin_notes/<aweme_id>/，界面走 /media/ 读）。
    _ensure_column(conn, "videos", "note_images", "TEXT DEFAULT ''")
    _ensure_column(conn, "videos", "note_image_count", "INTEGER DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sec_uid TEXT UNIQUE NOT NULL,
            user_url TEXT NOT NULL,
            author TEXT DEFAULT '',
            category TEXT DEFAULT '',
            game TEXT DEFAULT '',
            auto_asr INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_synced_at TEXT DEFAULT '',
            last_new_count INTEGER DEFAULT 0,
            last_sync_note TEXT DEFAULT ''
        )
        """
    )
    _ensure_column(conn, "subscriptions", "category", "TEXT DEFAULT ''")
    _ensure_column(conn, "subscriptions", "game", "TEXT DEFAULT ''")
    _ensure_column(conn, "subscriptions", "platform", "TEXT DEFAULT 'douyin'")
    # tags：用户自定义分组标签（逗号分隔）。与 category 正交——category 是喂
    # 选题雷达/渠道策略的权威口径，tags 只用于订阅页本地筛选与批量同步。
    _ensure_column(conn, "subscriptions", "tags", "TEXT DEFAULT ''")
    conn.commit()


def sync_excel_to_db(excel_path: str | Path, db_path: str | Path) -> dict[str, int]:
    # The sync code performs many random cell lookups. In openpyxl read_only mode
    # each ws.cell access can re-parse worksheet XML, which is painfully slow for
    # ASR-heavy sheets. The workbook is small enough to load normally here.
    wb = openpyxl.load_workbook(str(excel_path), read_only=False, data_only=True)
    conn = connect(db_path)
    upserted = 0
    skipped = 0
    now = _now()

    try:
        for ws in wb.worksheets:
            headers = _header_map(ws)
            for row in range(2, ws.max_row + 1):
                source_url = _cell(ws, row, "链接", headers)
                title = _cell(ws, row, "标题", headers)
                transcript = (
                    _cell(ws, row, "口播原文", headers)
                    or _cell(ws, row, "视频文案ASR", headers)
                )
                if not source_url and not title and not transcript:
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO videos (
                        aweme_id, source_sheet, source_row, source_url, video_url,
                        cover_url, author, title, status, published_at, tags,
                        transcript, ai_copy, keywords, remark, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_sheet, source_row) DO UPDATE SET
                        aweme_id=excluded.aweme_id,
                        source_url=excluded.source_url,
                        video_url=excluded.video_url,
                        cover_url=excluded.cover_url,
                        author=excluded.author,
                        title=excluded.title,
                        status=excluded.status,
                        published_at=excluded.published_at,
                        tags=excluded.tags,
                        transcript=excluded.transcript,
                        ai_copy=excluded.ai_copy,
                        keywords=excluded.keywords,
                        remark=excluded.remark,
                        updated_at=excluded.updated_at
                    """,
                    (
                        _cell(ws, row, "视频ID", headers),
                        ws.title,
                        row,
                        source_url,
                        _cell(ws, row, "视频链接", headers),
                        _cell(ws, row, "封面URL", headers),
                        _cell(ws, row, "作者", headers),
                        title,
                        _cell(ws, row, "状态", headers),
                        _cell(ws, row, "发布时间", headers),
                        _cell(ws, row, "标签", headers),
                        transcript,
                        _cell(ws, row, "AI优化文案", headers),
                        _cell(ws, row, "关键词摘要", headers),
                        _cell(ws, row, "备注", headers),
                        now,
                    ),
                )
                upserted += 1
        conn.commit()
    finally:
        conn.close()
        wb.close()

    return {"upserted": upserted, "skipped": skipped}


def backfill_author_sec_uid_from_subscriptions(db_path: str | Path) -> int:
    """幂等回填：按作者名把订阅的 sec_uid 写进 videos.author_sec_uid。

    只填空值（COALESCE 语义：显式标记过的不动），历史数据由此摆脱
    "author↔sec_uid 靠视频 URL 反查"（慢且依赖 cookie/链接有效性）。
    能对上抖音订阅的存量视频同时标记 source='subscription'。
    每次订阅同步后调用也安全。
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE videos SET
                author_sec_uid = (SELECT s.sec_uid FROM subscriptions s
                    WHERE s.author = videos.author
                      AND COALESCE(s.platform, 'douyin') = 'douyin'
                    LIMIT 1),
                source = 'subscription'
            WHERE COALESCE(author_sec_uid, '') = ''
              AND COALESCE(source, '') = ''
              AND COALESCE(source_sheet, '') NOT IN ('微信文章', 'B站视频', '抖音图文')
              AND EXISTS (SELECT 1 FROM subscriptions s
                    WHERE s.author = videos.author
                      AND COALESCE(s.platform, 'douyin') = 'douyin')
            """
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def mark_video_source(
    db_path: str | Path,
    sheet: str,
    rows: list[int],
    source: str,
    author_sec_uid: str = "",
) -> int:
    """写入口按行号标记视频来源（single_link / subscription），可顺带回填作者 sec_uid。

    在 sync_excel_to_db 之后调用（SQLite 已有该 sheet+row 的行）。
    """
    if not rows:
        return 0
    conn = connect(db_path)
    try:
        n = 0
        for row in rows:
            if author_sec_uid:
                cur = conn.execute(
                    "UPDATE videos SET source = ?, author_sec_uid = ? "
                    "WHERE source_sheet = ? AND source_row = ?",
                    (source, author_sec_uid, sheet, int(row)),
                )
            else:
                cur = conn.execute(
                    "UPDATE videos SET source = ? "
                    "WHERE source_sheet = ? AND source_row = ?",
                    (source, sheet, int(row)),
                )
            n += cur.rowcount or 0
        conn.commit()
        return n
    finally:
        conn.close()


def get_author_sec_uid(db_path: str | Path, author: str) -> str:
    """从 videos 表直查作者 sec_uid（回填后替代 URL 反查），查不到返回空串。"""
    if not author:
        return ""
    conn = open_readonly(db_path)
    try:
        row = conn.execute(
            "SELECT author_sec_uid FROM videos "
            "WHERE author = ? AND COALESCE(author_sec_uid, '') != '' "
            "ORDER BY id DESC LIMIT 1",
            (author,),
        ).fetchone()
        return (row["author_sec_uid"] or "") if row else ""
    finally:
        conn.close()


def _summary_where(summary_type: str = "", query: str = "") -> tuple:
    """整理稿列表的筛选条件，list / count 共用一份——两边不一致的话页码会骗人
    （显示"共 120 篇"实际能翻出 80 篇，是这类 bug 的标准长相）。"""
    where, params = [], []
    if summary_type:
        where.append("s.summary_type = ?")
        params.append(summary_type)
    if query:
        like = f"%{query}%"
        where.append(
            "(s.title LIKE ? OR s.content LIKE ? OR v.title LIKE ? OR v.author LIKE ?)"
        )
        params.extend([like, like, like, like])
    clause = "WHERE " + " AND ".join(where) if where else ""
    return clause, params


def count_summaries(
    db_path: str | Path,
    summary_type: str = "",
    query: str = "",
) -> int:
    conn = connect(db_path)
    try:
        clause, params = _summary_where(summary_type, query)
        row = conn.execute(
            f"""SELECT COUNT(*) AS c FROM ai_summaries s
                JOIN videos v ON v.id = s.video_id {clause}""",
            params,
        ).fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


def list_summaries(
    db_path: str | Path,
    summary_type: str = "",
    query: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        clause, params = _summary_where(summary_type, query)
        rows = conn.execute(
            f"""
            SELECT
                s.id, s.summary_type, s.title, s.outline, s.content, s.keywords,
                s.structured_data,
                s.model, s.status, s.source_video_ids, s.created_at, s.updated_at,
                v.id AS video_id, v.aweme_id, v.author, v.title AS video_title,
                v.source_sheet, v.source_row, v.video_url
            FROM ai_summaries s
            JOIN videos v ON v.id = s.video_id
            {clause}
            ORDER BY s.updated_at DESC, s.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, max(0, int(offset or 0))],
        ).fetchall()
        return [_dict(row) for row in rows]
    finally:
        conn.close()


def material_source_text(db_path: str | Path, summary: dict[str, Any]) -> str:
    """整理稿对应的源素材文本（标题 + 标签 + 口播正文），供数字与金句回指比对。

    标题和标签要一起进：很多数字来自标题（如「53K星」），只比对口播会把正确加工判成编造。
    合集稿的源视频存在 source_video_ids，也要一起取。
    """
    ids = set()
    if summary.get("video_id"):
        ids.add(int(summary["video_id"]))
    try:
        ids |= {int(x) for x in json.loads(summary.get("source_video_ids") or "[]")}
    except (ValueError, TypeError):
        pass
    if not ids:
        return ""
    parts = []
    for v in get_videos_by_ids(db_path, sorted(ids)):
        parts.append(" ".join(str(v.get(k) or "") for k in ("title", "tags", "ai_tags", "transcript")))
    return " ".join(parts)


def get_summary(db_path: str | Path, summary_id: int) -> dict[str, Any] | None:
    conn = open_readonly(db_path)
    try:
        row = conn.execute(
            """
            SELECT
                s.*, v.aweme_id, v.author, v.title AS video_title,
                v.source_sheet, v.source_row, v.video_url, v.transcript
            FROM ai_summaries s
            JOIN videos v ON v.id = s.video_id
            WHERE s.id = ?
            """,
            (summary_id,),
        ).fetchone()
        return _dict(row) if row else None
    finally:
        conn.close()


def get_video_by_source(
    db_path: str | Path,
    sheet: str | None = None,
    row: int | None = None,
    video_id: int | None = None,
) -> dict[str, Any] | None:
    conn = open_readonly(db_path)
    try:
        if video_id is not None:
            result = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        else:
            result = conn.execute(
                "SELECT * FROM videos WHERE source_sheet = ? AND source_row = ?",
                (sheet, row),
            ).fetchone()
        return _dict(result) if result else None
    finally:
        conn.close()


def get_videos_by_ids(db_path: str | Path, video_ids: list[int]) -> list[dict[str, Any]]:
    if not video_ids:
        return []
    conn = connect(db_path)
    try:
        placeholders = ",".join("?" for _ in video_ids)
        rows = conn.execute(
            f"SELECT * FROM videos WHERE id IN ({placeholders})",
            video_ids,
        ).fetchall()
        by_id = {int(row["id"]): _dict(row) for row in rows}
        return [by_id[video_id] for video_id in video_ids if video_id in by_id]
    finally:
        conn.close()


def search_videos(
    db_path: str | Path,
    keyword: str,
    game: str = "",
    category: str = "",
    limit: int = 80,
) -> list[dict[str, Any]]:
    """按关键词捞素材（标题 / ai_tags / 转写正文），选题包用它把沉睡素材一次捞出来。

    关键词命中转写正文很宽，所以只取 LIKE 匹配、按发布时间倒序、带条数上限，
    不做相关度排序——这是给人挑素材用的，不是给人直接成稿用的。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    sql = "SELECT * FROM videos WHERE (title LIKE ? OR ai_tags LIKE ? OR transcript LIKE ?)"
    args = ["%%%s%%" % kw] * 3
    if game:
        sql += " AND game = ?"
        args.append(game)
    if category:
        sql += " AND category = ?"
        args.append(category)
    sql += " ORDER BY published_at DESC LIMIT ?"
    args.append(int(limit))
    conn = open_readonly(db_path)
    try:
        return [_dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def used_video_ids(db_path: str | Path) -> set[int]:
    """进过整理稿的素材 id 集合——选题包用它标「★ 没用过」。"""
    conn = open_readonly(db_path)
    try:
        rows = conn.execute("SELECT source_video_ids FROM ai_summaries").fetchall()
    finally:
        conn.close()
    used: set[int] = set()
    for row in rows:
        try:
            used |= {int(x) for x in json.loads(row["source_video_ids"] or "[]")}
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return used


def save_summary(
    db_path: str | Path,
    video_id: int,
    summary_type: str,
    result: dict[str, Any],
    model: str = "",
    status: str = "draft",
    source_video_ids: list[int] | None = None,
) -> int:
    conn = connect(db_path)
    now = _now()
    try:
        cursor = conn.execute(
            """
            INSERT INTO ai_summaries (
                video_id, summary_type, title, outline, content, keywords, structured_data,
                model, status, source_video_ids, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                summary_type,
                result.get("title", ""),
                _json_or_text(result.get("outline", "")),
                result.get("content", ""),
                _json_or_text(result.get("keywords", "")),
                _json_or_text(_structured_payload(result)),
                model,
                status,
                _json_or_text(source_video_ids or [video_id]),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def update_summary(
    db_path: str | Path,
    summary_id: int,
    result: dict[str, Any],
    model: str = "",
    status: str = "draft",
    source_video_ids: list[int] | None = None,
) -> None:
    conn = connect(db_path)
    now = _now()
    try:
        conn.execute(
            """
            UPDATE ai_summaries SET
                title=?, outline=?, content=?, keywords=?, structured_data=?,
                model=?, status=?, source_video_ids=COALESCE(?, source_video_ids), updated_at=?
            WHERE id=?
            """,
            (
                result.get("title", ""),
                _json_or_text(result.get("outline", "")),
                result.get("content", ""),
                _json_or_text(result.get("keywords", "")),
                _json_or_text(_structured_payload(result)),
                model,
                status,
                _json_or_text(source_video_ids) if source_video_ids is not None else None,
                now,
                summary_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()



def delete_summary(db_path: str | Path, summary_id: int) -> None:
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM ai_summaries WHERE id = ?", (summary_id,))
        conn.commit()
    finally:
        conn.close()


def delete_video(db_path: str | Path, sheet: str, row: int) -> bool:
    """删除 SQLite 中的视频及其直接关联的整理稿（合并稿保留，仅清理引用）。"""
    conn = connect(db_path)
    try:
        r = conn.execute(
            "SELECT id FROM videos WHERE source_sheet = ? AND source_row = ?", (sheet, row)
        ).fetchone()
        if not r:
            return False
        vid = r["id"]
        conn.execute("DELETE FROM ai_summaries WHERE video_id = ?", (vid,))
        # 合并稿 source_video_ids 里清理该视频引用
        for s in conn.execute(
            "SELECT id, source_video_ids FROM ai_summaries WHERE source_video_ids IS NOT NULL"
        ).fetchall():
            try:
                vids = json.loads(s["source_video_ids"] or "[]")
            except (TypeError, ValueError):
                continue
            if vid in vids:
                vids = [x for x in vids if x != vid]
                if not vids:
                    conn.execute("DELETE FROM ai_summaries WHERE id = ?", (s["id"],))
                else:
                    conn.execute(
                        "UPDATE ai_summaries SET source_video_ids = ? WHERE id = ?",
                        (json.dumps(vids), s["id"]),
                    )
        conn.execute("DELETE FROM videos WHERE id = ?", (vid,))
        conn.commit()
        return True
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# 订阅（作者主页增量同步）
# ════════════════════════════════════════════════════════════════

def add_subscription(
    db_path: str | Path,
    sec_uid: str,
    user_url: str,
    author: str = "",
    category: str = "",
    game: str = "",
    platform: str = "douyin",
) -> dict[str, Any]:
    """添加/更新订阅，返回订阅记录。"""
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO subscriptions (sec_uid, user_url, author, category, game, platform, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sec_uid) DO UPDATE SET
                user_url = excluded.user_url,
                author = CASE WHEN excluded.author != '' THEN excluded.author ELSE subscriptions.author END,
                category = CASE WHEN excluded.category != '' THEN excluded.category ELSE subscriptions.category END,
                game = CASE WHEN excluded.game != '' THEN excluded.game ELSE subscriptions.game END,
                platform = excluded.platform
            """,
            (sec_uid, user_url, author, category, game, platform, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE sec_uid = ?", (sec_uid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_subscription_author(db_path: str | Path, sub_id: int, author: str) -> bool:
    """修改订阅作者显示名。"""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE subscriptions SET author = ? WHERE id = ?", (author.strip(), sub_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_subscriptions_category(
    db_path: str | Path,
    sub_ids: list[int],
    category: str,
    game: str = "",
) -> int:
    """批量设置订阅分类/游戏，返回更新条数。game 为空时保留原值。"""
    if not sub_ids:
        return 0
    placeholders = ",".join("?" for _ in sub_ids)
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE subscriptions SET category = ?, "
            "game = CASE WHEN ? != '' THEN ? ELSE game END "
            f"WHERE id IN ({placeholders})",
            (category, game, game, *sub_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def normalize_tags(raw: str) -> str:
    """订阅标签规范化：接受中英文逗号/分号/空格分隔，去空去重后以逗号连接。"""
    parts = [p.strip() for p in re.split(r"[,，;；\s]+", raw or "") if p.strip()]
    unique: list[str] = []
    for p in parts:
        if p not in unique:
            unique.append(p)
    return ",".join(unique)


def set_subscription_tags(db_path: str | Path, sub_ids: list[int], tags: str) -> int:
    """批量覆盖订阅标签（逗号分隔）；传空串即清空，返回更新条数。"""
    if not sub_ids:
        return 0
    placeholders = ",".join("?" for _ in sub_ids)
    conn = connect(db_path)
    try:
        cur = conn.execute(
            f"UPDATE subscriptions SET tags = ? WHERE id IN ({placeholders})",
            (tags, *sub_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_subscriptions(db_path: str | Path) -> list[dict[str, Any]]:
    conn = open_readonly(db_path)
    try:
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def subscription_cleanup_targets(db_path: str | Path, sec_uid: str, author: str = "") -> dict:
    """列出一个订阅名下的素材与整理稿（取消订阅时连带清理用）。

    返回 {"videos":[{id,sheet,row,aweme_id}], "summary_ids":[...]}

    两条口径要记着：
    1. author 只在非空时参与匹配。订阅刚加、还没同步出昵称时 author=''，
       这时若按 `author = videos.author` 匹配会空对空命中所有无作者的历史空行，
       把一堆垃圾行认领成这个号的素材（2026-10-16 实测 15 行）。
    2. 合集稿按号主拍板「引用到就整篇删」——不剔除单个 id，只要 source_video_ids
       里出现任何一个被删素材，整篇进删除清单。
    """
    conn = connect(db_path)
    try:
        conds = ["COALESCE(author_sec_uid, '') = ?"]
        params: list[Any] = [sec_uid]
        if (author or "").strip():
            conds.append("COALESCE(author, '') = ?")
            params.append(author.strip())
        where = " OR ".join(conds)
        videos = [
            {"id": r["id"], "sheet": r["source_sheet"], "row": r["source_row"],
             "aweme_id": r["aweme_id"] or ""}
            for r in conn.execute(
                f"SELECT id, source_sheet, source_row, aweme_id FROM videos WHERE {where} "
                "ORDER BY id", params).fetchall()
        ]
        vid_set = {v["id"] for v in videos}
        sids: set[int] = set()
        if vid_set:
            marks = ",".join("?" for _ in vid_set)
            for r in conn.execute(
                f"SELECT id FROM ai_summaries WHERE video_id IN ({marks})",
                list(vid_set)).fetchall():
                sids.add(r["id"])
            for r in conn.execute(
                "SELECT id, source_video_ids FROM ai_summaries "
                "WHERE COALESCE(source_video_ids, '') <> ''").fetchall():
                try:
                    refs = json.loads(r["source_video_ids"] or "[]")
                except ValueError:
                    continue
                if any(str(i).strip().isdigit() and int(i) in vid_set for i in refs):
                    sids.add(r["id"])
        return {"videos": videos, "summary_ids": sorted(sids)}
    finally:
        conn.close()


def delete_subscription(db_path: str | Path, sub_id: int) -> bool:
    conn = connect(db_path)
    try:
        cur = conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_subscription_sync(
    db_path: str | Path,
    sub_id: int,
    new_count: int,
    note: str,
    author: str = "",
) -> None:
    conn = connect(db_path)
    try:
        # author 只在订阅原本没有昵称时才补：**不许覆盖**。
        # 2026-10-16 实测：同步收尾拿的是"本轮第一条转写行的作者列"，而主页 DOM 里混着
        # 侧栏推荐位别人的视频，于是「笨小孩」这条订阅被改成了「厦门阿波」——
        # 号主下次看到的订阅名就不是他订的那个号了。
        conn.execute(
            """
            UPDATE subscriptions SET
                last_synced_at = ?, last_new_count = ?, last_sync_note = ?,
                author = CASE WHEN ? != '' AND COALESCE(author, '') = '' THEN ? ELSE author END
            WHERE id = ?
            """,
            (_now(), new_count, note, author, author, sub_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_wechat_articles(
    db_path: str | Path,
    articles: list[dict[str, Any]],
    category: str = "",
    game: str = "",
) -> list[dict[str, Any]]:
    """将微信文章写入 videos 表，返回已插入的记录列表。

    每篇文章的 transcript 字段存正文 Markdown，跳过 ASR。
    用 aweme_id（URL hash）去重，source_sheet='微信文章'。
    """
    if not articles:
        return []
    conn = connect(db_path)
    inserted: list[dict[str, Any]] = []
    try:
        # 查已有 aweme_id（去重）
        existing = {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = '微信文章' AND aweme_id IS NOT NULL"
            ).fetchall()
        }
        # 下一行号
        max_row = conn.execute(
            "SELECT COALESCE(MAX(source_row), 0) FROM videos WHERE source_sheet = '微信文章'"
        ).fetchone()[0]

        for idx, art in enumerate(articles):
            if art["aweme_id"] in existing:
                continue
            row_num = max_row + idx + 1
            g = game if category == "游戏攻略" else ""
            conn.execute(
                """
                INSERT INTO videos (
                    aweme_id, source_sheet, source_row, source_url,
                    author, title, status, published_at,
                    transcript, category, game, updated_at
                ) VALUES (?, '微信文章', ?, ?, ?, ?, '已完成', ?, ?, ?, ?, ?)
                """,
                (
                    art["aweme_id"], row_num, art["url"],
                    art["author"], art["title"],
                    art["published_at"], art["transcript"],
                    category, g, _now(),
                ),
            )
            inserted.append({**art, "source_sheet": "微信文章", "source_row": row_num})
        conn.commit()
    finally:
        conn.close()
    return inserted


def get_wechat_article_links(db_path: str | Path) -> set[str]:
    """返回已入库的微信文章 URL 集合（用于同步去重）。"""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source_url FROM videos WHERE source_sheet = '微信文章' AND source_url IS NOT NULL"
        ).fetchall()
        return {r["source_url"] for r in rows}
    finally:
        conn.close()


# ── B 站投稿入库（与微信文章同一套落点：DB 直写、不进 Excel、行号自增） ──

BILI_SHEET = "B站视频"


def insert_bilibili_videos(
    db_path: str | Path,
    items: list[dict[str, Any]],
    author: str = "",
    sec_uid: str = "",
    category: str = "",
    game: str = "",
) -> list[dict[str, Any]]:
    """将 B 站投稿写入 videos 表，返回实际插入的记录。

    去重键是 aweme_id（存真实 bvid，不像微信那样用 URL hash）。
    transcript 存字幕正文；没字幕时按 subs_status 标「无字幕」，
    正文空的那条 status 记「未开始」（沿用库里已有取值，不新造状态）。
    """
    if not items:
        return []
    conn = connect(db_path)
    inserted: list[dict[str, Any]] = []
    try:
        existing = {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL",
                (BILI_SHEET,),
            ).fetchall()
        }
        max_row = conn.execute(
            "SELECT COALESCE(MAX(source_row), 0) FROM videos WHERE source_sheet = ?",
            (BILI_SHEET,),
        ).fetchone()[0]
        for idx, art in enumerate(items):
            bvid = art.get("aweme_id") or ""
            if not bvid or bvid in existing:
                continue
            row_num = max_row + idx + 1
            body = art.get("transcript") or ""
            g = game if category == "游戏攻略" else ""
            conn.execute(
                """
                INSERT INTO videos (
                    aweme_id, source_sheet, source_row, source_url, cover_url,
                    author, title, status, published_at, transcript,
                    category, game, source, author_sec_uid,
                    view_count, duration_sec, subs_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'subscription', ?, ?, ?, ?, ?)
                """,
                (
                    bvid, BILI_SHEET, row_num, art.get("url") or "",
                    art.get("cover_url") or "",
                    author or (art.get("author") or ""), art.get("title") or "",
                    "已完成" if body else "未开始",
                    art.get("published_at") or "", body,
                    category, g, sec_uid or "",
                    int(art.get("view_count") or 0), int(art.get("duration_sec") or 0),
                    art.get("subs_status") or "", _now(),
                ),
            )
            inserted.append({**art, "source_sheet": BILI_SHEET, "source_row": row_num})
        conn.commit()
    finally:
        conn.close()
    return inserted


# ── 暴雪国服官方公告入库（第三个"DB 直写、不进 Excel"的源，落点与微信/B站同一套） ──

OFFICIAL_NEWS_SHEET = "暴雪官方"


def insert_official_news(db_path, items, category="", game=""):
    """把官网公告写进 videos 表，返回实际插入的记录。

    去重键是 aweme_id（`bn` + URL 的 md5 前 16 位，官方公告没有原生 id）。
    正文空的条目 status 记「未开始」，不拿导航文案凑数——沿用库里已有取值。
    """
    if not items:
        return []
    conn = connect(db_path)
    inserted = []
    try:
        existing = {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL",
                (OFFICIAL_NEWS_SHEET,),
            ).fetchall()
        }
        max_row = conn.execute(
            "SELECT COALESCE(MAX(source_row), 0) FROM videos WHERE source_sheet = ?",
            (OFFICIAL_NEWS_SHEET,),
        ).fetchone()[0]
        stamp = _now()
        row_num = max_row
        for art in items:
            aid = art.get("aweme_id") or ""
            if not aid or aid in existing:
                continue
            row_num += 1
            body = art.get("transcript") or ""
            conn.execute(
                """
                INSERT INTO videos (
                    aweme_id, source_sheet, source_row, source_url, cover_url,
                    author, title, status, published_at, transcript,
                    category, game, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'official', ?)
                """,
                (aid, OFFICIAL_NEWS_SHEET, row_num, art.get("url") or "",
                 art.get("cover_url") or "", art.get("author") or "魔兽世界运营团队",
                 art.get("title") or "", "已完成" if body else "未开始",
                 art.get("published_at") or "", body, category, game, stamp),
            )
            existing.add(aid)
            inserted.append({**art, "source_sheet": OFFICIAL_NEWS_SHEET, "source_row": row_num})
        conn.commit()
    finally:
        conn.close()
    return inserted


def official_news_urls(db_path):
    """已入库的官方公告 URL 集合（同步去重用）。"""
    conn = connect(db_path)
    try:
        return {r["source_url"] for r in conn.execute(
            "SELECT source_url FROM videos WHERE source_sheet = ? AND source_url IS NOT NULL",
            (OFFICIAL_NEWS_SHEET,)
        ).fetchall()}
    finally:
        conn.close()


def video_ids_by_rows(db_path: str | Path, pairs: list) -> list[int]:
    """按 (source_sheet, source_row) 精确取 video id。

    同步收尾只给本轮新增的那批打标用——classify_videos 不带范围时会把全库缺口
    一起过一遍 LLM，花多少没人盯着。
    """
    if not pairs:
        return []
    conn = connect(db_path)
    try:
        # 不用 (source_sheet, source_row) IN ((?,?),…)：SQLite 会报
        # 「row value misused」（行值语法不支持带占位符的元组列表），只能展开成 OR
        conds = " OR ".join("(source_sheet = ? AND source_row = ?)" for _ in pairs)
        params: list[Any] = []
        for sheet, row in pairs:
            params.extend([sheet, int(row)])
        rows = conn.execute(
            f"SELECT id FROM videos WHERE {conds} ORDER BY id", params
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def get_bilibili_bvids(db_path: str | Path) -> set[str]:
    """返回已入库的 bvid 集合（增量同步的比对基线）。"""
    conn = open_readonly(db_path)
    try:
        return {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL",
                (BILI_SHEET,),
            ).fetchall()
            if r["aweme_id"]
        }
    finally:
        conn.close()


# ── 抖音图文入库（第四种源：DB 直写、不进 Excel；和 B 站同一套落点） ──
#
# 为什么要单开一档而不是混在「抖音视频数据」里：图文（aweme_type=68，作品页锚点是
# /note/<id>）没有语音可转写，素材的全部正文就是作者自己写的文案 + 几张图。
# 和视频同档的话，筛选出来的条目有一半没有转写，写手与选题雷达都会被"没正文"污染。
# 号主 2026-10-16 拍板：来源栏单独一档「抖音图文」，图片下载到本地（抖音图链带签名、
# 约一个月过期，不存盘就看不到了）。

NOTE_SHEET = "抖音图文"


def get_douyin_note_ids(db_path: str | Path) -> set[str]:
    """已入库的图文 aweme_id 集合（增量同步的比对基线）。"""
    conn = open_readonly(db_path)
    try:
        return {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL",
                (NOTE_SHEET,),
            ).fetchall()
            if r["aweme_id"]
        }
    finally:
        conn.close()


def insert_douyin_notes(
    db_path: str | Path,
    items: list[dict[str, Any]],
    author: str = "",
    sec_uid: str = "",
    category: str = "",
    game: str = "",
) -> list[dict[str, Any]]:
    """把抖音图文写进 videos 表，返回实际插入的记录（含 source_row）。

    items 每条要有 aweme_id / title（作者文案）/ url，可选 images（本地图片相对
    output/ 的路径列表）、cover_url、published_at、stats_note。
    去重键是 aweme_id；status 沿用库里已有取值——有文案记「已完成」、纯图集没文案的
    记「未开始」（等 OCR 或人工补正文），不新造状态值。
    图文没有播放量，所以 stats_note（"赞2649·评65·转645"）落 remark：这一档是
    DB 直写、Excel 重同步永远碰不到这些行，不会和抖音那侧的"失败原因"撞车。
    """
    if not items:
        return []
    conn = connect(db_path)
    inserted: list[dict[str, Any]] = []
    try:
        existing = {
            r["aweme_id"]
            for r in conn.execute(
                "SELECT aweme_id FROM videos WHERE source_sheet = ? AND aweme_id IS NOT NULL",
                (NOTE_SHEET,),
            ).fetchall()
            if r["aweme_id"]
        }
        max_row = conn.execute(
            "SELECT COALESCE(MAX(source_row), 0) FROM videos WHERE source_sheet = ?",
            (NOTE_SHEET,),
        ).fetchone()[0]
        for idx, art in enumerate(items):
            aweme_id = art.get("aweme_id") or ""
            if not aweme_id or aweme_id in existing:
                continue
            row_num = max_row + idx + 1
            body = art.get("title") or ""
            images = art.get("images") or []
            g = game if category == "游戏攻略" else ""
            conn.execute(
                """
                INSERT INTO videos (
                    aweme_id, source_sheet, source_row, source_url, cover_url,
                    author, title, status, published_at, transcript, remark,
                    category, game, source, author_sec_uid,
                    note_images, note_image_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'subscription', ?, ?, ?, ?)
                """,
                (
                    aweme_id, NOTE_SHEET, row_num, art.get("url") or "",
                    art.get("cover_url") or "",
                    author or (art.get("author") or ""), body,
                    "已完成" if body else "未开始",
                    art.get("published_at") or "",
                    # 文案同时进 title 和 transcript：列表要有一行能读懂的标题，而下游
                    # （字数、复制正文、整理稿取材）统一读 transcript。图文没有第二条文本，
                    # 抄成两份比让 transcript 空着好——空着的话这条素材在界面上就是"无正文"。
                    body,
                    art.get("stats_note") or "",
                    category, g, sec_uid or "",
                    json.dumps(images, ensure_ascii=False), len(images), _now(),
                ),
            )
            inserted.append({**art, "source_sheet": NOTE_SHEET, "source_row": row_num})
        conn.commit()
    finally:
        conn.close()
    return inserted


def pending_bili_transcripts(db_path: str | Path, limit: int = 20,
                             season_author: str = "", max_seconds: int = 0) -> list:
    """列出正文还空着的 B 站素材（合集整档入库后就是这个状态），从新往老。

    season_author 非空时只取这个 UP 主的（号主按合集补转写，不该顺手把别的号也排进去）。
    max_seconds 非 0 时按条长截一刀：这个合集里最长的一条是 15018 秒（4 小时20 分的直播录像），
    不截的话"补 20 条"里有几条一条就能吃掉一小时机器时间。
    """
    cap = max(1, int(limit or 20))
    conn = open_readonly(db_path)
    try:
        sql = ("SELECT id, aweme_id, title, author, duration_sec, published_at, subs_status "
               "FROM videos WHERE source_sheet = ? AND COALESCE(transcript, '') = ''")
        params: list = [BILI_SHEET]
        if season_author:
            sql += " AND COALESCE(author, '') = ?"
            params.append(season_author)
        if int(max_seconds or 0) > 0:
            sql += " AND COALESCE(duration_sec, 0) <= ?"
            params.append(int(max_seconds))
        sql += " ORDER BY published_at DESC, id DESC LIMIT ?"
        params.append(cap)
        return [_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def pending_bili_frames(db_path: str | Path, limit: int = 20,
                        season_author: str = "", max_seconds: int = 0) -> list:
    """列出**已有正文**的 B 站素材，给"只补关键帧"那档用（从新往老）。

    "有没有帧池"是文件系统上的事，SQL 问不出来，所以这里只按正文筛，真跳过在作业里做
    （`frame_pool.load(bvid)` 已经抽过的就不重抽）。
    为什么要有这一条：B 站补转写只下音频、抽完就删片，那批素材之后**没有任何入口能补画面**
    ——「补画面」那颗只认抖音那条链的 aweme_id。号主 11-01 点名的就是这个洞。
    """
    cap = max(1, int(limit or 20))
    conn = open_readonly(db_path)
    try:
        sql = ("SELECT id, aweme_id, title, author, duration_sec, published_at, subs_status "
               "FROM videos WHERE source_sheet = ? AND LENGTH(COALESCE(transcript, '')) >= 200")
        params: list = [BILI_SHEET]
        if season_author:
            sql += " AND COALESCE(author, '') = ?"
            params.append(season_author)
        if int(max_seconds or 0) > 0:
            sql += " AND COALESCE(duration_sec, 0) <= ?"
            params.append(int(max_seconds))
        sql += " ORDER BY published_at DESC, id DESC LIMIT ?"
        params.append(cap)
        return [_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def bili_pending_authors(db_path: str | Path, max_seconds: int = 0) -> list:
    """按 UP 主统计"正文还空着"的 B 站素材：`[{author, n, last}]`，按**最新的积压**排。

    自动链的排队判据需要它。原来只把"这一轮抓到新料"的号排进队列，于是
    今天没更新的号那 324 条积压（好赛雷切片，11-02 实测）一晚上都排不到一次，
    积压不会自己变小。这里给出"还有哪些号有待转写的料、各多少条"，
    让一晚剩下的额度有地方花。
    `last` 用 published_at 的最大值：先啃"最近还在更新"的号，排产表才吃得到新鲜料。
    """
    conn = open_readonly(db_path)
    try:
        sql = ("SELECT COALESCE(author, '') AS author, COUNT(*) AS n,"
               " MAX(COALESCE(published_at, '')) AS last"
               " FROM videos WHERE source_sheet = ? AND COALESCE(transcript, '') = ''"
               " AND COALESCE(author, '') <> ''")
        params: list = [BILI_SHEET]
        if int(max_seconds or 0) > 0:
            # 只算这一档真能转写的条数：长录像根本不会下，按它排队等于把额度记在空账上
            sql += " AND COALESCE(duration_sec, 0) <= ?"
            params.append(int(max_seconds))
        sql += " GROUP BY author ORDER BY last DESC, n DESC"
        return [_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def set_video_transcript(db_path: str | Path, video_id: int, text: str,
                         subs_status: str = "ASR转写", sheet: str = BILI_SHEET) -> bool:
    """把转写结果写回某条素材。status 只认「有正文=已完成」这一条口径。

    subs_status 用「ASR转写」而不是「字幕」：B 站自带字幕轨和我们自己跑出来的转写
    必须分得开——前者是官方轨、错字少，后者是 whisper 认的、专有名词会错。
    写手看到「字幕」会以为可以直接引，看到「ASR转写」才知道要核。
    """
    body = (text or "").strip()
    if not body:
        return False
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE videos SET transcript = ?, status = '已完成', subs_status = ?, updated_at = ? "
            "WHERE id = ? AND source_sheet = ?",
            (body, subs_status, _now(), int(video_id), sheet),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def generate_summary(
    video: dict[str, Any],
    summary_type: str,
    ai_config: dict[str, str] | None = None,
    brief: dict | None = None,
) -> tuple[dict[str, Any], str, str]:
    ai_config = ai_config or {}
    method = (ai_config.get("method") or "skip").strip()
    api_key = ai_config.get("api_key", "")
    model = ai_config.get("model", "")
    if method != "skip" and api_key:
        ai_result = _generate_with_llm(video, summary_type, ai_config, brief)
        if ai_config.get("editorial"):
            from .editorial_model import validate_summary
            if not isinstance(ai_result, dict) or "error" in ai_result:
                raise ValueError("DeepSeek V4 Pro 整理失败：%s" %
                                 (ai_result.get("error") if isinstance(ai_result, dict) else "输出不是 JSON 对象"))
            validate_summary(ai_result, ai_config)
        if "error" not in ai_result:
            if summary_type == "ai_interview":
                ai_result = _normalize_interview_result(
                    ai_result,
                    fallback_title=f"AI面试题：{video.get('title') or '未命名视频'}",
                    source_lines=[f"{video.get('title') or '未命名视频'}（{video.get('author') or '未知作者'}）"],
                )
            return ai_result, model or method, "ai"
        fallback = _generate_local(video, summary_type)
        # 失败原因写进 structured_data，不再追加到正文：正文会被下游当素材抽进卡片，
        # 「[AI生成失败…Error code: 402]」曾被印到读者可见的成品图上（2026-09-30 事故）。
        # status=draft 已是权威信号，这里只补一份可排查的原因文本，不动正文。
        if isinstance(fallback.get("structured_data"), dict):
            fallback["structured_data"]["fallback_error"] = str(ai_result["error"])[:300]
        return fallback, model or method, "draft"
    return _generate_local(video, summary_type), "local-template", "draft"


def generate_collection_summary(
    videos: list[dict[str, Any]],
    summary_type: str,
    ai_config: dict[str, str] | None = None,
    brief: dict | None = None,
) -> tuple[dict[str, Any], str, str]:
    ai_config = ai_config or {}
    method = (ai_config.get("method") or "skip").strip()
    api_key = ai_config.get("api_key", "")
    model = ai_config.get("model", "")
    if method != "skip" and api_key:
        ai_result = _generate_collection_with_llm(videos, summary_type, ai_config, brief)
        if ai_config.get("editorial"):
            from .editorial_model import validate_summary
            if not isinstance(ai_result, dict) or "error" in ai_result:
                raise ValueError("DeepSeek V4 Pro 整理失败：%s" %
                                 (ai_result.get("error") if isinstance(ai_result, dict) else "输出不是 JSON 对象"))
            validate_summary(ai_result, ai_config)
        if "error" not in ai_result:
            if summary_type == "ai_interview":
                ai_result = _normalize_interview_result(
                    ai_result,
                    fallback_title="AI面试题合集",
                    source_lines=[
                        f"{video.get('title') or '未命名视频'}（{video.get('author') or '未知作者'}）"
                        for video in videos
                    ],
                )
            return ai_result, model or method, "ai"
        fallback = _generate_collection_local(videos, summary_type)
        # 失败原因写进 structured_data，不再追加到正文：正文会被下游当素材抽进卡片，
        # 「[AI生成失败…Error code: 402]」曾被印到读者可见的成品图上（2026-09-30 事故）。
        # status=draft 已是权威信号，这里只补一份可排查的原因文本，不动正文。
        if isinstance(fallback.get("structured_data"), dict):
            fallback["structured_data"]["fallback_error"] = str(ai_result["error"])[:300]
        return fallback, model or method, "draft"
    return _generate_collection_local(videos, summary_type), "local-template", "draft"


# 整理稿的模型超时。原来写死 300 秒，2026-10-10 实测 5 条素材的头条整合稿
# （qwen3.8-max，思考型）要 394 秒，直接撞线报 Request timed out；接实名参考之后提示词
# 又长了一截，只会更容易撞。放宽到 900 秒——前端有进度条与"已耗时"显示，慢不是问题，
# 静默失败才是。两处（单条 / 多条整合）共用这一个值。
LLM_TIMEOUT = 900


def _generate_with_llm(
    video: dict[str, Any],
    summary_type: str,
    ai_config: dict[str, str],
    brief: dict | None = None,
) -> dict[str, Any]:
    prompt = _prompt(video, summary_type, None if ai_config.get("editorial") else brief)
    if ai_config.get("editorial"):
        from .editorial_model import prompt_scope
        prompt += prompt_scope(brief)
    try:
        import openai
        from .editorial_model import chat_options

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=LLM_TIMEOUT,
            max_retries=0 if ai_config.get("editorial") else 1,
        )
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            **chat_options(ai_config, 0.25),
        )
        return _parse_llm_json(resp.choices[0].message.content)
    except Exception as exc:
        return {"error": str(exc)}


def _generate_collection_with_llm(
    videos: list[dict[str, Any]],
    summary_type: str,
    ai_config: dict[str, str],
    brief: dict | None = None,
) -> dict[str, Any]:
    prompt = _collection_prompt(videos, summary_type, None if ai_config.get("editorial") else brief)
    if ai_config.get("editorial"):
        from .editorial_model import prompt_scope
        prompt += prompt_scope(brief)
    try:
        import openai
        from .editorial_model import chat_options

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=LLM_TIMEOUT,
            max_retries=0 if ai_config.get("editorial") else 1,
        )
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            **chat_options(ai_config, 0.25),
        )
        return _parse_llm_json(resp.choices[0].message.content)
    except Exception as exc:
        return {"error": str(exc)}


def _parse_llm_json(text: Any) -> dict[str, Any]:
    """宽容解析 LLM 返回的 JSON：剥离 markdown 代码块、截取首个平衡的 {...}。"""
    text = str(text or "").strip()
    if not text:
        return {}
    # 剥离 ```json ... ``` 代码块包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 截取首个配平的花括号块（忽略字符串内的花括号）
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return {}
    return {}


# AI 面试/技术类场景：公众号素材档案按面试题解析文风组织（区别于游戏攻略文风）
INTERVIEW_CATEGORIES = {"前端面试", "AI技术", "编程开发"}


def _is_interview_scene(video: dict[str, Any]) -> bool:
    """判断视频是否属于 AI 面试场景：AI面试工作表来源，或分类为技术/面试类。"""
    if (video.get("source_sheet") or "").strip() == "AI面试":
        return True
    return (video.get("category") or "").strip() in INTERVIEW_CATEGORIES


def _generate_local(video: dict[str, Any], summary_type: str) -> dict[str, Any]:
    title = video.get("title") or "未命名视频"
    transcript = (video.get("transcript") or "").strip()
    tags = video.get("tags") or ""
    author = video.get("author") or ""
    sentences = _sentences(transcript)
    key_points = sentences[:6] or [transcript[:160] or title]
    keywords = _keywords(f"{title} {tags} {transcript[:1000]}")

    if summary_type == "ai_interview":
        out_title = f"AI面试题：{title[:40]}"
        concepts = _interview_concepts(title, tags, transcript)
        if concepts:
            keywords = concepts
            key_points = [
                f"{concept}：{_evidence_around(transcript, concept)}"
                for concept in concepts
            ]
        questions = _build_interview_questions(
            key_points=key_points,
            keywords=keywords,
            prefix=f"v{video.get('id') or 'x'}",
            source_title=title,
        )
        outline = ["题目", "答案解析", "考察点", "追问", "来源视频"]
        content = _questions_to_markdown(
            title=out_title,
            questions=questions,
            source_lines=[f"{title}（{author}）"],
            note="规则版草稿基于 ASR 自动抽题，入库前建议人工核对术语、选项和答案。"
        )
    elif summary_type == "wechat_material":
        # 公众号文章档（规则版草稿）：固定【标签】分节，下游按节解析
        # （graphics/channels/wechat.py 的本地兜底模板就靠这套分节拼卡）。
        # 2026-10-26 与 AI 提示词同步改成"完整文章"形态：这里**没有 AI 写不出真正文**，
        # 所以【正文】只给小节＋一句实话，体检会把它判成"正文只有小节"从而拦住发布，
        # 不许拿这种骨架冒充成稿。
        out_title = f"公众号文章（规则版骨架）：{title[:32]}"
        outline = ["【标题候选】", "【导语】", "【适用范围】", "【核心论点】", "【正文】",
                   "【关键数据】", "【金句摘录】", "【风险核查】", "【来源】"]
        data_lines = [s for s in sentences if re.search(r"\d+(\.\d+)?%|\d{2,}", s)][:5]
        if _is_interview_scene(video):
            # AI 面试场景：核心论点/正文小节按面试题解析组织，并抽技术概念做关键词
            concepts = _interview_concepts(title, tags, transcript)
            if concepts:
                keywords = concepts
                arguments = [
                    f"{c}：{_evidence_around(transcript, c)[:50]} [他述]"
                    for c in concepts[:3]
                ]
            else:
                arguments = [f"{s[:50]} [他述]" for s in sentences[:3]]
            skeleton = ("## 问题背景\n本节正文待写（规则版只给小节）\n"
                        "## 核心原理\n本节正文待写\n## 工程实践\n本节正文待写\n"
                        "## 考察点与追问\n本节正文待写")
            scope = "技术栈与框架版本：素材未给出，发前需核 [待核]"
        else:
            arguments = [f"{s[:50]} [他述]" for s in sentences[:3]]
            skeleton = ("## 事件背景\n本节正文待写（规则版只给小节）\n"
                        "## 关键细节\n本节正文待写\n## 玩家影响\n本节正文待写")
            scope = "游戏版本／服务器：素材未给出，发前需核 [待核]"
        content = (
            f"来源作者：{author}\n来源视频：{title}\n\n"
            "【标题候选】\n"
            f"1. {title[:40]}（配置 AI 后自动生成 3 个候选标题）\n\n"
            "【导语】\n"
            f"{(sentences[0] or title)[:40]}\n\n"
            "【适用范围】\n" + scope + "\n\n"
            "【核心论点】\n"
            + "\n".join(f"- {a}" for a in arguments)
            + "\n\n【正文】\n" + skeleton + "\n\n"
            "【关键数据】\n"
            + (("\n".join(f"- {s[:60]} [他述]" for s in data_lines) + "\n") if data_lines else "无\n")
            + "\n\n【金句摘录】\n"
            f"- {(sentences[1] if len(sentences) > 1 else '待补充')[:60]}（原话）\n\n"
            "【风险核查】\n"
            "- 规则版草稿未做事实核对，发布前核查所有数值/版本/时间线。\n"
            "- 待核项：本文【正文】还是小节骨架，没有可照做的步骤，**不能直接发**。"
            "配 AI 重新生成或由本人写正文后才能进体检。\n\n"
            "【来源】\n"
            f"- {author}《{title[:40]}》，{(video.get('published_at') or video.get('publish_date') or '日期未记录')[:10]}"
            f"（素材库 id={video.get('id') or '未入库'}）\n\n"
            "> 规则版只提供结构与骨架；配置 AI 后重新生成才会写出完整正文。"
        )
    elif summary_type == "guide_article":
        # 已废弃：成品文章由自媒体工作台生成，存量数据仅保留可读
        out_title = title[:40]
        outline = ["已废弃"]
        content = (
            f"来源作者：{author}\n\n"
            "> 该类型已废弃（成品文章由自媒体工作台生成）。存量草稿仅作参考，"
            "请改用「公众号文章素材」生成结构化素材档案。"
        )
    else:
        out_title = title[:40]
        outline = ["适用场景", "操作步骤", "关键机制", "避坑提醒"]
        content = (
            f"来源作者：{author}\n\n"
            "攻略要点：\n"
            + "\n".join(f"- {point}" for point in key_points)
            + "\n\n整理建议：把强结论、数值、版本时间重新核查后，再扩写成正式攻略。"
        )

    return {
        "title": out_title,
        "outline": outline,
        "content": content,
        "keywords": keywords,
        "questions": questions if summary_type == "ai_interview" else [],
        "structured_data": {"questions": questions} if summary_type == "ai_interview" else {},
    }


def _generate_collection_local(
    videos: list[dict[str, Any]],
    summary_type: str,
) -> dict[str, Any]:
    author_names = sorted({v.get("author") or "未知作者" for v in videos})
    corpus = "\n".join((v.get("transcript") or "")[:1200] for v in videos)
    keywords = _keywords(" ".join([v.get("title") or "" for v in videos]) + " " + corpus)
    source_lines = "\n".join(
        f"- {i}. {v.get('title') or '未命名视频'}（{v.get('author') or '未知作者'}）"
        for i, v in enumerate(videos, 1)
    )

    if summary_type == "ai_interview":
        title = f"AI面试题合集：{keywords[0] if keywords else '短视频整理'}"
        source_titles = [
            f"{video.get('title') or '未命名视频'}（{video.get('author') or '未知作者'}）"
            for video in videos
        ]
        key_points = []
        for video in videos:
            key_points.extend(_sentences(video.get("transcript") or "")[:2])
        concept_points = []
        concept_keywords = []
        for video in videos:
            concepts = _interview_concepts(
                video.get("title") or "",
                video.get("tags") or "",
                video.get("transcript") or "",
            )
            for concept in concepts:
                if concept not in concept_keywords:
                    concept_keywords.append(concept)
                    concept_points.append(f"{concept}：{_evidence_around(video.get('transcript') or '', concept)}")
        if concept_points:
            key_points = concept_points
            keywords = concept_keywords
        questions = _build_interview_questions(
            key_points=key_points,
            keywords=keywords,
            prefix=f"c{videos[0].get('id') if videos else 'x'}",
            source_title=title,
            max_questions=8,
        )
        outline = ["题目", "答案解析", "考察点", "追问", "来源视频"]
        content = _questions_to_markdown(
            title=title,
            questions=questions,
            source_lines=source_titles,
            note="合并题库草稿已按题目结构生成，入库前建议人工去重并校准答案。"
        )
    elif summary_type == "wechat_material":
        # 公众号文章档（规则版草稿）：固定【标签】分节，下游按节解析
        # （graphics/channels/wechat.py 的本地兜底模板就靠这套分节拼卡）。
        # 与单视频档同一口径：正文写不出来就别冒充成稿，体检会拦。
        title = f"公众号文章（规则版骨架）：{keywords[0] if keywords else '短视频整理'}"
        outline = ["【标题候选】", "【导语】", "【适用范围】", "【核心论点】", "【正文】",
                   "【关键数据】", "【金句摘录】", "【风险核查】", "【来源】"]
        sentences = []
        for video in videos:
            sentences.extend(_sentences(video.get("transcript") or "")[:2])
        # 关键数据行：从口播里粗抽数字/百分比的句子
        data_lines = [s for s in sentences if re.search(r"\d+(\.\d+)?%|\d{2,}", s)][:5]
        src_detail = "\n".join(
            f"- {i}. {v.get('author') or '未知作者'}《{(v.get('title') or '未命名视频')[:40]}》，"
            f"{(v.get('published_at') or v.get('publish_date') or '日期未记录')[:10]}"
            f"（素材库 id={v.get('id') or '未入库'}）"
            for i, v in enumerate(videos, 1))
        if any(_is_interview_scene(v) for v in videos):
            # AI 面试场景：跨视频抽技术概念去重，论点/正文小节按面试题解析组织
            concept_keywords = []
            arguments = []
            for video in videos:
                for concept in _interview_concepts(
                    video.get("title") or "", video.get("tags") or "", video.get("transcript") or ""
                ):
                    if concept not in concept_keywords and len(arguments) < 3:
                        concept_keywords.append(concept)
                        arguments.append(
                            f"{concept}：{_evidence_around(video.get('transcript') or '', concept)[:50]} [他述]"
                        )
            if not arguments:
                arguments = [f"{s[:50]} [他述]" for s in sentences[:3]]
            if concept_keywords:
                keywords = concept_keywords + [k for k in keywords if k not in concept_keywords][:5]
            skeleton = ("## 问题背景\n本节正文待写（规则版只给小节）\n"
                        "## 核心原理\n本节正文待写\n## 工程实践\n本节正文待写\n"
                        "## 考察点与追问\n本节正文待写")
            scope = "技术栈与框架版本：素材未给出，发前需核 [待核]"
        else:
            arguments = [f"{s[:50]} [他述]" for s in sentences[:3]]
            skeleton = ("## 事件背景\n本节正文待写（规则版只给小节）\n"
                        "## 关键细节\n本节正文待写\n## 玩家影响\n本节正文待写")
            scope = "游戏版本／服务器：素材未给出，发前需核 [待核]"
        content = (
            f"来源视频：\n{source_lines}\n\n"
            "【标题候选】\n"
            f"1. {keywords[0] if keywords else '待定选题'}（配置 AI 后自动生成 3 个候选标题）\n\n"
            "【导语】\n"
            f"{sentences[0][:40] if sentences else '待补充'}\n\n"
            "【适用范围】\n" + scope + "\n\n"
            "【核心论点】\n"
            + "\n".join(f"- {a}" for a in arguments)
            + "\n\n【正文】\n" + skeleton + "\n\n"
            "【关键数据】\n"
            + (("\n".join(f"- {s[:60]} [他述]" for s in data_lines) + "\n") if data_lines else "无\n")
            + "\n\n【金句摘录】\n"
            f"- {sentences[1][:60] if len(sentences) > 1 else '待补充'}（原话）\n\n"
            "【风险核查】\n"
            "- 规则版草稿未做事实核对，发布前核查所有数值/版本/时间线。\n"
            "- 待核项：本文【正文】还是小节骨架，没有可照做的步骤，**不能直接发**。"
            "配 AI 重新生成或由本人写正文后才能进体检。\n\n"
            "【来源】\n" + src_detail + "\n\n"
            "> 规则版只提供结构与骨架；配置 AI 后重新生成才会写出完整正文。"
        )
    elif summary_type == "guide_article":
        # 已废弃：成品文章由自媒体工作台生成，存量数据仅保留可读
        title = f"（已废弃）攻略长文合集：{keywords[0] if keywords else '短视频整理'}"
        outline = ["已废弃"]
        key_points = []
        for video in videos:
            sentences = _sentences(video.get("transcript") or "")
            if sentences:
                key_points.append(sentences[0])
        content = (
            f"来源作者：{'、'.join(author_names)}\n\n"
            f"来源视频：\n{source_lines}\n\n"
            "> 该类型已废弃（成品文章由自媒体工作台生成）。存量草稿仅作参考，"
            "请改用「公众号文章素材」生成结构化素材档案。"
        )
    else:
        title = f"游戏攻略合集：{keywords[0] if keywords else '短视频整理'}"
        outline = ["攻略结论", "操作步骤", "机制解释", "避坑提醒", "来源视频"]
        key_points = []
        for video in videos:
            sentences = _sentences(video.get("transcript") or "")
            if sentences:
                key_points.append(sentences[0])
        content = (
            f"来源作者：{'、'.join(author_names)}\n\n"
            "来源视频：\n"
            f"{source_lines}\n\n"
            "攻略要点：\n"
            + "\n".join(f"- {point}" for point in key_points[:12])
            + "\n\n整理建议：合并稿适合做长文骨架，发布前核查版本、数值、装备名和机制。"
        )

    return {
        "title": title,
        "outline": outline,
        "content": content,
        "keywords": keywords,
        "questions": questions if summary_type == "ai_interview" else [],
        "structured_data": {"questions": questions} if summary_type == "ai_interview" else {},
    }


# 头条整合稿的「留人味」纪律（单源/多源共用一条，避免两处口径漂移）。
# 为什么要单独写死：实测整理稿把口播里最有识别度的说法（「这个NPC的位置非常地老六」）
# 磨成书面语（「位置非常隐蔽」），下游出图再怎么要求「口语化」都没有原料可口语化，
# 成品必然退回模型的语言均值——这就是头条稿「一眼 AI 写的、还没有素材库原文好看」的直接成因。
_TOUTIAO_VOICE_RULES = (
    "\n写作纪律（逐条遵守）：\n"
    "1. 术语表只用于纠正语音转写的同音错字；玩家黑话、口语说法、说话节奏一律保留原样，"
    "不许替换成书面语（「非常地老六」不许写成「位置隐蔽」）。\n"
    "2. 转述别人的实测或结论时标来源，格式「up主『作者名』实测」，作者名取自输入材料，不许编。\n"
    "3. 材料之间口径不一致时不许抹平：叫法、数值、时间对不上的，并列写出来并说明哪个更可信，"
    "不许合并成同一个结论。\n"
    "4. 口播里没有对应内容的小节，整节省略该标签，不要输出「无」占位表格或空清单。\n"
    "5. 【变化对比】只在素材真的有「原来 / 现在」差异时才写。路线型、攻略型素材没有版本前后对比，"
    "整节省略，绝不允许拿「未提及」去填表格的列。\n"
    "6. 不要用 emoji、星号、⚠️✅📍 这类符号做条目前缀——这份稿子是给下游排版消费的原料。\n"
    "7. 【原话摘录】是给作者挑词用的原料，不是成品文案，下游写稿不会整句照抄。\n"
)


def _input_label(summary_type: str) -> str:
    """多源提示词里素材块的标题行。公众号说"输入素材"，其余渠道仍是原来的"输入视频"。"""
    return "输入素材（编号与文末台账一一对应）" if summary_type == "wechat_material" else "输入视频"


def _source_ledger(videos: list, summary_type: str) -> str:
    """公众号这一档专用的「素材台账」：编号 → 是文章还是视频 → 作者 → 发布日。

    2026-10-26 第一次真生成抓到的两个缺陷：提示词里那行「口播 ASR」和「输入视频」会把
    暴雪官方**文章**也说成"视频1/视频2"，模型照着写【来源】，读者按视频去找就找不到出处；
    而发布日根本没喂进去，【来源】只能写"素材未给"（库里其实有 published_at）。
    只给 wechat_material 加，头条/攻略的提示词内容与之前逐字一致（串线判据在 e2e W15）。
    """
    if summary_type != "wechat_material":
        return ""
    kinds = {"微信文章": "文章", "暴雪官方": "文章", "抖音视频数据": "视频",
             "抖音图文": "图文", "B站视频": "视频"}
    rows = []
    for i, v in enumerate(videos, 1):
        sheet = (v.get("source_sheet") or "").strip()
        kind = kinds.get(sheet, "素材")
        date = (v.get("published_at") or v.get("publish_date") or "素材未给")[:10]
        # 链接也一起给：97 号诊断 P1——「信息来源」只写了作者/标题/日期，文章宣称"读者可回查"
        # 却一个链接都没有，库里 source_url 明明是有的（只是没喂进这一层）。
        url = (v.get("source_url") or v.get("video_url") or "").strip()
        rows.append("- 素材%d｜%s（来源表：%s）｜作者：%s｜发布日：%s｜链接：%s"
                    % (i, kind, sheet or "未记录", v.get("author") or "未记录", date,
                       url or "素材未记录"))
    return ("\n素材台账（【来源】与表里的编号照抄，**别把文章写成视频**；"
            "发布日台账里有就填、写「素材未给」才是不知；链接有就原样带进「信息来源」，"
            "不许自己拼、不许改成别的域名）：\n" + "\n".join(rows) + "\n")


def source_ledger(videos: list) -> str:
    """台账的公开入口：成稿那一层也要用同一份实现，别抄第二份类型表。"""
    return _source_ledger(videos, "wechat_material")


def _prompt(video: dict[str, Any], summary_type: str, brief: dict | None = None) -> str:
    if summary_type == "ai_interview":
        task = "整理成高质量 AI 面试题。必须生成 questions 数组，每道题是开放式面试题，包含题目、关键词答案、完整参考回答、答案解析、追问或工程实践延伸。"
        schema = """"questions": [
    {
      "id": "auto_q1",
      "type": "fill",
      "knowledgeTag": "知识点",
      "text": "题目文本",
      "answer": ["关键词答案"],
      "referenceAnswer": "完整参考回答，按问题背景、核心机制、工程边界、验证方法组织",
      "explain": "答案解析：说明这个题考什么，常见错误是什么",
      "deeper": "追问或工程实践延伸"
    }
  ]"""
    elif summary_type == "wechat_material":
        # 2026-10-26 按《公众号当前策略》改口径：这一档的成品是**一篇完整文章**，
        # 不再是"给下游写稿引擎消费的骨架"。配图（AI 海报／图文包）是可选工具，不是必经链，
        # 所以保留【导语】【核心论点】【关键数据】【金句摘录】【风险核查】这几节短颗粒
        # （出图按节拼卡靠它们，`graphics/channels/wechat.py:_fallback_cards`），
        # 但主体【正文】必须真写出来——没有合适图片，这篇文章也要能单独成立。
        task = (
            "把口播写成一篇**能独立发的公众号文章**（不是骨架、不是素材清单）。"
            "正文必须严格按以下【固定标签】分节，标签原样输出、顺序不变，方便下游按节解析：\n"
            "【标题候选】3 个公众号风格标题，15 字内，具体+数字+结果导向，不用惊叹号堆砌；\n"
            "【导语】一句话（40 字内）点出读者收益或痛点；\n"
            "【适用范围】写清这条内容对谁成立：游戏版本／服务器（国服·国际服·无限服等）／"
            "前置条件／什么时候不适用。素材里没说清版本的，直接写「素材未给出版本，发前需核」，不许猜；\n"
            "【核心论点】最多 3 条，每条一句话，信息密度优先；\n"
            "【正文】**这篇的主体**，3-5 个 `## ` 小标题分节，每节要么给能照做的步骤"
            "（第几步做什么、在哪查、看到什么才算成），要么给判断依据（依据是什么、边界在哪）；"
            "每节都要有能回查来源的具体颗粒，不许只写「要注意」「建议关注」这类空话；"
            "本站的独立贡献体现在**重新组织步骤、对照不同来源、写清边界**，"
            "不是把公告拼接一遍、把别人攻略转述一遍；\n"
            "【关键数据】把出现的数值/版本/日期/装备/技能名整理成 markdown 表格（表头：项目|数值|口径|标注），"
            "素材没有的数据写“无”，严禁编造；\n"
            "【金句摘录】直接引用口播原话 1-3 句，标注“（原话）”，保留口语感；\n"
            "【风险核查】发布前必须人工核对的强结论/数值清单，加一条**待核项**：素材里没核实但文章里出现了的说法，"
            "逐条列出；没有写“无强结论、无待核项”。**待核项不许写成能照做的建议**；\n"
            "【来源】这条内容来自谁、原始出处、日期（作者名＋视频/文章标题＋发布日），能让人回查。\n"
            "**四件标注（硬要求）**：正文与关键数据里的每一条，句末用方括号标它是哪一类——"
            "`[已核]`＝本人操作或官方原文可查、`[他述]`＝他人经验/第三方说法、"
            "`[本站]`＝本站自己的分析判断、`[待核]`＝还没查实。"
            "不许把 `[他述]` 或 `[待核]` 写成 `[已核]`，也不许因为【适用范围】里写了就凭空补事实。"
        )
        if video.get("game"):
            task += f"标题候选、【适用范围】的版本口径和【正文】小节都要能看出游戏《{video['game']}》。"
        if _is_interview_scene(video):
            task += (
                "这是 AI 面试/技术类内容，正文按面试题解析组织："
                "标题候选带「面试」「八股」「高频题」等场景词并点出具体技术点；"
                "【核心论点】按「问题→结论」组织，每条对应一个技术考点；"
                "【正文】按「问题背景 → 核心原理 → 代码或工程实践 → 考察点与追问」组织，"
                "每节给能复述的推导，不要只给结论；"
                "【适用范围】写清适用的技术版本与框架；"
                "【风险核查】重点核对技术版本、性能数值、兼容性结论是否为口播原意。"
            )
        schema = """"outline": ["【标题候选】", "【导语】", "【适用范围】", "【核心论点】", "【正文】", "【关键数据】", "【金句摘录】", "【风险核查】", "【来源】"],
  "content": "完整文章，按上述【固定标签】分节，【正文】是真写的分节正文而不是骨架，markdown 表格用标准语法，每条结论句末带 [已核]/[他述]/[本站]/[待核] 标注",
  "keywords": ["关键词1", "关键词2"]"""
    elif summary_type == "toutiao_mix":
        # 头条整合稿（单视频）：同一决策化口径，单源也按固定标签组织，下游出图消费
        task = (
            "把这条视频的口播整理成「头条决策化整合稿」——把信息压成决策价值："
            "读者看完知道「发生了什么变化、对他意味着什么、该怎么做」。"
            "正文必须严格按以下【固定标签】分节，标签原样输出、顺序不变（只有【变化对比】是可选节，见下面第 5 条）：\n"
            "【标题候选】3 个头条号风格标题，20 字内，数字+具体+利益点；\n"
            "【时间线】关键时间节点 markdown 表格（时间|事件）；\n"
            "【变化对比】（可选）核心变化 markdown 表格（维度|原来|现在|对你的影响）——素材里没有真实的「原来 / 现在」差异就整节省略，不许拿「未提及」填空表；\n"
            "【对你的影响】玩家/读者视角的影响判断，最多 4 条；\n"
            "【行动建议】可直接照抄执行的操作清单；\n"
            "【原话摘录】口播里值得留下的原话 3-6 条，每条一行，格式"
            "「原话：≤15字的说法｜意思：一句话」，优先挑玩家黑话、具体动作、带数字的说法；\n"
            "【风险核查】数值/强结论核对清单。" + _TOUTIAO_VOICE_RULES
        )
        schema = """"outline": ["【标题候选】", "【时间线】", "【变化对比】", "【对你的影响】", "【行动建议】", "【原话摘录】", "【风险核查】"],
  "content": "完整整合稿，按上述【固定标签】分节（【变化对比】没有真实前后差异时可省略，但不得输出空表）,markdown 表格用标准语法",
  "keywords": ["关键词1", "关键词2"]"""
    elif summary_type == "guide_article":
        # 已废弃：成品文章由自媒体工作台生成（API 层白名单已拦截，这里兜底）
        raise ValueError("guide_article 已废弃：成品文章由自媒体工作台生成，请使用 wechat_material 公众号素材档案")
    else:
        task = "整理成游戏攻略草稿，包含适用场景、步骤、机制解释、避坑提醒、事实核查清单。"
        schema = """"outline": ["一级要点1", "一级要点2"],
  "content": "完整正文，使用 Markdown",
  "keywords": ["关键词1", "关键词2"]"""
    return f"""你是内容整理助手。请只基于输入材料，不要编造事实，把短视频口播整理成结构化内容。

任务：{task}
{_brief_block(brief)}

输出 JSON：
{{
  "title": "整理后的标题",
  {schema},
  "keywords": ["关键词1", "关键词2"]
}}
{_glossary_block()}{_wow_block([video])}
视频标题：{video.get('title') or ''}
作者：{video.get('author') or ''}
游戏：{video.get('game') or ''}
标签：{video.get('tags') or ''}
口播 ASR：
{(video.get('transcript') or '')[:8000]}
{_source_ledger([video], summary_type)}"""


def _brief_block(brief: dict | None) -> str:
    """排产任务那一格的"选题要求"塞进提示词（2026-10-20 第一单 R3）。

    为什么必须塞进来而不是只存数据库：以前 `plan_tasks` 存了 title/hook/note，
    但生成那一步只把素材原文交给模型——**同素材、同渠道、不同角度会得到完全一样的稿子**，
    排产表填的那些要求等于白填。Codex 验收 R3 点名的就是这个。

    纪律写死在文案里：要求只管**取舍与角度**，不许拿它当事实来源——
    模型仍然"只基于输入材料"，hook 里提到的东西素材没有就不许编。
    """
    if not isinstance(brief, dict):
        return ""
    lines = []
    for key, label in (("title", "这一篇要写的题"), ("hook", "必须答的那个问题／要有的那一格"),
                       ("note", "补充要求"), ("plan_date", "计划发布")):
        val = str(brief.get(key) or "").strip()
        if val:
            lines.append("· %s：%s" % (label, val))
    if brief.get("channel_label") or brief.get("form_label"):
        lines.append("· 渠道：%s　内容形式：%s"
                     % (brief.get("channel_label") or "", brief.get("form_label") or ""))
    block = ("") if not lines else (
        "\n本篇的选题要求（来自排产表，只用来决定**取舍与角度**，不是事实来源；"
        "素材里没有的不许因为这里写了就编）：\n" + "\n".join(lines) + "\n")
    # 补充资料（非视频料）单独一段：身份是配角，判据与纪律见 src/supplements.py。
    # 走这里而不是各提示词自己拼——单素材与合集两条生成路都要能看到同一份。
    if brief.get("supplements"):
        from . import supplements as _sup
        block += "\n" + _sup.brief_block(brief["supplements"])
    return block


def _glossary_block() -> str:
    """把术语纠正表塞进整理稿提示词。

    为什么必须塞表而不是在提示词里写一句"请修正错字"：整理稿的纪律是「只基于输入材料，
    不要编造事实」，模型没有依据就不敢改，于是语音转写的同音错字一路原样传到海报上
    （实测「装等」变成「装灯」、「饰品」变成「视频」）。有了这张表，按表改属于有依据的
    规范化，不算编造。表在 config/term_glossary.json，人工维护、按修改时间热加载不用重启。
    """
    try:
        from . import term_glossary
        text = term_glossary.render_prompt()
    except Exception:
        return ""
    return ("\n" + text + "\n") if text else ""


def _wow_block(videos: list) -> str:
    """魔兽题材额外注入「实名参考」：名单来自 wow-forever 数据库同步下来的副本。

    和上面那张手工词表同一个道理——整理稿纪律「只基于输入材料」会连纠错一起挡掉，
    得有依据才敢改。区别是这张表由数据派生、按本篇相关性挑条目，不手工维护。
    非魔兽题材返回空串，一个字都不加。逻辑与纪律说明见 src/wow_reference.py。
    """
    try:
        from . import wow_reference
        return wow_reference.build_block(videos or [])
    except Exception:
        return ""


def _collection_prompt(videos: list[dict[str, Any]], summary_type: str,
                       brief: dict | None = None) -> str:
    if summary_type == "ai_interview":
        task = "把多条视频合并整理成一套高质量 AI 面试题，去重相似内容。必须生成 questions 数组，每题是开放式面试题，包含题目、关键词答案、完整参考回答、答案解析、追问或工程实践延伸。"
        schema = """"questions": [
    {
      "id": "auto_q1",
      "type": "fill",
      "knowledgeTag": "知识点",
      "text": "题目文本",
      "answer": ["关键词答案"],
      "referenceAnswer": "完整参考回答，按问题背景、核心机制、工程边界、验证方法组织",
      "explain": "答案解析",
      "deeper": "追问或工程实践延伸"
    }
  ]"""
    elif summary_type == "wechat_material":
        # 与单视频档同一口径（2026-10-26 改成完整文章）：多源合并的价值就在"同一件事几个 up 主
        # 讲出不同说法"，所以冲突必须并列写、标注谁说的，不许揉成一个看似确定的结论。
        task = (
            "把多条视频合并成一篇**能独立发的公众号文章**（不是素材清单），去重相似内容。"
            "正文必须严格按以下【固定标签】分节，标签原样输出、顺序不变，方便下游按节解析：\n"
            "【标题候选】3 个公众号风格标题，15 字内，具体+数字+结果导向；\n"
            "【导语】一句话（40 字内）点出读者收益；\n"
            "【适用范围】游戏版本／服务器／前置条件／什么时候不适用；素材没给版本就写「素材未给出版本，发前需核」；\n"
            "【核心论点】最多 3 条（跨视频去重合并）；\n"
            "【正文】**这篇的主体**，3-5 个 `## ` 小标题分节，每节给能照做的步骤或明确的判断依据；"
            "同一件事几个来源说法不一致的，**并列写清「谁说的、各自结论、本站倾向哪个、为什么」**，"
            "不许把矛盾揉成一句确定话，也不许拿「未提及」填空；\n"
            "【关键数据】markdown 表格（项目|数值|口径|来源编号），来源编号照文末台账写「素材1」这种并带上类型，素材没有写“无”，严禁编造；\n"
            "【金句摘录】引用原话 1-3 句，标注“（原话）”并带来源编号；\n"
            "【风险核查】发布前必查的强结论/数值清单（标注来自哪条素材），外加**待核项**：还没查实却出现在正文里的说法逐条列出，"
            "待核项不许写成能照做的建议；没有写“无强结论、无待核项”；\n"
            "【来源】照文末「素材台账」逐条写：类型（文章/视频）＋作者＋标题＋发布日，让读者能回查；台账没给日期的才写「素材未给」。\n"
            "**四件标注（硬要求）**：正文与关键数据每条句末标 `[已核]`／`[他述]`／`[本站]`／`[待核]`，"
            "不许把 `[他述]` 或 `[待核]` 写成 `[已核]`。"
        )
        if any(_is_interview_scene(v) for v in videos):
            task += (
                "这批是 AI 面试/技术类内容，正文按面试题解析组织："
                "标题候选带「面试」「八股」「高频题」等场景词并点出具体技术点；"
                "【核心论点】按「问题→结论」组织，每条对应一个技术考点；"
                "【关键数据】优先整理技术栈/版本/性能指标/兼容性结论；"
                "【正文】按「问题背景 → 核心原理 → 代码或工程实践 → 考察点与追问」组织。"
            )
        schema = """"outline": ["【标题候选】", "【导语】", "【适用范围】", "【核心论点】", "【正文】", "【关键数据】", "【金句摘录】", "【风险核查】", "【来源】"],
  "content": "完整文章，按【固定标签】分节，【正文】是真写的分节正文而不是骨架，markdown 表格用标准语法，每条结论句末带标注",
  "keywords": ["关键词1", "关键词2"]"""
    elif summary_type == "toutiao_mix":
        # 头条整合稿：多源决策化整合（变化对比+影响+行动），下游「头条图文」出图消费
        task = (
            "把多条视频的口播整合成一份「头条决策化整合稿」——不是消息拼盘，是把多源信息压成决策价值："
            "读者看完知道「发生了什么变化、对他意味着什么、该怎么做」。去重相似内容，交叉印证的信息标注一致，矛盾的信息并列展示。"
            "正文必须严格按以下【固定标签】分节，标签原样输出、顺序不变，方便下游按节解析出图：\n"
            "【标题候选】3 个头条号风格标题，20 字内，数字+具体+利益点，不用惊叹号堆砌；\n"
            "【时间线】关键时间节点 markdown 表格（时间|事件|来源视频编号）；\n"
            "【变化对比】核心变化 markdown 表格（维度|原来|现在|对你的影响|来源视频编号），有版本前后差异时这是本稿的核心、至少 3 行；"
            "纯路线/纯攻略素材确实没有前后差异的，按纪律 5 整节省略；\n"
            "【对你的影响】玩家/读者视角的影响判断，最多 4 条，每条一句话；\n"
            "【行动建议】可直接照抄执行的操作清单（选什么、准备什么、什么时候做）；\n"
            "【口径分歧】不同视频说法不一致的地方，每条格式「争点：A 说…（视频1）／B 说…（视频3）→ 我倾向…，因为…」，"
            "这是本稿唯一的独家增量，有分歧必须写，没有就省略本节；\n"
            "【原话摘录】各条口播里值得留下的原话 3-6 条，每条一行，格式"
            "「原话：≤15字的说法｜意思：一句话｜视频编号」，优先挑玩家黑话、具体动作、带数字的说法；\n"
            "【风险核查】数值/强结论核对清单，标注来源视频编号。" + _TOUTIAO_VOICE_RULES
        )
        schema = """"outline": ["【标题候选】", "【时间线】", "【变化对比】", "【对你的影响】", "【行动建议】", "【口径分歧】", "【原话摘录】", "【风险核查】"],
  "content": "完整整合稿，按上述【固定标签】分节，markdown 表格用标准语法",
  "keywords": ["关键词1", "关键词2"]"""
    elif summary_type == "guide_article":
        # 已废弃：成品文章由自媒体工作台生成（API 层白名单已拦截，这里兜底）
        raise ValueError("guide_article 已废弃：成品文章由自媒体工作台生成，请使用 wechat_material 公众号素材档案")
    else:
        task = "把多条视频合并整理成一篇游戏攻略合集，去重相似内容，输出适用场景、步骤、机制解释、避坑提醒和事实核查清单。"
        schema = """"outline": ["一级要点1", "一级要点2"],
  "content": "完整正文，使用 Markdown，并标出来源视频编号",
  "keywords": ["关键词1", "关键词2"]"""
    games = [v.get("game") for v in videos if v.get("game")]
    if games and summary_type != "ai_interview":
        unique_games = list(dict.fromkeys(games))
        game_note = "、".join(f"《{g}》" for g in unique_games)
        task += f"标题和正文点明游戏 {game_note}，方便同游戏读者检索。"
    blocks = []
    for i, video in enumerate(videos, 1):
        blocks.append(
            f"""[{i}]
标题：{video.get('title') or ''}
作者：{video.get('author') or ''}
游戏：{video.get('game') or ''}
标签：{video.get('tags') or ''}
口播 ASR：
{(video.get('transcript') or '')[:5000]}
"""
        )
    return f"""你是内容整理助手。请只基于输入材料，不要编造事实。

任务：{task}
{_brief_block(brief)}

输出 JSON：
{{
  "title": "整理后的合集标题",
  {schema},
  "keywords": ["关键词1", "关键词2"]
}}
{_glossary_block()}{_wow_block(videos)}
{_input_label(summary_type)}：
{chr(10).join(blocks)}
{_source_ledger(videos, summary_type)}"""


def _normalize_interview_result(
    result: dict[str, Any],
    fallback_title: str,
    source_lines: list[str],
) -> dict[str, Any]:
    questions = result.get("questions") or result.get("structured_data", {}).get("questions") or []
    normalized = []
    for idx, raw in enumerate(questions, 1):
        q_type = raw.get("type") or "fill"
        question = {
            "id": str(raw.get("id") or f"auto_q{idx}"),
            "type": q_type if q_type in {"single", "multi", "fill", "order", "locate", "interview"} else "fill",
            "knowledgeTag": str(raw.get("knowledgeTag") or raw.get("knowledge_tag") or "AI工程"),
            "text": str(raw.get("text") or raw.get("question") or ""),
            "answer": raw.get("answer") or [],
            "referenceAnswer": str(raw.get("referenceAnswer") or raw.get("reference_answer") or raw.get("参考答案") or ""),
            "explain": str(raw.get("explain") or raw.get("analysis") or raw.get("解析") or ""),
            "deeper": str(raw.get("deeper") or raw.get("follow_up") or raw.get("追问") or ""),
        }
        if question["type"] in {"single", "multi"}:
            question["options"] = _normalize_options(raw.get("options") or [])
        if question["text"]:
            normalized.append(question)

    if not normalized:
        normalized = _build_interview_questions(
            key_points=[result.get("content") or fallback_title],
            keywords=result.get("keywords") or [],
            prefix="auto",
            source_title=fallback_title,
        )

    title = result.get("title") or fallback_title
    return {
        "title": title,
        "outline": result.get("outline") or ["题目", "答案解析", "考察点", "追问", "来源视频"],
        "content": result.get("content") or _questions_to_markdown(
            title=title,
            questions=normalized,
            source_lines=source_lines,
            note="AI 已生成题库结构，入库前建议人工复核。",
        ),
        "keywords": result.get("keywords") or [q.get("knowledgeTag", "") for q in normalized[:8]],
        "questions": normalized,
        "structured_data": {"questions": normalized},
    }


def _build_interview_questions(
    key_points: list[str],
    keywords: list[str],
    prefix: str,
    source_title: str,
    max_questions: int = 5,
) -> list[dict[str, Any]]:
    points = [point.strip() for point in key_points if point and point.strip()]
    if not points:
        points = [source_title]
    tags = keywords or _keywords(" ".join(points))
    questions = []
    for idx, point in enumerate(points[:max_questions], 1):
        tag = tags[(idx - 1) % len(tags)] if tags else "AI工程"
        concise = _compact_text(point, 180)
        answer = _build_reference_answer(tag, concise)
        questions.append({
            "id": f"{prefix}q{idx}",
            "type": "fill",
            "knowledgeTag": tag,
            "text": f"请结合视频内容，系统解释「{tag}」解决了什么问题、核心方法是什么，以及它的边界或注意事项。",
            "answer": [tag],
            "referenceAnswer": answer,
            "explain": f"这题考察的不是名词记忆，而是能否把视频里的口播信息整理成「问题 -> 方法 -> 边界 -> 验证」的工程化回答。核心依据：{concise}",
            "deeper": f"如果你在真实项目中使用「{tag}」，你会设计哪些指标或测试来证明它确实有效？",
        })
    return questions


def _build_reference_answer(tag: str, evidence: str) -> str:
    return (
        f"可以从四层来回答。\n"
        f"1. 问题背景：材料围绕「{tag}」讨论的不是抽象定义，而是实际使用中会遇到的效果不稳定、理解偏差或落地困难。\n"
        f"2. 核心机制：结合视频依据，可以概括为：{evidence}\n"
        f"3. 工程边界：回答时要说明它适合解决什么问题，不适合解决什么问题，哪些前提条件缺失时容易失效。\n"
        f"4. 验证方法：可以通过对比实验、失败样例、输出质量、准确率、延迟、成本或人工复核结果来判断方案是否真的有效。"
    )


def _interview_concepts(title: str, tags: str, transcript: str) -> list[str]:
    text = f"{title} {tags} {transcript}"
    candidates = [
        ("提示词六步法", ["六步法", "提示词"]),
        ("角色设定", ["角色", "帽子", "专业模式"]),
        ("Context 背景", ["Context", "背景"]),
        ("Task 任务", ["Task", "任务", "明确的动词"]),
        ("输出格式约束", ["格式", "表格", "输出"]),
        ("RAG 检索增强生成", ["RAG", "检索增强生成", "外挂一个大脑"]),
        ("向量数据库", ["向量", "书局库", "数据库"]),
        ("切分与重叠", ["切分", "重叠", "窗口长度"]),
        ("幻觉问题", ["幻觉", "胡说八道", "瞎编"]),
        ("知识时效性", ["知识之后", "数据截止", "今天早上"]),
        ("私有数据接入", ["数据私有", "内网", "企业"]),
        ("RAG 与微调区别", ["微调", "开卷考试", "闭卷"]),
        ("Agent 工具调用", ["工具", "Action", "Observation"]),
        ("异步并发", ["async", "并发", "I/O"]),
        ("流式输出", ["流式", "NDJSON", "SSE"]),
    ]
    concepts = []
    for concept, needles in candidates:
        if any(needle.lower() in text.lower() for needle in needles):
            concepts.append(concept)
    if concepts:
        return concepts[:6]
    return _keywords(f"{title} {tags} {transcript[:600]}")[:5]


def _evidence_around(text: str, term: str, radius: int = 150) -> str:
    if not text:
        return term
    lower = text.lower()
    aliases = {
        "提示词六步法": ["六步法", "提示词", "下命令"],
        "角色设定": ["帽子", "专业模式", "绝色", "角色"],
        "Context 背景": ["Context", "背景", "老板", "汇报"],
        "Task 任务": ["Task", "任务", "出租车", "明确"],
        "输出格式约束": ["格式", "表格", "输出", "小座文"],
        "RAG 检索增强生成": ["RAG", "外挂一个大脑", "检索增强生成"],
        "向量数据库": ["向量", "书局库", "数据库"],
        "切分与重叠": ["切分", "重叠", "窗口长度"],
        "幻觉问题": ["幻觉", "胡说八道", "瞎编"],
        "知识时效性": ["知识之后", "数据截止", "今天早上"],
        "私有数据接入": ["数据私有", "内网", "企业"],
        "RAG 与微调区别": ["微调", "开卷考试", "闭卷"],
    }
    needles = [
        term.lower(),
        *[alias.lower() for alias in aliases.get(term, [])],
        *[part.lower() for part in re.split(r"[\s/与]+", term) if len(part) >= 2],
    ]
    positions = [lower.find(needle) for needle in needles if lower.find(needle) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return _compact_text(text[start:end], 220)


def _questions_to_markdown(
    title: str,
    questions: list[dict[str, Any]],
    source_lines: list[str],
    note: str,
) -> str:
    chunks = [f"# {title}", "", "## 来源视频"]
    chunks.extend(f"- {line}" for line in source_lines)
    chunks.append("")
    chunks.append("## 面试题")
    for idx, question in enumerate(questions, 1):
        chunks.extend([
            "",
            f"### Q{idx}. {question.get('text', '')}",
            "",
            f"- 考察点：{question.get('knowledgeTag', '')}",
        ])
        options = question.get("options") or []
        if options:
            chunks.append("- 选项：")
            for option in options:
                chunks.append(f"  - {option.get('value')}. {option.get('text')}")
        chunks.extend([
            f"- 关键词答案：{_answer_to_text(question.get('answer'))}",
            f"- 参考回答：{question.get('referenceAnswer') or question.get('reference_answer') or ''}",
            f"- 答案解析：{question.get('explain', '')}",
            f"- 追问：{question.get('deeper', '')}",
        ])
    chunks.extend(["", f"## 入库备注\n{note}"])
    return "\n".join(chunks)


def _normalize_options(options: list[Any]) -> list[dict[str, str]]:
    values = ["a", "b", "c", "d"]
    normalized = []
    for idx, option in enumerate(options[:4]):
        if isinstance(option, dict):
            text = str(option.get("text") or option.get("label") or "")
            value = str(option.get("value") or values[idx])
        else:
            text = str(option)
            value = values[idx]
        normalized.append({"text": text, "value": value})
    while len(normalized) < 4:
        idx = len(normalized)
        normalized.append({"text": f"干扰选项 {values[idx].upper()}", "value": values[idx]})
    return normalized


def _answer_to_text(answer: Any) -> str:
    if isinstance(answer, list):
        return "、".join(str(item) for item in answer)
    return str(answer)


def _header_map(ws) -> dict[str, int]:
    aliases = {
        "抖音链接": "链接",
        "原始链接": "链接",
        "处理状态": "状态",
        "视频文案(ASR)": "视频文案ASR",
        "口播原文(ASR)": "口播原文",
        "关键词/摘要": "关键词摘要",
    }
    mapping = {}
    for col in range(1, ws.max_column + 1):
        raw = ws.cell(1, col).value
        if not raw:
            continue
        name = aliases.get(str(raw).strip(), str(raw).strip())
        mapping[name] = col
    return mapping


def _cell(ws, row: int, name: str, headers: dict[str, int]) -> str:
    col = headers.get(name) or BASE_COLUMNS.get(name)
    if not col:
        return ""
    value = ws.cell(row, col).value
    return "" if value is None else str(value)


def _dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {row["name"] for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _json_or_text(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _structured_payload(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("structured_data"), dict):
        return result["structured_data"]
    if result.get("questions"):
        return {"questions": result["questions"]}
    # 攻略类（game_guide / guide_article）：按 ## 小标题切块，供程序化排版/抽取用
    sections = _split_markdown_sections(result.get("content") or "")
    if sections:
        return {
            "outline": result.get("outline") or [],
            "keywords": result.get("keywords") or [],
            "sections": sections,
        }
    return {}


def _split_markdown_sections(content: str) -> list[dict[str, str]]:
    """按 ## 小标题切 Markdown 正文；正文开头的引子归入 intro 段。"""
    sections: list[dict[str, str]] = []
    current_title = "intro"
    buf: list[str] = []
    for line in (content or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## "):
            if buf or sections:
                sections.append({"title": current_title, "body": "\n".join(buf).strip()})
            current_title = stripped[3:].strip()
            buf = []
        else:
            buf.append(line)
    if buf or sections:
        sections.append({"title": current_title, "body": "\n".join(buf).strip()})
    return [s for s in sections if s["title"] != "intro" or s["body"]]


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"[。！？!?；;\n]+", cleaned)
    sentences = [p.strip() for p in parts if len(p.strip()) >= 8]
    if len(sentences) <= 1 and len(cleaned) > 120:
        sentences = [
            cleaned[i:i + 110].strip()
            for i in range(0, min(len(cleaned), 660), 110)
            if cleaned[i:i + 110].strip()
        ]
    return sentences


def _compact_text(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ，。,.")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip(" ，。,.") + "..."


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9+#]{2,}|[\u4e00-\u9fff]{2,6}", text)
    stop = {"这个", "一个", "就是", "可以", "没有", "什么", "视频", "我们", "你们"}
    counts: dict[str, int] = {}
    for word in words:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: -item[1])[:8]]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ════════════════════════════════════════════════════════════════
# 分类打标
# ════════════════════════════════════════════════════════════════

CATEGORIES = [
    "前端面试", "AI技术", "游戏攻略", "编程开发",
    "产品设计", "数码评测", "汽车资讯", "商业财经",
    "职场成长", "生活日常", "其他",
]


def get_category_stats(db_path: str | Path) -> list[dict[str, Any]]:
    """分类 -> 数量统计（含未分类），供前端筛选 pills 渲染。"""
    conn = open_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(category, ''), '未分类') AS cat, COUNT(*) AS n "
            "FROM videos GROUP BY cat ORDER BY n DESC"
        ).fetchall()
        return [{"category": r["cat"], "count": r["n"]} for r in rows]
    finally:
        conn.close()


# 渠道选题策略（与自媒体工作台 app.js 口径一致，集中管理便于后续调整）
# 头条号：7:2:1（攻略:资讯:试错），攻略特征词加权、资讯特征词降权、止损分类直接排除
# 公众号：2026-10-19 换轨——从「AI 技术信息搬运」改成「魔兽资料查询工具的使用教程」，
#         加权分类随之从 AI/前端/编程换成游戏攻略与魔兽题材词，并把 AI/编程类整类排除
#         （依据 product/公众号内容线计划-2026-10.md 第〇节：号主 10-19 拍板 AI 面试答案库不做了）
# 小红书：轻量话题渠道，近期热度权重放大，生活/攻略类标签优先
# 渠道策略版本：调整任何渠道的词表/分类/权重后 +1（media-workbench 兜底快照同步对齐）
# v4（2026-10-19）：公众号换轨；v3：公众号图文模式 newspic
# v5（2026-10-26）：公众号**主体成品改成完整富文本文章**，图片消息/图文包降级为可选配图工具
#         （依据 wow-forever-web `docs/OPS-公众号当前策略-2026-10-25.md`，号主 10-25 定、10-26 修订成稿形式）。
#         头条侧词表与 7:2:1 口径一行没动，这次 +1 只因为公众号口径变了。
#         注意：media-workbench 的兜底快照 `public/channel-strategy.js` 停在 v2（本轮未同步，
#         比对会 warn——那是它一直在 warn，不是这次改坏的）。
CHANNEL_STRATEGY_VERSION = 5

RADAR_CHANNEL_STRATEGY: dict[str, dict[str, Any]] = {
    "toutiao": {
        "label": "头条号",
        "desc": "7:2:1 策略 · 攻略标签加权 · 资讯标签降权 · 止损分类与AI/面试类内容排除",
        "guide_kw": ["指南", "一览", "盘点", "怎么", "怎么选", "别乱", "别扔", "别急", "路线",
                      "攻略", "必看", "先看", "算清", "速通", "排行", "清单", "合集", "天价",
                      "底材", "掉落", "配装", "毕业", "流程", "对比", "避坑", "注意", "设置",
                      "帧数", "插件"],
        "news_kw": ["定档", "要开", "要来", "来了", "跳票", "更新", "补丁", "版本", "开服",
                     "上架", "发布", "免费", "上线", "解禁", "开启", "改了", "被砍", "加强",
                     "削弱", "预警", "风险", "紧急", "官宣", "泄露", "爆料", "开测", "停服",
                     "复刻"],
        # 历史CTR<1%止损分类 + AI/面试类分类
        # 2026-10-19 改口径：这四类原来注成「归公众号渠道」，公众号换轨后也不收 AI 题材了，
        # 所以现在两个渠道都排除（wechat 侧同名键各自列一份），别再写「归某某渠道」。
        "excluded_categories": ["数码评测", "汽车资讯", "AI技术", "前端面试"],
        # AI/面试关键词：标题或标签命中即剔除（游戏类豁免，防误伤"AI代打""AI BD"等游戏语境）
        "exclude_kw": [
            "AI", "人工智能", "大模型", "智能体", "机器学习", "深度学习", "神经网络",
            "多模态", "微调", "提示词", "AIGC", "生成式", "幻觉",
            "llm", "gpt", "claude", "deepseek", "rag", "mcp", "agentic", "agent",
            "embedding", "diffusion", "webllm", "onnx", "codex", "dify",
            "langchain", "langgraph", "lang", "copilot", "midjourney", "vibe coding",
            "面试", "八股", "秋招", "春招", "简历", "求职", "leetcode", "刷题",
            "算法题", "offer",
            # 汽车/数码/硬件类：流量跑偏来源（2nm芯片/车展/路虎/NVIDIA驱动等阅读几乎为0），归数码渠道不进头条号
            "芯片", "2nm", "手机", "汽车", "车展", "路虎", "智驾", "预售",
            "nvidia", "显卡", "处理器", "小米18", "激光雷达",
        ],
        "exclude_kw_exempt_categories": ["游戏攻略", "生活日常"],
    },
    "wechat": {
        "label": "公众号",
        "desc": "魔兽工具教程优先 · 游戏攻略/魔兽题材标签加权 · AI与编程类整类排除 · 公众号文章稿加权 · "
                "主体成品=完整富文本文章（不是图卡），配图工具可选、图片消息不是必经链",
        # 2026-10-26 新策略落进权威源：这两键是工作台读"公众号该交什么"的判据，
        # graphics_mode 那三条留着——它们描述的是**可选配图工具**产出物的形态，不是渠道主成品。
        "primary_product": "article",       # 交出去的是整合稿那篇完整文章
        "graphics_optional": True,          # 出图/图文包是配图工具，缺图文章照样成立
        # 粗分类（视频库 category，Python 侧 content_store.py:2410 是**精确相等**匹配）
        # + 细分类关键词（video_index 选题 topic，media-workbench 侧 channel-strategy.js 走**子串**匹配）
        # 2026-10-19 换轨：原值 ["AI技术","前端面试","编程开发","产品设计","前端","编程"] 作废。
        # 现在只留一条能被库证明的：videos 表 609 条「游戏攻略」是最大分类，其余是魔兽题材词，
        # 逐条对得上公众号选题池那 6 篇（BiS/掉落、物品、副本任务、稀有、配方、资料完整度）。
        "knowledge_categories": ["游戏攻略", "魔兽", "攻略", "配装", "掉落",
                                  "副本", "稀有", "配方", "天赋"],
        # 换轨后必须加这一条：knowledge_categories 只是**加权**，不排除。
        # 不加它，132 条 AI技术 + 24 条前端面试照样出现在公众号选题池里，只是不带权重——
        # 号主看到的仍然是"工作台在推 AI"。取值与 toutiao 侧同源（AI/编程两渠道都不收）。
        # 实测（2026-10-19，/api/topics/radar?months=12）：加这条后 wechat 的 total_videos
        # 从 938 降到 689（正好等于六个分类之和 249），top10 全是魔兽题材且 knowledge_count 生效。
        # 已知残留 13 条带 AI/面试/前端 字样的标签，来自 category 为空的 202 条——
        # 头条侧同样口径只剩 3 条，差别就在它有 exclude_kw 兜着。等真出现误推再给 wechat 补
        # exclude_kw，不先加一堆词。
        "excluded_categories": ["AI技术", "前端面试", "编程开发", "产品设计",
                                 "数码评测", "汽车资讯"],
        "summary_bonus_type": "wechat_material",
        # 图文模式（迁移方案 §5.4-8，v3 新增）：公众号走图片消息 newspic，工作台据此展示入口
        "graphics_mode": "newspic",
        "graphics_canvas": "1080x1440",
        "graphics_templates": ["classic", "minimal", "magazine"],
        "graphics_max_images": 20,
        "author_draft_min": 300,       # 作者真实底稿最低字数（§6 强制机制，生成时必填）
        "real_screenshots_min": 2,     # 真实截图铁律下限（lint 核验）
    },
    "xhs": {
        "label": "小红书",
        "desc": "近期话题优先 · 近期热度权重×3 · 生活/攻略类标签加权",
        # 同上：粗分类 + 细分类关键词（「流放2攻略」「暗黑4攻略」等）
        "light_categories": ["生活日常", "游戏攻略", "攻略", "生活"],
    },
}

# 全局策略阈值（所有渠道共享）：止损线、发布结构目标、结构提示统计窗口
CHANNEL_STRATEGY_GLOBAL: dict[str, Any] = {
    "lowCtrLine": 0.01,       # 止损线：同类已发≥2篇且均CTR低于该值的分类建议停更
    "mixTarget": [7, 2, 1],   # 头条号发布结构目标（攻略:资讯:其他）
    "mixRecentCount": 10,     # 结构提示统计的近期发布篇数
    "minStatCount": 2,        # 分类止损判定的最小样本数
}


def get_channel_strategy_config() -> dict[str, Any]:
    """渠道策略配置下发：video2text 是策略唯一权威源。

    自媒体工作台通过 /api/strategy/channels 拉取本配置驱动前端策略引擎，
    避免策略口径在两个项目各存一份拷贝（调整只改此处，全局生效）。
    """
    return {
        # 版本号：权威源调整策略口径时递增；media-workbench 兜底快照对齐此版本，
        # 不一致时前端控制台 warn（漂移可自动发现，不再依赖人肉同步）
        "version": CHANNEL_STRATEGY_VERSION,
        "global": dict(CHANNEL_STRATEGY_GLOBAL),
        "channels": {
            key: {k: (list(v) if isinstance(v, list) else v) for k, v in cfg.items()}
            for key, cfg in RADAR_CHANNEL_STRATEGY.items()
        },
    }


# 选题雷达已于 2026-10-20 下线（排产表是唯一的日常选题入口，见 CHANGELOG 同日一节）。
# 删的是雷达专属聚合 `_radar_kw_hit` / `get_topic_radar` 与 `/api/topics/radar`；
# **上面那份 RADAR_CHANNEL_STRATEGY 留着**——它是渠道策略权威源，由 /api/strategy/channels
# 下发给工作台与 plan_tasks 借 key→label，名字带 radar 不代表只服务雷达页，别顺手删。

def update_video_category(
    db_path: str | Path,
    video_id: int,
    category: str,
    ai_tags: str = "",
    game: str | None = None,
) -> bool:
    if category and category not in CATEGORIES:
        category = "其他"
    if category != "游戏攻略" and game is not None:
        game = ""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE videos SET category = ?, "
            "ai_tags = CASE WHEN ? = '' THEN ai_tags ELSE ? END, "
            "game = CASE WHEN ? IS NULL THEN game ELSE ? END, "
            "updated_at = ? WHERE id = ?",
            (
                category,
                ai_tags.strip(), ai_tags.strip(),
                game, game,
                _now(), video_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_game_stats(db_path: str | Path) -> list[dict[str, Any]]:
    """按游戏名统计视频数（仅游戏攻略分类）。"""
    conn = open_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(game), ''), '未识别游戏') AS g, COUNT(*) AS n "
            "FROM videos WHERE category = '游戏攻略' GROUP BY g ORDER BY n DESC"
        ).fetchall()
        return [{"game": r["g"], "count": r["n"]} for r in rows]
    finally:
        conn.close()


def _classify_prompt(batch: list[dict[str, Any]]) -> str:
    items = []
    for v in batch:
        snippet = _compact_text(v.get("transcript") or "", 500)
        items.append(
            f'{{"key": "{v["sheet"]}::{v["row"]}", "title": "{(v.get("title") or "")[:60]}", '
            f'"author": "{(v.get("author") or "")[:20]}", "tags": "{(v.get("tags") or "")[:60]}", '
            f'"excerpt": "{snippet}"}}'
        )
    return (
        "你是内容分类助手。根据以下抖音视频的标题、作者、标签和口播摘要，"
        f'为每条视频从这些分类中选一个最贴切的：{"、".join(CATEGORIES)}。\n'
        "同时为每条视频提炼 3-5 个内容关键词（标签里没有的、视频实际讲的主题）。\n"
        "如果分类是游戏攻略，识别出视频讲的具体游戏名（如：流放之路2、原神、黑神话:悟空），"
        '填入 game 字段；其他分类 game 留空字符串。\n\n'
        "输入视频列表：\n" + "\n".join(items) + "\n\n"
        '输出 JSON：{"results": [{"key": "sheet::row", "category": "分类", "game": "游戏名或空", "tags": ["关键词", ...]}, ...]}\n'
        "要求：key 必须原样返回；只输出 JSON，不要输出其他内容。"
    )


def _classify_local(video: dict[str, Any]) -> dict[str, Any]:
    """无 AI 时的规则兜底分类。"""
    text = f'{video.get("title") or ""} {video.get("tags") or ""} {(video.get("transcript") or "")[:800]}'
    rules = [
        ("前端面试", r"面试|前端|js|javascript|css|vue|react|浏览器|闭包|promise"),
        ("游戏攻略", r"攻略|游戏|副本|装备|打法|阵容|通关|赛季"),
        ("AI技术", r"大模型|ai|llm|gpt|deepseek|提示词|agent|智能体|人工智能"),
        ("编程开发", r"代码|编程|开发|python|java|接口|数据库|算法"),
        ("职场成长", r"职场|副业|涨薪|简历|裁员|打工|牛马"),
    ]
    category = "其他"
    for cat, pattern in rules:
        if re.search(pattern, text, re.I):
            category = cat
            break
    return {"key": f'{video["sheet"]}::{video["row"]}', "category": category,
            "tags": _keywords(text)[:5]}


def classify_videos(
    db_path: str | Path,
    ai_config: dict[str, str],
    force: bool = False,
    progress_cb=None,
    only_ids=None,
) -> dict[str, int]:
    """批量分类：优先 LLM，失败批次降级规则分类。progress_cb(done, total, note)。

    only_ids：只处理这批 video id（同步收尾用）。不传就是全库扫缺口——那在没有上限的
    情况下会把历史存量一起过一遍 LLM，一次同步花掉的钱没人盯着。
    """
    ids = [int(i) for i in (only_ids or [])]
    conn = connect(db_path)
    try:
        parts = []
        params: list[Any] = []
        # 订阅同步入库的视频自带 category（继承订阅分类），但仍缺 ai_tags（选题雷达依赖）。
        # 因此只要 category 或 ai_tags 任一为空就补分类，而不是仅看 category。
        # 加 only_ids 时必须整体加括号，否则 OR 会把 id 条件短路掉。
        if not force:
            parts.append("(COALESCE(category, '') = '' OR COALESCE(ai_tags, '') = '')")
        if ids:
            parts.append("id IN (" + ",".join("?" for _ in ids) + ")")
            params = ids
        where = ("WHERE " + " AND ".join(parts)) if parts else ""
        rows = conn.execute(
            f"SELECT id, source_sheet, source_row, author, title, tags, transcript "
            f"FROM videos {where} ORDER BY id", params
        ).fetchall()
        videos = []
        for r in rows:
            v = dict(r)
            v["sheet"] = v["source_sheet"]
            v["row"] = v["source_row"]
            videos.append(v)
    finally:
        conn.close()
    if not videos:
        return {"total": 0, "classified": 0, "llm_batches": 0}

    total = len(videos)
    done = 0
    use_llm = ai_config.get("method") != "skip" and ai_config.get("api_key")
    batch_size = 10
    results: list[dict[str, Any]] = []

    for i in range(0, total, batch_size):
        batch = videos[i : i + batch_size]
        if use_llm:
            data = _llm_chat(ai_config, _classify_prompt(batch), timeout=90)
            parsed = data.get("results") if isinstance(data, dict) else None
            if parsed:
                keyed = {str(v["sheet"]) + "::" + str(v["row"]): v for v in batch}
                for item in parsed:
                    key = str(item.get("key") or "")
                    if key in keyed:
                        cat = item.get("category") or "其他"
                        if cat not in CATEGORIES:
                            cat = "其他"
                        game = str(item.get("game") or "").strip()
                        if cat != "游戏攻略":
                            game = ""
                        tags = item.get("tags") or []
                        if isinstance(tags, list):
                            tags = " ".join(str(t) for t in tags[:5])
                        results.append(
                            {"id": keyed[key]["id"], "category": cat, "ai_tags": str(tags or ""),
                             "game": game}
                        )
                        continue
                # LLM 漏掉的条目用规则兜底
                got_keys = {r.get("key") for r in (parsed or [])}
                for v in batch:
                    if f'{v["source_sheet"]}::{v["source_row"]}' not in got_keys:
                        r = _classify_local(
                            {"sheet": v["source_sheet"], "row": v["source_row"],
                             "title": v["title"], "tags": v["tags"], "transcript": v["transcript"]}
                        )
                        results.append({"id": v["id"], "category": r["category"],
                                        "ai_tags": " ".join(r["tags"])})
            else:
                for v in batch:
                    r = _classify_local(
                        {"sheet": v["source_sheet"], "row": v["source_row"],
                         "title": v["title"], "tags": v["tags"], "transcript": v["transcript"]}
                    )
                    results.append({"id": v["id"], "category": r["category"],
                                    "ai_tags": " ".join(r["tags"])})
        else:
            for v in batch:
                r = _classify_local(
                    {"sheet": v["source_sheet"], "row": v["source_row"],
                     "title": v["title"], "tags": v["tags"], "transcript": v["transcript"]}
                )
                results.append({"id": v["id"], "category": r["category"],
                                "ai_tags": " ".join(r["tags"])})

        done += len(batch)
        if progress_cb:
            progress_cb(done, total, f"已分类 {done}/{total}")

    # 写回 DB
    # 增量模式（force=False）下已有 category 是权威值：订阅预设优先于 AI 分类，
    # 只补空 category 与缺失的 ai_tags，绝不改写既有分类；强制模式才整体重判。
    conn = connect(db_path)
    try:
        for r in results:
            cat = r["category"] if r["category"] in CATEGORIES else "其他"
            game = r.get("game") or ""
            if force:
                conn.execute(
                    "UPDATE videos SET category = ?, ai_tags = ?, "
                    "game = CASE WHEN ? = '' THEN game ELSE ? END, updated_at = ? WHERE id = ?",
                    (cat, r["ai_tags"], game, game, _now(), r["id"]),
                )
            else:
                conn.execute(
                    "UPDATE videos SET ai_tags = ?, "
                    "category = CASE WHEN COALESCE(category, '') = '' THEN ? ELSE category END, "
                    "game = CASE WHEN COALESCE(category, '') = '' AND ? <> '' THEN ? ELSE game END, "
                    "updated_at = ? WHERE id = ?",
                    (r["ai_tags"], cat, game, game, _now(), r["id"]),
                )
        conn.commit()
    finally:
        conn.close()
    return {"total": total, "classified": len(results),
            "llm_batches": (total + batch_size - 1) // batch_size if use_llm else 0}


def _llm_chat(ai_config: dict[str, str], prompt: str, timeout: int = 90) -> dict[str, Any]:
    """通用 LLM JSON 调用（分类等场景复用），失败返回 {}。"""
    try:
        import openai

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
            timeout=timeout,
            max_retries=1,
        )
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return _parse_llm_json(resp.choices[0].message.content)
    except Exception:
        return {}
