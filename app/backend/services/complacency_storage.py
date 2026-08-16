"""
app/backend/services/complacency_storage.py
============================================
Persistence for Complacency screener cohorts.

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The old raw-sqlite3 access gave every Railway
process its own private file; since the complacency refresh runs on the
worker in queue mode, the web replicas kept serving stale cohorts.
complacency_runs was already copied to PG by the 2026-08 migration.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from src.data import db as _db
from src.research_ideas.complacency.schemas import ComplacencyCohortResult


logger = logging.getLogger(__name__)


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

_COLS = (
    "run_id, created_at, universe, ticker_count, gate_passers, "
    "failed_tickers, results"
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
        logger.warning("complacency_storage _ensure_table: %s", exc)


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
INSERT INTO complacency_runs
    (run_id, created_at, universe, ticker_count, gate_passers,
     failed_tickers, results)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(run_id) DO UPDATE SET
    created_at = excluded.created_at,
    universe = excluded.universe,
    ticker_count = excluded.ticker_count,
    gate_passers = excluded.gate_passers,
    failed_tickers = excluded.failed_tickers,
    results = excluded.results
"""


def save_complacency_run(cohort: ComplacencyCohortResult) -> None:
    _ensure_table()
    payload = _sanitize(cohort.model_dump())
    results_json = json.dumps(payload.get("results", []))
    failed_json = json.dumps(payload.get("failed_tickers", []))
    _db.execute(
        _SAVE_SQL,
        [
            cohort.run_id,
            cohort.created_at,
            cohort.universe,
            cohort.ticker_count,
            cohort.gate_passers,
            failed_json,
            results_json,
        ],
    )


def get_latest_complacency_run() -> Optional[dict]:
    """
    Latest cohort run with at least one ticker. Skips EMPTY cohorts —
    a refresh that produced zero tickers (FMP outage / rate-limit
    cascade) should never hide the previous good run. Combined with the
    empty-cohort save guard in the refresh worker, this makes a failed
    refresh a non-event: the prior populated cohort stays visible.

    Falls back to the absolute-latest (even if empty) only when NO
    non-empty cohort exists at all (fresh install).
    """
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM complacency_runs "
        "WHERE ticker_count > 0 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        # No non-empty cohort — fall back to absolute latest (may be empty)
        row = _db.query_one(
            f"SELECT {_COLS} FROM complacency_runs "
            "ORDER BY created_at DESC LIMIT 1"
        )
    if not row:
        return None
    return _row_to_dict(row)


def get_complacency_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    row = _db.query_one(
        f"SELECT {_COLS} FROM complacency_runs WHERE run_id = ?",
        [run_id],
    )
    if not row:
        return None
    return _row_to_dict(row)


def list_complacency_runs(limit: int = 20) -> list[dict]:
    _ensure_table()
    rows = _db.query(
        "SELECT run_id, created_at, universe, ticker_count, gate_passers, failed_tickers "
        "FROM complacency_runs ORDER BY created_at DESC LIMIT ?",
        [limit],
    )
    return [
        {
            "run_id": r["run_id"],
            "created_at": r["created_at"],
            "universe": r["universe"],
            "ticker_count": r["ticker_count"],
            "gate_passers": r["gate_passers"],
            "failed_tickers": json.loads(r["failed_tickers"] or "[]"),
        }
        for r in rows
    ]


