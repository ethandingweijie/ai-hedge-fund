"""
app/backend/services/hk50_storage.py
=====================================
SQLite persistence for HK50 ("Long China / HK") cohort runs. Reuses
run_archive.db so admins have one place to back up state.

Auto-migrating: the hk50_runs table is created on first call to any read /
write helper, mirroring app/backend/services/sw46_storage.py.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.hk50.schemas import HK50CohortResult


logger = logging.getLogger(__name__)


# ─── DB path (shared with analysis_service.py) ────────────────────────────


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


_HK50_DDL = """
CREATE TABLE IF NOT EXISTS hk50_runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    ticker_count      INTEGER,
    avg_growth        REAL,
    avg_dividend      REAL,
    median_p_iv15     REAL,
    lead_growth_count INTEGER,
    failed_tickers    TEXT,
    results           TEXT NOT NULL
)
"""

_HK50_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hk50_runs_created_at ON hk50_runs(created_at DESC)",
]


def _ensure_table() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_HK50_DDL)
        for idx in _HK50_INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


# ─── JSON sanitiser (NaN / Inf -> None) ────────────────────────────────────


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ─── Public API ────────────────────────────────────────────────────────────


def save_hk50_run(cohort: HK50CohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    results_json = json.dumps(payload.get("results", []))
    failed_json = json.dumps(payload.get("failed_tickers", []))
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hk50_runs
                (run_id, created_at, ticker_count, avg_growth, avg_dividend,
                 median_p_iv15, lead_growth_count, failed_tickers, results)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cohort.run_id,
                cohort.created_at,
                cohort.ticker_count,
                cohort.avg_growth,
                cohort.avg_dividend,
                cohort.median_p_iv15,
                cohort.lead_growth_count,
                failed_json,
                results_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_hk50_run() -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, ticker_count, avg_growth, avg_dividend, "
            "median_p_iv15, lead_growth_count, failed_tickers, results "
            "FROM hk50_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_hk50_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, ticker_count, avg_growth, avg_dividend, "
            "median_p_iv15, lead_growth_count, failed_tickers, results "
            "FROM hk50_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_hk50_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, ticker_count, avg_growth, avg_dividend, "
            "median_p_iv15, lead_growth_count, failed_tickers "
            "FROM hk50_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "run_id": r[0],
            "created_at": r[1],
            "ticker_count": r[2],
            "avg_growth": r[3],
            "avg_dividend": r[4],
            "median_p_iv15": r[5],
            "lead_growth_count": r[6],
            "failed_tickers": json.loads(r[7] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row: tuple) -> dict:
    return {
        "run_id": row[0],
        "created_at": row[1],
        "ticker_count": row[2],
        "avg_growth": row[3],
        "avg_dividend": row[4],
        "median_p_iv15": row[5],
        "lead_growth_count": row[6],
        "failed_tickers": json.loads(row[7] or "[]"),
        "results": json.loads(row[8] or "[]"),
    }
