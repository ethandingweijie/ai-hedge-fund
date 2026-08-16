"""
app/backend/services/qualitative_storage.py
=============================================
Per-indicator cache for the Complacency qualitative scorer (LLM calls).

Lookup is by (ticker, indicator). If the latest row is younger than
max_age_days the cached score is reused — we avoid paying for the LLM
call. Default 7-day TTL.

One row per (ticker, indicator, scored_at). Older rows kept for drift
analysis (we may quarterly compare a re-score vs the prior month).

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The qual cache is written by the worker (refresh
runs queued since Phase 2e) and read by the web replicas when the drawer
opens, so a per-process SQLite file meant every replica re-paid for the
same LLM scores. complacency_qualitative was already copied to PG by the
2026-08 migration.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from src.data import db as _db

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS complacency_qualitative (
    ticker        TEXT NOT NULL,
    indicator     TEXT NOT NULL,
    scored_at     TEXT NOT NULL,
    score         INTEGER,
    confidence    REAL,
    summary       TEXT,
    evidence_json TEXT,
    model_used    TEXT,
    cost_usd      REAL DEFAULT 0,
    PRIMARY KEY (ticker, indicator, scored_at)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_qualitative_lookup "
    "ON complacency_qualitative(ticker, indicator, scored_at DESC)",
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
        _db.execute_script(";".join([_DDL] + _INDEXES))
        _tables_ready_key = key
    except Exception as exc:
        # A concurrent CREATE TABLE IF NOT EXISTS race at boot is harmless;
        # anything persistent surfaces loudly on the first real query.
        logger.warning("qualitative_storage _ensure_table: %s", exc)


def _sanitize(v: Any) -> Any:
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _sanitize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize(x) for x in v]
    return v


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SQL = """
INSERT INTO complacency_qualitative
    (ticker, indicator, scored_at, score, confidence, summary,
     evidence_json, model_used, cost_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker, indicator, scored_at) DO UPDATE SET
    score = excluded.score,
    confidence = excluded.confidence,
    summary = excluded.summary,
    evidence_json = excluded.evidence_json,
    model_used = excluded.model_used,
    cost_usd = excluded.cost_usd
"""


def save_qualitative_score(
    ticker: str,
    indicator: str,
    score: int,
    confidence: float,
    summary: str,
    evidence: list[dict],
    model_used: str,
    cost_usd: float = 0.0,
    scored_at: Optional[str] = None,
) -> None:
    _ensure_table()
    ts = scored_at or datetime.now(timezone.utc).isoformat()
    _db.execute(
        _SAVE_SQL,
        [
            ticker.upper(), indicator, ts,
            int(score), float(confidence), summary,
            json.dumps(_sanitize(evidence)),
            model_used, float(cost_usd),
        ],
    )


def get_latest_qualitative_score(
    ticker: str,
    indicator: str,
    max_age_days: int = 7,
) -> Optional[dict]:
    """Returns the latest non-stale score, or None if missing/stale."""
    _ensure_table()
    row = _db.query_one(
        "SELECT scored_at, score, confidence, summary, evidence_json, model_used, cost_usd "
        "FROM complacency_qualitative "
        "WHERE ticker = ? AND indicator = ? "
        "ORDER BY scored_at DESC "
        "LIMIT 1",
        [ticker.upper(), indicator],
    )
    if not row:
        return None

    try:
        dt = datetime.fromisoformat(row["scored_at"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        age_days = 999

    if age_days > max_age_days:
        return None

    return {
        "ticker": ticker.upper(),
        "indicator": indicator,
        "scored_at": row["scored_at"],
        "score": row["score"],
        "confidence": row["confidence"],
        "summary": row["summary"],
        "evidence": json.loads(row["evidence_json"] or "[]"),
        "model_used": row["model_used"],
        "cost_usd": row["cost_usd"],
        "age_days": age_days,
    }


def list_all_for_ticker(ticker: str, max_age_days: int = 7) -> list[dict]:
    """All cached indicators for a ticker that are still fresh."""
    _ensure_table()
    rows = _db.query(
        "SELECT indicator, MAX(scored_at) AS latest "
        "FROM complacency_qualitative "
        "WHERE ticker = ? "
        "GROUP BY indicator",
        [ticker.upper()],
    )

    out: list[dict] = []
    for r in rows:
        cached = get_latest_qualitative_score(ticker, r["indicator"], max_age_days)
        if cached:
            out.append(cached)
    return out
