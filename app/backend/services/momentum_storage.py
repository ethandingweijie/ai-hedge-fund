"""
app/backend/services/momentum_storage.py
=========================================
Persistence for the momentum screen's cohort runs.

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The old raw-sqlite3 access gave every Railway
process its own private file, so a refresh landing on one replica stayed
invisible to the others — the research-ideas main page showed stale
summaries. momentum_runs was already copied to PG by the 2026-08 migration.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from src.data import db as _db
from src.research_ideas.momentum.schemas import MomentumCohortResult


logger = logging.getLogger(__name__)


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

_COLS = (
    "run_id, created_at, as_of, universe, ticker_count, "
    "long_count, short_count, sectors, failed_tickers, results"
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
        logger.warning("momentum_storage _ensure_table: %s", exc)


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SQL = """
INSERT INTO momentum_runs
    (run_id, created_at, as_of, universe, ticker_count,
     long_count, short_count, sectors, failed_tickers, results)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(run_id) DO UPDATE SET
    created_at = excluded.created_at,
    as_of = excluded.as_of,
    universe = excluded.universe,
    ticker_count = excluded.ticker_count,
    long_count = excluded.long_count,
    short_count = excluded.short_count,
    sectors = excluded.sectors,
    failed_tickers = excluded.failed_tickers,
    results = excluded.results
"""


def save_momentum_run(cohort: MomentumCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    _db.execute(
        _SAVE_SQL,
        [
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
        ],
    )


def get_latest_momentum_run() -> Optional[dict]:
    """
    Latest cohort with at least one ticker. Skips EMPTY cohorts so a failed
    refresh (FMP outage) never hides the previous good run. Falls back to the
    absolute-latest only when no non-empty cohort exists (fresh install).
    """
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM momentum_runs WHERE ticker_count > 0 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        row = _db.query_one(
            f"SELECT {_COLS} FROM momentum_runs ORDER BY created_at DESC LIMIT 1"
        )
    if not row:
        return None
    return _row_to_dict(row)


def get_momentum_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM momentum_runs WHERE run_id = ?",
        [run_id],
    )
    if not row:
        return None
    return _row_to_dict(row)


def list_momentum_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    rows = _db.query(
        "SELECT run_id, created_at, as_of, universe, ticker_count, "
        "long_count, short_count, failed_tickers "
        "FROM momentum_runs ORDER BY created_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "as_of": r["as_of"],
            "universe": r["universe"],
            "ticker_count": r["ticker_count"],
            "long_count": r["long_count"],
            "short_count": r["short_count"],
            "failed_tickers": json.loads(r["failed_tickers"] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row) -> dict:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "as_of": row["as_of"],
        "universe": row["universe"],
        "ticker_count": row["ticker_count"],
        "long_count": row["long_count"],
        "short_count": row["short_count"],
        "sectors": json.loads(row["sectors"] or "[]"),
        "failed_tickers": json.loads(row["failed_tickers"] or "[]"),
        "results": json.loads(row["results"] or "[]"),
    }
