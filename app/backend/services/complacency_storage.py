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
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, universe, ticker_count, gate_passers, "
            "failed_tickers, results FROM complacency_runs "
            "WHERE ticker_count > 0 "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            # No non-empty cohort — fall back to absolute latest (may be empty)
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
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, results FROM complacency_runs "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return False
        run_id = row[0]
        try:
            results = json.loads(row[1] or "[]")
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

        conn.execute(
            "UPDATE complacency_runs SET results = ? WHERE run_id = ?",
            (json.dumps(results), run_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


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
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, results FROM complacency_runs "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return False
        run_id = row[0]
        try:
            results = json.loads(row[1] or "[]")
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

            conn.execute(
                "UPDATE complacency_runs SET results = ? WHERE run_id = ?",
                (json.dumps(results), run_id),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


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
