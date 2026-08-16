"""
app/backend/services/contrarian_storage.py
============================================
Storage for "Research Idea of the Day" (contrarian deep-value hypotheses),
the chat thread per idea, and the user's shortlist.

Tables:
  • contrarian_ideas      — one row per generated idea
  • contrarian_idea_chat  — one row per chat message
  • contrarian_shortlist  — one row per shortlisted idea (with snapshot)

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The old raw-sqlite3 access gave every Railway
process its own private file, so ideas generated on one replica were
invisible to the others. All three tables were already copied to PG by the
2026-08 migration.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Optional

from src.data import db as _db

logger = logging.getLogger(__name__)


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
        payload        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contrarian_idea_chat (
        message_id   TEXT PRIMARY KEY,
        idea_id      TEXT NOT NULL,
        role         TEXT NOT NULL,
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
        idea_snapshot    TEXT NOT NULL
    )
    """,
]
# contrarian_ideas.payload = JSON of the full ContrarianIdea;
# contrarian_idea_chat.role = 'user' | 'assistant';
# contrarian_shortlist.idea_snapshot = frozen JSON of the idea at shortlist time.

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_contrarian_ideas_generated "
    "ON contrarian_ideas(generated_at DESC) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_contrarian_idea_chat_idea "
    "ON contrarian_idea_chat(idea_id, created_at ASC)",
    "CREATE INDEX IF NOT EXISTS idx_contrarian_shortlist_at "
    "ON contrarian_shortlist(shortlisted_at DESC)",
]


# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join(_DDL + _INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("contrarian_storage _ensure_tables: %s", exc)


def _sanitize(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ─── Ideas ────────────────────────────────────────────────────────────────


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
# deleted_at is included so a re-save keeps the exact INSERT OR REPLACE
# semantics it had before (a replaced row resets to not-deleted).
_SAVE_IDEA_SQL = """
INSERT INTO contrarian_ideas
    (idea_id, ticker, company_name, generated_at, deleted_at,
     model_used, cost_usd, payload)
VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
ON CONFLICT(idea_id) DO UPDATE SET
    ticker = excluded.ticker,
    company_name = excluded.company_name,
    generated_at = excluded.generated_at,
    deleted_at = excluded.deleted_at,
    model_used = excluded.model_used,
    cost_usd = excluded.cost_usd,
    payload = excluded.payload
"""


def save_idea(idea: dict) -> None:
    """Persist a newly-generated idea."""
    _ensure_tables()
    payload = _sanitize(idea)
    _db.execute(
        _SAVE_IDEA_SQL,
        [
            idea["idea_id"],
            idea.get("ticker", "").upper(),
            idea.get("company_name"),
            idea["generated_at"],
            idea.get("model_used"),
            idea.get("cost_usd"),
            json.dumps(payload),
        ],
    )


def get_idea(idea_id: str) -> Optional[dict]:
    _ensure_tables()
    row = _db.query_one(
        "SELECT payload, deleted_at FROM contrarian_ideas WHERE idea_id = ?",
        [idea_id],
    )
    if not row:
        return None
    idea = json.loads(row["payload"])
    if row["deleted_at"]:
        idea["_deleted_at"] = row["deleted_at"]
    return idea


def get_latest_idea() -> Optional[dict]:
    """Return the most recent non-deleted idea, or None."""
    _ensure_tables()
    row = _db.query_one(
        "SELECT payload FROM contrarian_ideas "
        "WHERE deleted_at IS NULL "
        "ORDER BY generated_at DESC LIMIT 1"
    )
    return json.loads(row["payload"]) if row else None


def list_ideas(limit: int = 20, include_deleted: bool = False) -> list[dict]:
    _ensure_tables()
    if include_deleted:
        rows = _db.query(
            "SELECT payload, deleted_at FROM contrarian_ideas "
            "ORDER BY generated_at DESC LIMIT ?",
            [limit],
        )
    else:
        rows = _db.query(
            "SELECT payload, deleted_at FROM contrarian_ideas "
            "WHERE deleted_at IS NULL "
            "ORDER BY generated_at DESC LIMIT ?",
            [limit],
        )
    out = []
    for r in rows:
        idea = json.loads(r["payload"])
        if r["deleted_at"]:
            idea["_deleted_at"] = r["deleted_at"]
        out.append(idea)
    return out


def soft_delete_idea(idea_id: str) -> bool:
    """Mark as deleted (preserves chat history). Returns True if a row was updated."""
    _ensure_tables()
    rowcount = _db.execute(
        "UPDATE contrarian_ideas SET deleted_at = ? "
        "WHERE idea_id = ? AND deleted_at IS NULL",
        [datetime.now(timezone.utc).isoformat(), idea_id],
    )
    return rowcount > 0


# ─── Chat ─────────────────────────────────────────────────────────────────


def append_chat_message(
    idea_id: str, role: str, content: str, cost_usd: Optional[float] = None,
) -> dict:
    _ensure_tables()
    message_id = uuid.uuid4().hex[:16]
    created_at = datetime.now(timezone.utc).isoformat()
    _db.execute(
        "INSERT INTO contrarian_idea_chat "
        "(message_id, idea_id, role, content, created_at, cost_usd) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [message_id, idea_id, role, content, created_at, cost_usd],
    )
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
    rows = _db.query(
        "SELECT message_id, idea_id, role, content, created_at, cost_usd "
        "FROM contrarian_idea_chat WHERE idea_id = ? "
        "ORDER BY created_at ASC",
        [idea_id],
    )
    return [
        {
            "message_id": r["message_id"], "idea_id": r["idea_id"],
            "role": r["role"], "content": r["content"],
            "created_at": r["created_at"], "cost_usd": r["cost_usd"],
        }
        for r in rows
    ]


# ─── Shortlist ────────────────────────────────────────────────────────────


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SHORTLIST_SQL = """
INSERT INTO contrarian_shortlist
    (idea_id, shortlisted_at, user_note, idea_snapshot)
VALUES (?, ?, ?, ?)
ON CONFLICT(idea_id) DO UPDATE SET
    shortlisted_at = excluded.shortlisted_at,
    user_note = excluded.user_note,
    idea_snapshot = excluded.idea_snapshot
"""


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
    _db.execute(
        _SAVE_SHORTLIST_SQL,
        [idea_id, shortlisted_at, user_note, json.dumps(_sanitize(idea))],
    )
    return {
        "idea_id": idea_id,
        "shortlisted_at": shortlisted_at,
        "user_note": user_note,
        "idea_snapshot": idea,
    }


def list_shortlist(limit: int = 50) -> list[dict]:
    _ensure_tables()
    rows = _db.query(
        "SELECT idea_id, shortlisted_at, user_note, idea_snapshot "
        "FROM contrarian_shortlist "
        "ORDER BY shortlisted_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "idea_id": r["idea_id"],
            "shortlisted_at": r["shortlisted_at"],
            "user_note": r["user_note"],
            "idea_snapshot": json.loads(r["idea_snapshot"]),
        }
        for r in rows
    ]


def remove_from_shortlist(idea_id: str) -> bool:
    _ensure_tables()
    rowcount = _db.execute(
        "DELETE FROM contrarian_shortlist WHERE idea_id = ?",
        [idea_id],
    )
    return rowcount > 0


def is_shortlisted(idea_id: str) -> bool:
    _ensure_tables()
    row = _db.query_one(
        "SELECT 1 AS one FROM contrarian_shortlist WHERE idea_id = ?",
        [idea_id],
    )
    return row is not None
