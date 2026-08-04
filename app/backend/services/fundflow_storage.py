"""
app/backend/services/fundflow_storage.py
=========================================
SQLite persistence for the geographic fund-flow screen's cohort runs. Mirrors
momentum_storage.py — shared run_archive.db, auto-migrating table,
skip-empty-cohort guard on the "latest" query.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Any, Optional

from src.research_ideas.fundflow.schemas import FundFlowCohortResult


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
CREATE TABLE IF NOT EXISTS fundflow_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    as_of           TEXT,
    universe        TEXT,
    region_count    INTEGER,
    inflow_count    INTEGER,
    outflow_count   INTEGER,
    summary         TEXT,
    regions         TEXT NOT NULL,
    benchmarks      TEXT,
    failed_regions  TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fundflow_runs_created_at "
    "ON fundflow_runs(created_at DESC)",
]

_COLS = (
    "run_id, created_at, as_of, universe, region_count, inflow_count, "
    "outflow_count, summary, regions, benchmarks, failed_regions"
)


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
    """NaN/Inf are legal Python floats but not legal JSON — a single one would
    make the stored row unparseable on read."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def save_fundflow_run(cohort: FundFlowCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    conn = _connect()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO fundflow_runs ({_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cohort.run_id,
                cohort.created_at,
                cohort.as_of,
                cohort.universe,
                cohort.region_count,
                cohort.inflow_count,
                cohort.outflow_count,
                json.dumps(payload.get("summary")),
                json.dumps(payload.get("regions", [])),
                json.dumps(payload.get("benchmarks", [])),
                json.dumps(payload.get("failed_regions", [])),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_fundflow_run() -> Optional[dict]:
    """
    Latest cohort with at least one region. Skips EMPTY cohorts so a failed
    refresh (FMP outage) never hides the previous good run. Falls back to the
    absolute-latest only when no non-empty cohort exists (fresh install).
    """
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {_COLS} FROM fundflow_runs WHERE region_count > 0 "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                f"SELECT {_COLS} FROM fundflow_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def get_fundflow_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {_COLS} FROM fundflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def list_fundflow_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, created_at, as_of, universe, region_count, "
            "inflow_count, outflow_count, failed_regions "
            "FROM fundflow_runs ORDER BY created_at DESC LIMIT ?",
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
            "region_count": r[4],
            "inflow_count": r[5],
            "outflow_count": r[6],
            "failed_regions": json.loads(r[7] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row: tuple) -> dict:
    return {
        "run_id": row[0],
        "created_at": row[1],
        "as_of": row[2],
        "universe": row[3],
        "region_count": row[4],
        "inflow_count": row[5],
        "outflow_count": row[6],
        "summary": json.loads(row[7]) if row[7] else None,
        "regions": json.loads(row[8] or "[]"),
        "benchmarks": json.loads(row[9] or "[]"),
        "failed_regions": json.loads(row[10] or "[]"),
    }