def update_ticker_in_latest_cohort(updated_result: dict) -> bool:
    """
    Patch the latest cohort run's results JSON to replace a single ticker's
    row with `updated_result`. Used when the user generates qualitative
    on-the-fly for a non-gate ticker — we want the new aggregate to PERSIST
    so a page-refresh shows it.

    Returns True if a row was replaced, False if the ticker isn't in the
    latest cohort or no cohort exists. Does not create a new run row.
    """
    if not updated_result or not updated_result.get("ticker"):
        return False
    ticker = str(updated_result["ticker"]).upper()

    _ensure_table()
    row = _db.query_one(
        "SELECT run_id, results FROM complacency_runs "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        return False
    run_id = row["run_id"]
    try:
        results = json.loads(row["results"] or "[]")
    except Exception:
        return False

    replaced = False
    for i, r in enumerate(results):
        if (r.get("ticker") or "").upper() == ticker:
            # Preserve rank from existing row (the on-the-fly score won't
            # have a sensible rank — and the cohort ordering shouldn't
            # change just because qual was added).
            existing_rank = r.get("rank")
            merged = _sanitize(updated_result)
            if existing_rank is not None and merged.get("rank") is None:
                merged["rank"] = existing_rank
            results[i] = merged
            replaced = True
            break
    if not replaced:
        return False

    _db.execute(
        "UPDATE complacency_runs SET results = ? WHERE run_id = ?",
        [json.dumps(results), run_id],
    )
    return True


def update_ticker_in_latest_cohort_partial_qual(
    ticker: str,
    indicator_code: str,
    indicator_score: dict,
) -> bool:
    """
    Two-phase architecture per-indicator patcher.

    Patches ONE indicator into the latest cohort row's qualitative
    dict, then recomputes:
      • qual_assessment.composite (sum of all indicator scores so far)
      • qual_assessment.max_possible (5 × number scored)
      • qual_assessment.composite_normalized
      • aggregate_score (quant + qual / max * 50)
      • aggregate_qual_pts

    Called by assess_qualitative's on_indicator_done callback each
    time ONE indicator scores. Result: if a force-rescore 429-cascades
    at 7/10, those 7 are already persisted in the cohort row with a
    proportional aggregate. The drawer shows them live.

    Returns True if patched, False if ticker not in cohort.
    """
    ticker_u = ticker.upper()
    _ensure_table()
    row = _db.query_one(
        "SELECT run_id, results FROM complacency_runs "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not row:
        return False
    run_id = row["run_id"]
    try:
        results = json.loads(row["results"] or "[]")
    except Exception:
        return False

    for i, r in enumerate(results):
        if (r.get("ticker") or "").upper() != ticker_u:
            continue
        # Ensure qualitative dict exists
        qual = r.get("qualitative") or {
            "indicators": {},
            "composite": 0,
            "max_possible": 0,
            "composite_normalized": 0.0,
            "conviction_label": "PASS",
            "assessed_at": None,
            "cost_usd": 0.0,
            "incomplete": True,  # incomplete while streaming
        }
        indicators = qual.get("indicators") or {}
        indicators[indicator_code] = _sanitize(indicator_score)
        qual["indicators"] = indicators

        # Recompute composite + aggregate
        composite = sum(int(v.get("score") or 0) for v in indicators.values())
        max_possible = 5 * len(indicators)
        qual["composite"] = composite
        qual["max_possible"] = max_possible
        qual["composite_normalized"] = (
            composite / max_possible if max_possible else 0.0
        )

        # Recompute aggregate based on current quant + partial qual
        from datetime import datetime, timezone
        quant_composite = float(r.get("composite") or 0.0)
        quant_pts = (max(0.0, min(quant_composite, 8.0)) / 8.0) * 50.0
        qual_pts = 0.0
        if max_possible > 0:
            qual_pts = (composite / max_possible) * 50.0

        r["qualitative"] = qual
        r["aggregate_score"] = round(quant_pts + qual_pts, 1)
        r["aggregate_quant_pts"] = round(quant_pts, 1)
        r["aggregate_qual_pts"] = round(qual_pts, 1)
        qual["assessed_at"] = datetime.now(timezone.utc).isoformat()

        results[i] = r

        _db.execute(
            "UPDATE complacency_runs SET results = ? WHERE run_id = ?",
            [json.dumps(results), run_id],
        )
        return True
    return False


def _row_to_dict(row) -> dict:
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "universe": row["universe"],
        "ticker_count": row["ticker_count"],
        "gate_passers": row["gate_passers"],
        "failed_tickers": json.loads(row["failed_tickers"] or "[]"),
        "results": json.loads(row["results"] or "[]"),
    }
