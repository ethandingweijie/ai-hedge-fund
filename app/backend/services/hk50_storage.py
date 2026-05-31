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
from datetime import datetime, timezone
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
    results           TEXT NOT NULL,
    cohort_meta       TEXT
)
"""

_HK50_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hk50_runs_created_at ON hk50_runs(created_at DESC)",
]

# Columns added after the table's first ship — auto-migrated on every connect so
# an existing run_archive.db gains them without a manual migration step. The
# dynamic-universe summary (displayed/eligible counts, ENTER/STAY thresholds,
# promoted/relegated deltas) lives in one JSON blob: the promotion/relegation
# deltas are computed per run vs the prior run and CANNOT be re-derived from a
# single stored snapshot, so they must be persisted here.
_HK50_MIGRATIONS = [
    ("cohort_meta", "ALTER TABLE hk50_runs ADD COLUMN cohort_meta TEXT"),
]


def _ensure_table() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_HK50_DDL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(hk50_runs)")}
        for col, ddl in _HK50_MIGRATIONS:
            if col not in existing:
                conn.execute(ddl)
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
    meta_json = json.dumps(
        {
            "eligible_count": payload.get("eligible_count", 0),
            "displayed_count": payload.get("displayed_count", 0),
            "enter_threshold": payload.get("enter_threshold", 0.0),
            "stay_threshold": payload.get("stay_threshold", 0.0),
            "promoted": payload.get("promoted", []),
            "relegated": payload.get("relegated", []),
        }
    )
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hk50_runs
                (run_id, created_at, ticker_count, avg_growth, avg_dividend,
                 median_p_iv15, lead_growth_count, failed_tickers, results, cohort_meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                meta_json,
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
            "median_p_iv15, lead_growth_count, failed_tickers, results, cohort_meta "
            "FROM hk50_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_latest_membership() -> set[str]:
    """Set of hk_ticker that were in the DISPLAYED cohort (in_cohort) in the
    latest stored run. Feeds the hysteresis bands in selection.select_cohort so
    membership doesn't flicker run-to-run. Returns an empty set on cold start
    (no prior run) or any read error — the runner then evaluates every name
    against the ENTER threshold, which is the correct cold-start behavior.
    """
    try:
        _ensure_table()
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT results FROM hk50_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return set()
        results = json.loads(row[0] or "[]")
        return {
            r["hk_ticker"]
            for r in results
            if r.get("in_cohort") and r.get("hk_ticker")
        }
    except Exception:
        logger.warning("HK50: get_latest_membership failed — treating as cold start")
        return set()


def get_hk50_run(run_id: str) -> Optional[dict]:
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_id, created_at, ticker_count, avg_growth, avg_dividend, "
            "median_p_iv15, lead_growth_count, failed_tickers, results, cohort_meta "
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


def _match_row(r: dict, needle_u: str) -> bool:
    """A cohort row matches if the reported OR canonical HK ticker matches."""
    return (
        (r.get("ticker") or "").upper() == needle_u
        or (r.get("hk_ticker") or "").upper() == needle_u
    )


def update_ticker_in_latest_cohort_partial_qual(
    needle: str,
    code: str,
    indicator_score: dict,
) -> bool:
    """
    Two-phase architecture per-sub-metric patcher (mirrors the Complacency
    screener). Patches ONE qualitative sub-metric into the latest HK50 cohort
    row, then RE-AGGREGATES both dimensions + the combined conviction so a
    page-refresh mid-run shows the overlay filling in live.

    The two PURE quant screens (growth_score / dividend_score) are never
    touched — only the parallel `qualitative` overlay is recomputed. Math is
    single-sourced from hk50_qualitative (aggregate_dimension + combined_
    conviction), so there is no risk of drift between the live patch and a
    full assess_hk50_qualitative() pass.

    `needle` matches the reported (ADR/HK) ticker OR the canonical HK ticker.
    Returns True if a row was patched, False otherwise.
    """
    from src.research_ideas.hk50.hk50_qualitative import (
        aggregate_dimension, combined_conviction, load_seed, _seed_default,
        sector_of,
    )
    from src.research_ideas.hk50.schemas import QualIndicatorScore

    needle_u = (needle or "").upper()
    _ensure_table()
    conn = _connect()
    try:
        row_db = conn.execute(
            "SELECT run_id, results FROM hk50_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row_db:
            return False
        run_id = row_db[0]
        try:
            results = json.loads(row_db[1] or "[]")
        except Exception:
            return False

        for i, r in enumerate(results):
            if not _match_row(r, needle_u):
                continue
            hk_ticker = r.get("hk_ticker") or r.get("ticker") or needle

            # Rebuild the full scored set from whatever's already persisted
            # (curated baseline starts empty), then add/replace the new one.
            qual = r.get("qualitative") or {}
            scored: dict[str, QualIndicatorScore] = {}
            for dim_key in ("policy", "moat"):
                dim = qual.get(dim_key) or {}
                for c, sdict in (dim.get("indicators") or {}).items():
                    try:
                        scored[c] = QualIndicatorScore(**sdict)
                    except Exception:
                        pass
            try:
                scored[code] = QualIndicatorScore(**_sanitize(indicator_score))
            except Exception:
                return False

            seed = load_seed().get(hk_ticker) or _seed_default()
            policy_dim = aggregate_dimension("policy", scored, seed_tier=seed.get("policy_tier"))
            moat_dim = aggregate_dimension("moat", scored, seed_tier=seed.get("moat_tier"))

            quant_lead = max(
                float(r.get("growth_score") or 0.0),
                float(r.get("dividend_score") or 0.0),
            )
            conv, flags = combined_conviction(quant_lead, policy_dim.tier, moat_dim.tier)
            if seed.get("note"):
                flags = [seed["note"]] + flags

            used_seed_fallback = (policy_dim.n_scored == 0) or (moat_dim.n_scored == 0)
            r["qualitative"] = _sanitize({
                "hk_ticker": hk_ticker,
                "sector": sector_of(hk_ticker),
                "policy": policy_dim.model_dump(),
                "moat": moat_dim.model_dump(),
                "conviction": conv,
                "source": "hybrid" if used_seed_fallback else "llm",
                "flags": flags,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "cost_usd": qual.get("cost_usd", 0.0),
                "incomplete": True,  # still streaming
            })
            results[i] = r

            conn.execute(
                "UPDATE hk50_runs SET results = ? WHERE run_id = ?",
                (json.dumps(results), run_id),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def set_ticker_qualitative_in_latest_cohort(
    needle: str,
    qualitative_dict: dict,
) -> bool:
    """
    Replace the WHOLE `qualitative` overlay on the latest cohort's matching
    row with a finished assessment (source=llm/hybrid, incomplete=False). Used
    for the final stamp after all sub-metrics for a name have streamed in.

    Returns True if patched, False if the ticker isn't in the latest cohort.
    """
    needle_u = (needle or "").upper()
    _ensure_table()
    conn = _connect()
    try:
        row_db = conn.execute(
            "SELECT run_id, results FROM hk50_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row_db:
            return False
        run_id = row_db[0]
        try:
            results = json.loads(row_db[1] or "[]")
        except Exception:
            return False
        for i, r in enumerate(results):
            if not _match_row(r, needle_u):
                continue
            r["qualitative"] = _sanitize(qualitative_dict)
            results[i] = r
            conn.execute(
                "UPDATE hk50_runs SET results = ? WHERE run_id = ?",
                (json.dumps(results), run_id),
            )
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def _row_to_dict(row: tuple) -> dict:
    out = {
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
    # Dynamic-universe summary (added post-ship via cohort_meta). Pre-migration
    # rows have no meta → fall back to values derivable from `results` so the
    # frontend always has displayed/eligible counts. promoted/relegated cannot
    # be reconstructed from one snapshot, so they default to empty.
    meta = {}
    if len(row) > 9 and row[9]:
        try:
            meta = json.loads(row[9])
        except Exception:
            meta = {}
    results = out["results"]
    out["eligible_count"] = meta.get("eligible_count", len(results))
    out["displayed_count"] = meta.get(
        "displayed_count", sum(1 for r in results if r.get("in_cohort"))
    )
    out["enter_threshold"] = meta.get("enter_threshold", 0.0)
    out["stay_threshold"] = meta.get("stay_threshold", 0.0)
    out["promoted"] = meta.get("promoted", [])
    out["relegated"] = meta.get("relegated", [])
    return out
