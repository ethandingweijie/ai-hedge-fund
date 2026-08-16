"""
app/backend/services/fundflow_storage.py
=========================================
Persistence for the geographic fund-flow screen's cohort runs.

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The old raw-sqlite3 access gave every Railway
process its own private file, so a refresh (or the Monday scheduled cycle,
which runs in the worker) stayed invisible to the web replicas — the
research-ideas main page showed stale summaries. fundflow_runs was already
copied to PG by the 2026-08 migration.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from src.data import db as _db
from src.research_ideas.fundflow.schemas import FundFlowCohortResult


logger = logging.getLogger(__name__)


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


# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_table() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join([_DDL] + _INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("fundflow_storage _ensure_table: %s", exc)


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


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SQL = (
    f"INSERT INTO fundflow_runs ({_COLS}) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(run_id) DO UPDATE SET "
    "created_at = excluded.created_at, "
    "as_of = excluded.as_of, "
    "universe = excluded.universe, "
    "region_count = excluded.region_count, "
    "inflow_count = excluded.inflow_count, "
    "outflow_count = excluded.outflow_count, "
    "summary = excluded.summary, "
    "regions = excluded.regions, "
    "benchmarks = excluded.benchmarks, "
    "failed_regions = excluded.failed_regions"
)


def save_fundflow_run(cohort: FundFlowCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    _db.execute(
        _SAVE_SQL,
        [
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
        ],
    )


def get_latest_fundflow_run() -> Optional[dict]:
    """
    Latest cohort with at least one region. Skips EMPTY cohorts so a failed
    refresh (FMP outage) never hides the previous good run. Falls back to the
    absolute-latest only when no non-empty cohort exists (fresh install).
    """
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM fundflow_runs WHERE region_count > 0 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        row = _db.query_one(
            f"SELECT {_COLS} FROM fundflow_runs ORDER BY created_at DESC LIMIT 1"
        )
    return _row_to_dict(row) if row else None


def get_fundflow_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM fundflow_runs WHERE run_id = ?", [run_id]
    )
    return _row_to_dict(row) if row else None


def list_fundflow_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    rows = _db.query(
        "SELECT run_id, created_at, as_of, universe, region_count, "
        "inflow_count, outflow_count, failed_regions "
        "FROM fundflow_runs ORDER BY created_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "as_of": r["as_of"],
            "universe": r["universe"],
            "region_count": r["region_count"],
            "inflow_count": r["inflow_count"],
            "outflow_count": r["outflow_count"],
            "failed_regions": json.loads(r["failed_regions"] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row) -> dict:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "as_of": row["as_of"],
        "universe": row["universe"],
        "region_count": row["region_count"],
        "inflow_count": row["inflow_count"],
        "outflow_count": row["outflow_count"],
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "regions": json.loads(row["regions"] or "[]"),
        "benchmarks": json.loads(row["benchmarks"] or "[]"),
        "failed_regions": json.loads(row["failed_regions"] or "[]"),
    }
