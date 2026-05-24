"""
app/backend/services/contrarian_storage.py
============================================
SQLite-backed storage for "Research Idea of the Day" (contrarian deep-value
hypotheses), the chat thread per idea, and the user's shortlist.

Tables (auto-migrated, shared run_archive.db):
  • contrarian_ideas      — one row per generated idea
  • contrarian_idea_chat  — one row per chat message
  • contrarian_shortlist  — one row per shortlisted idea (with snapshot)
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS contrarian_ideas (
        idea_id        TEXT PRIMARY KEY,
        ticker         TEXT NOT NULL,
        company_name   TEXT,
        generated_at   TEXT NOT NULL,
        deleted_at     TEXT,
        model_used     TEXT,
        cost_usd       REAL,
        payload        TEXT NOT NULL    -- JSON of the full ContrarianIdea
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contrarian_idea_chat (
        message_id   TEXT PRIMARY KEY,
        idea_id      TEXT NOT NULL,
        role         TEXT NOT NULL,    -- 'user' | 'assistant'
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        cost_usd     REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contrarian_shortlist (
        idea_id          TEXT PRIMARY KEY,
        shortlisted_at   TEXT NOT NULL,
        user_note        TEXT,
        idea_snapshot    TEXT NOT NULL    -- frozen JSON of the idea at shortlist time
    )
    """,
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_contrarian_ideas_generated "
    "ON contrarian_ideas(generated_at DESC) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_contrarian_idea_chat_idea "
    "ON contrarian_idea_chat(idea_id, created_at ASC)",
    "CREATE INDEX IF NOT EXISTS idx_contrarian_shortlist_at "
    "ON contrarian_shortlist(shortlisted_at DESC)",
]


def _ensure_tables() -> None:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect()
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def _sanitize(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ─── Ideas ────────────────────────────────────────────────────────────────


def save_idea(idea: dict) -> None:
    """Persist a newly-generated idea."""
    _ensure_tables()
    payload = _sanitize(idea)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO contrarian_ideas "
            "(idea_id, ticker, company_name, generated_at, model_used, cost_usd, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                idea["idea_id"],
                idea.get("ticker", "").upper(),
                idea.get("company_name"),
                idea["generated_at"],
                idea.get("model_used"),
                idea.get("cost_usd"),
                json.dumps(payload),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_idea(idea_id: str) -> Optional[dict]:
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload, deleted_at FROM contrarian_ideas WHERE idea_id = ?",
            (idea_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    idea = json.loads(row[0])
    if row[1]:
        idea["_deleted_at"] = row[1]
    return idea


def get_latest_idea() -> Optional[dict]:
    """Return the most recent non-deleted idea, or None."""
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload FROM contrarian_ideas "
            "WHERE deleted_at IS NULL "
            "ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def list_ideas(limit: int = 20, include_deleted: bool = False) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    try:
        if include_deleted:
            rows = conn.execute(
                "SELECT payload, deleted_at FROM contrarian_ideas "
                "ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT payload, deleted_at FROM contrarian_ideas "
                "WHERE deleted_at IS NULL "
                "ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        idea = json.loads(r[0])
        if r[1]:
            idea["_deleted_at"] = r[1]
        out.append(idea)
    return out


def soft_delete_idea(idea_id: str) -> bool:
    """Mark as deleted (preserves chat history). Returns True if a row was updated."""
    _ensure_tables()
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE contrarian_ideas SET deleted_at = ? "
            "WHERE idea_id = ? AND deleted_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), idea_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ─── Chat ─────────────────────────────────────────────────────────────────


def append_chat_message(
    idea_id: str, role: str, content: str, cost_usd: Optional[float] = None,
) -> dict:
    _ensure_tables()
    message_id = uuid.uuid4().hex[:16]
    created_at = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO contrarian_idea_chat "
            "(message_id, idea_id, role, content, created_at, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, idea_id, role, content, created_at, cost_usd),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "message_id": message_id,
        "idea_id": idea_id,
        "role": role,
        "content": content,
        "created_at": created_at,
        "cost_usd": cost_usd,
    }


def list_chat_messages(idea_id: str) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT message_id, idea_id, role, content, created_at, cost_usd "
            "FROM contrarian_idea_chat WHERE idea_id = ? "
            "ORDER BY created_at ASC",
            (idea_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "message_id": r[0], "idea_id": r[1], "role": r[2],
            "content": r[3], "created_at": r[4], "cost_usd": r[5],
        }
        for r in rows
    ]


# ─── Shortlist ────────────────────────────────────────────────────────────


def add_to_shortlist(idea_id: str, user_note: Optional[str] = None) -> Optional[dict]:
    """
    Snapshots the idea and adds it to the shortlist. Returns the shortlist
    entry, or None if the idea doesn't exist.
    """
    _ensure_tables()
    idea = get_idea(idea_id)
    if not idea:
        return None
    shortlisted_at = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO contrarian_shortlist "
            "(idea_id, shortlisted_at, user_note, idea_snapshot) "
            "VALUES (?, ?, ?, ?)",
            (idea_id, shortlisted_at, user_note, json.dumps(_sanitize(idea))),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "idea_id": idea_id,
        "shortlisted_at": shortlisted_at,
        "user_note": user_note,
        "idea_snapshot": idea,
    }


def list_shortlist(limit: int = 50) -> list[dict]:
    _ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT idea_id, shortlisted_at, user_note, idea_snapshot "
            "FROM contrarian_shortlist "
            "ORDER BY shortlisted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "idea_id": r[0],
            "shortlisted_at": r[1],
            "user_note": r[2],
            "idea_snapshot": json.loads(r[3]),
        }
        for r in rows
    ]


def remove_from_shortlist(idea_id: str) -> bool:
    _ensure_tables()
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM contrarian_shortlist WHERE idea_id = ?",
            (idea_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_shortlisted(idea_id: str) -> bool:
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM contrarian_shortlist WHERE idea_id = ?",
            (idea_id,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None
