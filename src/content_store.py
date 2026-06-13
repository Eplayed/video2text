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


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
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
            model TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ai_summaries_type ON ai_summaries(summary_type);
        CREATE INDEX IF NOT EXISTS idx_ai_summaries_video_id ON ai_summaries(video_id);
        """
    )
    conn.commit()


def sync_excel_to_db(excel_path: str | Path, db_path: str | Path) -> dict[str, int]:
    wb = openpyxl.load_workbook(str(excel_path), read_only=True, data_only=True)
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


def list_summaries(
    db_path: str | Path,
    summary_type: str = "",
    query: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        where = []
        params: list[Any] = []
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
        rows = conn.execute(
            f"""
            SELECT
                s.id, s.summary_type, s.title, s.outline, s.content, s.keywords,
                s.model, s.status, s.created_at, s.updated_at,
                v.id AS video_id, v.aweme_id, v.author, v.title AS video_title,
                v.source_sheet, v.source_row, v.video_url
            FROM ai_summaries s
            JOIN videos v ON v.id = s.video_id
            {clause}
            ORDER BY s.updated_at DESC, s.id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [_dict(row) for row in rows]
    finally:
        conn.close()


def get_summary(db_path: str | Path, summary_id: int) -> dict[str, Any] | None:
    conn = connect(db_path)
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
    conn = connect(db_path)
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


def save_summary(
    db_path: str | Path,
    video_id: int,
    summary_type: str,
    result: dict[str, Any],
    model: str = "",
    status: str = "draft",
) -> int:
    conn = connect(db_path)
    now = _now()
    try:
        cursor = conn.execute(
            """
            INSERT INTO ai_summaries (
                video_id, summary_type, title, outline, content, keywords,
                model, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                summary_type,
                result.get("title", ""),
                _json_or_text(result.get("outline", "")),
                result.get("content", ""),
                _json_or_text(result.get("keywords", "")),
                model,
                status,
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
) -> None:
    conn = connect(db_path)
    now = _now()
    try:
        conn.execute(
            """
            UPDATE ai_summaries SET
                title=?, outline=?, content=?, keywords=?,
                model=?, status=?, updated_at=?
            WHERE id=?
            """,
            (
                result.get("title", ""),
                _json_or_text(result.get("outline", "")),
                result.get("content", ""),
                _json_or_text(result.get("keywords", "")),
                model,
                status,
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


def generate_summary(
    video: dict[str, Any],
    summary_type: str,
    ai_config: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str, str]:
    ai_config = ai_config or {}
    method = (ai_config.get("method") or "skip").strip()
    api_key = ai_config.get("api_key", "")
    model = ai_config.get("model", "")
    if method != "skip" and api_key:
        ai_result = _generate_with_llm(video, summary_type, ai_config)
        if "error" not in ai_result:
            return ai_result, model or method, "ai"
        fallback = _generate_local(video, summary_type)
        fallback["content"] += f"\n\n[AI生成失败，已保存规则版草稿：{ai_result['error']}]"
        return fallback, model or method, "draft"
    return _generate_local(video, summary_type), "local-template", "draft"


def _generate_with_llm(
    video: dict[str, Any],
    summary_type: str,
    ai_config: dict[str, str],
) -> dict[str, Any]:
    prompt = _prompt(video, summary_type)
    try:
        import openai

        client = openai.OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("api_base") or "https://api.openai.com/v1",
        )
        resp = client.chat.completions.create(
            model=ai_config.get("model") or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        return {"error": str(exc)}


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
        outline = ["核心概念", "候选人回答要点", "追问方向", "可核查风险"]
        questions = "\n".join(
            f"{i}. {point.rstrip('。')}，面试时可以怎么解释？"
            for i, point in enumerate(key_points[:5], 1)
        )
        content = (
            f"来源作者：{author}\n\n"
            f"候选题目：\n{questions}\n\n"
            "参考回答要点：\n"
            + "\n".join(f"- {point}" for point in key_points)
            + "\n\n复核提醒：规则版草稿只基于 ASR 提取，发布前需要人工核对术语和事实。"
        )
    else:
        out_title = f"游戏攻略：{title[:40]}"
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
    }


def _prompt(video: dict[str, Any], summary_type: str) -> str:
    if summary_type == "ai_interview":
        task = "整理成 AI 面试题库条目，包含题目、考察点、参考回答、追问、易错点。"
    else:
        task = "整理成游戏攻略草稿，包含适用场景、步骤、机制解释、避坑提醒、事实核查清单。"
    return f"""你是内容整理助手。请只基于输入材料，不要编造事实，把短视频口播整理成结构化内容。

任务：{task}

输出 JSON：
{{
  "title": "整理后的标题",
  "outline": ["一级要点1", "一级要点2"],
  "content": "完整正文，使用 Markdown",
  "keywords": ["关键词1", "关键词2"]
}}

视频标题：{video.get('title') or ''}
作者：{video.get('author') or ''}
标签：{video.get('tags') or ''}
口播 ASR：
{video.get('transcript') or ''}
"""


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


def _json_or_text(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"[。！？!?；;\n]+", cleaned)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


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
