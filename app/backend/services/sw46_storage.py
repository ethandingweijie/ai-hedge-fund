"""
app/backend/services/sw46_storage.py
=====================================
Persistence for SW46 cohort runs.

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production — same pattern as the screener fix (f311cd5) and the
R1 knowledge_graph migration. The old raw-sqlite3 access against
RUN_ARCHIVE_PATH gave every Railway process (2 web replicas + worker +
scheduler) its own private copy, so a refresh landing on one process stayed
invisible to the others — the research-ideas main page showed stale
summaries. The sw46_runs table was already copied to PG by the 2026-08
migration, so no schema work was needed.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from src.data import db as _db
from src.research_ideas.sw46.schemas import SW46CohortResult


logger = logging.getLogger(__name__)


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


# DDL target memo so CREATE TABLE IF NOT EXISTS runs once per database
# (per-process in PG mode, per-file in SQLite mode).
_tables_ready_key: Optional[tuple] = None


def _ensure_table() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    try:
        _db.execute_script(";".join([_SW46_DDL] + _SW46_INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("sw46_storage _ensure_table: %s", exc)


# ─── JSON sanitiser (NaN / Inf -> None) ────────────────────────────────────


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
INSERT INTO sw46_runs
    (run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(run_id) DO UPDATE SET
    created_at = excluded.created_at,
    cohort_pooled_delta_e = excluded.cohort_pooled_delta_e,
    ticker_count = excluded.ticker_count,
    failed_tickers = excluded.failed_tickers,
    results = excluded.results
"""


# ─── Public API ────────────────────────────────────────────────────────────


def save_sw46_run(cohort: SW46CohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    results_json = json.dumps(payload.get("results", []))
    failed_json = json.dumps(payload.get("failed_tickers", []))
    _db.execute(
        _SAVE_SQL,
        [
            cohort.run_id,
            cohort.created_at,
            cohort.pooled_delta_e,
            cohort.ticker_count,
            failed_json,
            results_json,
        ],
    )


def get_latest_sw46_run() -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results "
        "FROM sw46_runs ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        return None
    return _row_to_dict(row)


def get_sw46_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers, results "
        "FROM sw46_runs WHERE run_id = ?",
        [run_id],
    )
    if not row:
        return None
    return _row_to_dict(row)


def list_sw46_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    rows = _db.query(
        "SELECT run_id, created_at, cohort_pooled_delta_e, ticker_count, failed_tickers "
        "FROM sw46_runs ORDER BY created_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "cohort_pooled_delta_e": r["cohort_pooled_delta_e"],
            "ticker_count": r["ticker_count"],
            "failed_tickers": json.loads(r["failed_tickers"] or "[]"),
        }
        for r in rows
    ]


def _row_to_dict(row) -> dict:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "cohort_pooled_delta_e": row["cohort_pooled_delta_e"],
        "ticker_count": row["ticker_count"],
        "failed_tickers": json.loads(row["failed_tickers"] or "[]"),
        "results": json.loads(row["results"] or "[]"),
    }
