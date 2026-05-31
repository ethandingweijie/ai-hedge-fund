"""
app/backend/services/momentum_storage.py
=========================================
SQLite persistence for the momentum screen's cohort runs. Mirrors
complacency_storage.py — shared run_archive.db, auto-migrating table,
skip-empty-cohort guard on the "latest" query.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.momentum.schemas import MomentumCohortResult


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
CREATE TABLE IF NOT EXISTS momentum_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    as_of           TEXT,
    universe        TEXT,
    ticker_count    INTEGER,
    long_count      INTEGER,
    short_count     INTEGER,
    sectors         TEXT,
    failed_tickers  TEXT,
    results         TEXT NOT NULL
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_momentum_runs_created_at "
    "ON momentum_runs(created_at DESC)",
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


def save_momentum_run(cohort: MomentumCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO momentum_runs
                (run_id, created_at, as_of, universe, ticker_count,
                 long_count, short_count, sectors, failed_tickers, results)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cohort.run_id,
                cohort.created_at,
                cohort.as_of,
                cohort.universe,
                cohort.ticker_count,
                cohort.long_count,
                cohort.short_count,
                json.dumps(payload.get("sectors", [])),
                json.dumps(payload.get("failed_tickers", [])),
                json.dumps(payload.get("results", [])),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_momentum_run() -> Optional[dict]:
    """
    Latest cohort with at least one ticker. Skips EMPTY cohorts so a failed
    refresh (FMP outage) never hides the previous good run. Falls back to the
    absolute-latest only when no non-empty cohort exists (fresh install).
    """
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, as_of, universe, ticker_count, "
            "long_count, short_count, sectors, failed_tickers, results "
            "FROM momentum_runs WHERE ticker_count > 0 "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT run_id, created_at, as_of, universe, ticker_count, "
                "long_count, short_count, sectors, failed_tickers, results "
                "FROM momentum_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_momentum_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, as_of, universe, ticker_count, "
            "long_count, short_count, sectors, failed_tickers, results "
            "FROM momentum_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_momentum_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, as_of, universe, ticker_count, "
            "long_count, short_count, failed_tickers "
            "FROM momentum_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "run_id": r[0],
            "created_at": r[1],
            "as_of": r[2],
            "universe": r[3],
            "ticker_count": r[4],
            "long_count": r[5],
            "short_count": r[6],
            "failed_tickers": json.loads(r[7] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row: tuple) -> dict:
    return {
        "run_id": row[0],
        "created_at": row[1],
        "as_of": row[2],
        "universe": row[3],
        "ticker_count": row[4],
        "long_count": row[5],
        "short_count": row[6],
        "sectors": json.loads(row[7] or "[]"),
        "failed_tickers": json.loads(row[8] or "[]"),
        "results": json.loads(row[9] or "[]"),
    }
