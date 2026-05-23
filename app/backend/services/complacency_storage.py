"""
app/backend/services/complacency_storage.py
============================================
SQLite persistence for Complacency screener cohorts. Mirrors sw46_storage.py
patterns — shared run_archive.db, auto-migrating table.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.complacency.schemas import ComplacencyCohortResult


logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    import os

    env_path = os.environ.get("RUN_ARCHIVE_PATH")
    if env_path:
        return env_path
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent
    return str(project_root / "src" / "data" / "run_archive.db")


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or _get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_DDL = """
CREATE TABLE IF NOT EXISTS complacency_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    universe        TEXT,
    ticker_count    INTEGER,
    gate_passers    INTEGER,
    failed_tickers  TEXT,
    results         TEXT NOT NULL
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_complacency_runs_created_at "
    "ON complacency_runs(created_at DESC)",
]


def _ensure_table() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def save_complacency_run(cohort: ComplacencyCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    results_json = json.dumps(payload.get("results", []))
    failed_json = json.dumps(payload.get("failed_tickers", []))
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO complacency_runs
                (run_id, created_at, universe, ticker_count, gate_passers,
                 failed_tickers, results)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cohort.run_id,
                cohort.created_at,
                cohort.universe,
                cohort.ticker_count,
                cohort.gate_passers,
                failed_json,
                results_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_complacency_run() -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, universe, ticker_count, gate_passers, "
            "failed_tickers, results FROM complacency_runs "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_complacency_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, universe, ticker_count, gate_passers, "
            "failed_tickers, results FROM complacency_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_complacency_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, universe, ticker_count, gate_passers, failed_tickers "
            "FROM complacency_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "run_id": r[0],
            "created_at": r[1],
            "universe": r[2],
            "ticker_count": r[3],
            "gate_passers": r[4],
            "failed_tickers": json.loads(r[5] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row: tuple) -> dict:
    return {
        "run_id": row[0],
        "created_at": row[1],
        "universe": row[2],
        "ticker_count": row[3],
        "gate_passers": row[4],
        "failed_tickers": json.loads(row[5] or "[]"),
        "results": json.loads(row[6] or "[]"),
    }
