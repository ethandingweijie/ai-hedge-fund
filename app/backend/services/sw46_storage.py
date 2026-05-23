"""
app/backend/services/sw46_storage.py
=====================================
SQLite persistence for SW46 cohort runs. Reuses run_archive.db so admins
have one place to back up state.

Auto-migrating: the sw46_runs table is created on first call to any read /
write helper, mirroring the pattern in app/backend/services/analysis_service.py.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.sw46.schemas import SW46CohortResult


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


_SW46_DDL = """
CREATE TABLE IF NOT EXISTS sw46_runs (
    run_id                 TEXT PRIMARY KEY,
    created_at             TEXT NOT NULL,
    cohort_pooled_delta_e  REAL,
    ticker_count           INTEGER,
    failed_tickers         TEXT,
    results                TEXT NOT NULL
)
"""

_SW46_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sw46_runs_created_at ON sw46_runs(created_at DESC)",
]


def _ensure_table() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_SW46_DDL)
        for idx in _SW46_INDEXES:
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


def save_sw46_run(cohort: SW46CohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    results_json = json.dumps(payload.get("results", []))
    failed_json = json.dumps(payload.get("failed_tickers", []))
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sw46_runs
                (run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cohort.run_id,
                cohort.created_at,
                cohort.pooled_delta_e,
                cohort.ticker_count,
                failed_json,
                results_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_sw46_run() -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results "
            "FROM sw46_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_sw46_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results "
            "FROM sw46_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_sw46_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers "
            "FROM sw46_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "run_id": r[0],
            "created_at": r[1],
            "cohort_pooled_delta_e": r[2],
            "ticker_count": r[3],
            "failed_tickers": json.loads(r[4] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row: tuple) -> dict:
    return {
        "run_id": row[0],
        "created_at": row[1],
        "cohort_pooled_delta_e": row[2],
        "ticker_count": row[3],
        "failed_tickers": json.loads(row[4] or "[]"),
        "results": json.loads(row[5] or "[]"),
    }
