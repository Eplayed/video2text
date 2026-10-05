"""发布审核状态机：图文包 / 文章稿 的「体检 → 放行 → 已发布」流转。

为什么要状态而不是让人自己看报告：实测一份成稿带着 6 处施工指令泄漏、通篇没有
一个「我」就出线了，因为报告写在文末、发的人没看。门禁必须是"不点体检就发不了"。

三条不可绕过的规矩：
  1. 指纹不符即失效——内容改过一个字，之前的通过作废，回到「需重新体检」；
  2. 有 FAIL 时只能凭理由人工放行，理由落库可追溯（不填就拒绝）；
  3. 发布动作仍然在平台手工完成，这里只记「已发布」，不接任何自动发布通道。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

KINDS = {"toutiao": "头条图文", "article": "文章稿"}
LABELS = {
    "unaudited": "未体检",
    "stale": "内容已改动，需重新体检",
    "fail": "不能发",
    "warn": "改完再发",
    "approved": "可以发",
    "published": "已发布",
}
PUBLISHABLE = ("approved", "published")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_reviews (
  kind TEXT NOT NULL, ref_id TEXT NOT NULL,
  status TEXT NOT NULL, fail_count INTEGER DEFAULT 0, warn_count INTEGER DEFAULT 0,
  score REAL, fingerprint TEXT, report TEXT, override_reason TEXT,
  published_at TEXT, updated_at TEXT,
  PRIMARY KEY (kind, ref_id)
)
"""


def fingerprint(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def ensure(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row(conn: sqlite3.Connection, kind: str, ref_id: str) -> Optional[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM content_reviews WHERE kind=? AND ref_id=?",
                        (kind, str(ref_id))).fetchone()


def state(db_path: str, kind: str, ref_id: str, content: str) -> Dict[str, Any]:
    """当前状态。指纹对不上就一律降级为 stale——这是整个门禁的关键，
    否则改完稿子还挂着「可以发」，门禁就成了摆设。"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(db_path)
        row = _row(conn, kind, ref_id)
    finally:
        conn.close()
    if not row:
        return _view("unaudited", 0, 0, None, "", "", None, "")
    stale = (row["fingerprint"] or "") != fingerprint(content)
    status = "stale" if stale and row["status"] in ("approved", "published", "warn", "fail") \
        else row["status"]
    return _view(status, row["fail_count"] or 0, row["warn_count"] or 0, row["score"],
                 row["report"] or "", row["override_reason"] or "",
                 row["published_at"], row["updated_at"] or "")


def _view(status: str, fails: int, warns: int, score, report: str, reason: str,
          published_at, updated_at: str) -> Dict[str, Any]:
    return {
        "status": status,
        "label": LABELS.get(status, status),
        "fails": fails, "warns": warns, "score": score,
        "report": report, "override_reason": reason,
        "published_at": published_at or "", "updated_at": updated_at,
        "can_publish": status in PUBLISHABLE,
        "needs_audit": status in ("unaudited", "stale", "fail", "warn"),
    }


def save_audit(db_path: str, kind: str, ref_id: str, content: str,
               result: Dict[str, Any]) -> Dict[str, Any]:
    """体检结果落库。零 FAIL 零 WARN 直接给「可以发」；只有 WARN 要人确认放行。"""
    verdict = result["verdict"]
    status = {"fail": "fail", "warn": "warn", "pass": "approved"}[verdict]
    conn = sqlite3.connect(db_path)
    try:
        ensure(db_path)
        conn.execute(
            "INSERT INTO content_reviews (kind, ref_id, status, fail_count, warn_count, score,"
            " fingerprint, report, override_reason, published_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(kind, ref_id) DO UPDATE SET status=excluded.status,"
            " fail_count=excluded.fail_count, warn_count=excluded.warn_count,"
            " score=excluded.score, fingerprint=excluded.fingerprint,"
            " report=excluded.report, override_reason='', updated_at=excluded.updated_at",
            (kind, str(ref_id), status, result["fails"], result["warns"], result.get("score"),
             fingerprint(content), result["report"], "", "", datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
    return state(db_path, kind, ref_id, content)


def approve(db_path: str, kind: str, ref_id: str, content: str, reason: str = "") -> Dict[str, Any]:
    """人工放行。有 FAIL 时必须给理由——不给就拒绝，别让门禁变成摆设。"""
    current = state(db_path, kind, ref_id, content)
    if current["status"] in ("unaudited", "stale"):
        raise ValueError("先跑一次体检再放行（当前状态：%s）" % current["label"])
    if current["fails"] > 0 and not (reason or "").strip():
        raise ValueError("还有 %d 项 FAIL，必须写明放行理由才能强推" % current["fails"])
    conn = sqlite3.connect(db_path)
    try:
        ensure(db_path)
        conn.execute(
            "UPDATE content_reviews SET status='approved', override_reason=?, updated_at=? "
            "WHERE kind=? AND ref_id=?",
            ((reason or "").strip(), datetime.now().isoformat(timespec="seconds"),
             kind, str(ref_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return state(db_path, kind, ref_id, content)


def mark_published(db_path: str, kind: str, ref_id: str, content: str) -> Dict[str, Any]:
    current = state(db_path, kind, ref_id, content)
    if not current["can_publish"]:
        raise ValueError("审核没过不能标记发布（当前：%s）" % current["label"])
    conn = sqlite3.connect(db_path)
    try:
        ensure(db_path)
        conn.execute(
            "UPDATE content_reviews SET status='published', published_at=?, updated_at=? "
            "WHERE kind=? AND ref_id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),
             datetime.now().isoformat(timespec="seconds"), kind, str(ref_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return state(db_path, kind, ref_id, content)


def counts(db_path: str) -> Dict[str, int]:
    """按状态计数，给列表页和首页待办板用。"""
    conn = sqlite3.connect(db_path)
    try:
        ensure(db_path)
        rows = conn.execute("SELECT status, COUNT(*) AS c FROM content_reviews GROUP BY status").fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}
