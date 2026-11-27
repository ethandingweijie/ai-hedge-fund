"""
app/backend/services/robo_strategy_storage.py
================================================
Storage for the Robo Strategy questionnaire + generated portfolio — a single
row per user, overwritten on every save (per product decision: "Regenerate"
recomputes and replaces in place, no history).

Tables (created on first use, shared run archive — Postgres in production,
SQLite locally, via `src.data.db`):
  • robo_strategy_profile    — one row per user, latest questionnaire answers
  • robo_strategy_portfolio  — one row per user, latest generated portfolio

Two tables (not one) so retaking the questionnaire doesn't have to touch the
last-generated portfolio row until the user explicitly clicks Generate again.

2026-11-27: ported off a private sqlite3 connection that followed
RUN_ARCHIVE_PATH to a path that does not exist in the production container.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timezone
from typing import Optional

from src.data import db

logger = logging.getLogger(__name__)


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS robo_strategy_profile (
        user_id      BIGINT PRIMARY KEY,
        answers_json TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS robo_strategy_portfolio (
        user_id        BIGINT PRIMARY KEY,
        mode           TEXT NOT NULL,
        portfolio_json TEXT NOT NULL,
        generated_at   TEXT NOT NULL
    )
    """,
]

_ensured_for: Optional[str] = None
_ensure_lock = threading.Lock()


def _ensure_tables() -> None:
    global _ensured_for
    target = "pg" if db.is_postgres() else db.get_db_path()
    if _ensured_for == target:
        return
    with _ensure_lock:
        if _ensured_for == target:
            return
        for ddl in _DDL:
            db.ensure_table(ddl)
        _ensured_for = target


def _sanitize(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def save_profile(user_id: int, answers: dict) -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO robo_strategy_profile (user_id, answers_json, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "answers_json = excluded.answers_json, updated_at = excluded.updated_at",
        [user_id, json.dumps(_sanitize(answers)), now],
    )


def get_profile(user_id: int) -> Optional[dict]:
    _ensure_tables()
    row = db.query_one(
        "SELECT answers_json FROM robo_strategy_profile WHERE user_id = ?",
        [user_id],
    )
    return json.loads(row["answers_json"]) if row else None


def save_portfolio(user_id: int, portfolio: dict, mode: str = "etf") -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO robo_strategy_portfolio (user_id, mode, portfolio_json, generated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "mode = excluded.mode, portfolio_json = excluded.portfolio_json, "
        "generated_at = excluded.generated_at",
        [user_id, mode, json.dumps(_sanitize(portfolio)), now],
    )


def get_portfolio(user_id: int) -> Optional[dict]:
    _ensure_tables()
    row = db.query_one(
        "SELECT portfolio_json FROM robo_strategy_portfolio WHERE user_id = ?",
        [user_id],
    )
    return json.loads(row["portfolio_json"]) if row else None


def delete_profile(user_id: int) -> None:
    _ensure_tables()
    db.execute("DELETE FROM robo_strategy_profile WHERE user_id = ?", [user_id])
    db.execute("DELETE FROM robo_strategy_portfolio WHERE user_id = ?", [user_id])
