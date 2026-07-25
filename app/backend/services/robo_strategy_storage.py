"""
app/backend/services/robo_strategy_storage.py
================================================
SQLite-backed storage for the Robo Strategy questionnaire + generated
portfolio — a single row per user, overwritten on every save (per product
decision: "Regenerate" recomputes and replaces in place, no history).

Tables (auto-migrated, shared run_archive.db):
  • robo_strategy_profile    — one row per user, latest questionnaire answers
  • robo_strategy_portfolio  — one row per user, latest generated portfolio

Two tables (not one) so retaking the questionnaire doesn't have to touch the
last-generated portfolio row until the user explicitly clicks Generate again.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
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
    CREATE TABLE IF NOT EXISTS robo_strategy_profile (
        user_id      INTEGER PRIMARY KEY,
        answers_json TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS robo_strategy_portfolio (
        user_id        INTEGER PRIMARY KEY,
        mode           TEXT NOT NULL,    -- last-viewed 'etf' | 'stocks', UI convenience only
        portfolio_json TEXT NOT NULL,
        generated_at   TEXT NOT NULL
    )
    """,
]


def _ensure_tables() -> None:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect()
    try:
        for ddl in _DDL:
            conn.execute(ddl)
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


def save_profile(user_id: int, answers: dict) -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO robo_strategy_profile (user_id, answers_json, updated_at) "
            "VALUES (?, ?, ?)",
            (user_id, json.dumps(_sanitize(answers)), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile(user_id: int) -> Optional[dict]:
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT answers_json FROM robo_strategy_profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return json.loads(row["answers_json"]) if row else None
    finally:
        conn.close()


def save_portfolio(user_id: int, portfolio: dict, mode: str = "etf") -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO robo_strategy_portfolio "
            "(user_id, mode, portfolio_json, generated_at) VALUES (?, ?, ?, ?)",
            (user_id, mode, json.dumps(_sanitize(portfolio)), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_portfolio(user_id: int) -> Optional[dict]:
    _ensure_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT portfolio_json FROM robo_strategy_portfolio WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return json.loads(row["portfolio_json"]) if row else None
    finally:
        conn.close()


def delete_profile(user_id: int) -> None:
    _ensure_tables()
    conn = _connect()
    try:
        conn.execute("DELETE FROM robo_strategy_profile WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM robo_strategy_portfolio WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
