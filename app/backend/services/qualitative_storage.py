"""
app/backend/services/qualitative_storage.py
=============================================
Per-indicator cache for the Complacency qualitative scorer (LLM calls).

Lookup is by (ticker, indicator). If the latest row is younger than
max_age_days the cached score is reused — we avoid paying for the LLM
call. Default 7-day TTL.

One row per (ticker, indicator, scored_at). Older rows kept for drift
analysis (we may quarterly compare a re-score vs the prior month).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


def _sanitize(v: Any) -> Any:
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _sanitize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sanitize(x) for x in v]
    return v


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
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO complacency_qualitative
                (ticker, indicator, scored_at, score, confidence, summary,
                 evidence_json, model_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker.upper(), indicator, ts,
                int(score), float(confidence), summary,
                json.dumps(_sanitize(evidence)),
                model_used, float(cost_usd),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_qualitative_score(
    ticker: str,
    indicator: str,
    max_age_days: int = 7,
) -> Optional[dict]:
    """Returns the latest non-stale score, or None if missing/stale."""
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT scored_at, score, confidence, summary, evidence_json, model_used, cost_usd
            FROM complacency_qualitative
            WHERE ticker = ? AND indicator = ?
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            (ticker.upper(), indicator),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None

    try:
        dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
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
        "scored_at": row[0],
        "score": row[1],
        "confidence": row[2],
        "summary": row[3],
        "evidence": json.loads(row[4] or "[]"),
        "model_used": row[5],
        "cost_usd": row[6],
        "age_days": age_days,
    }


def list_all_for_ticker(ticker: str, max_age_days: int = 7) -> list[dict]:
    """All cached indicators for a ticker that are still fresh."""
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT indicator, MAX(scored_at) AS latest
            FROM complacency_qualitative
            WHERE ticker = ?
            GROUP BY indicator
            """,
            (ticker.upper(),),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for indicator, _latest in rows:
        cached = get_latest_qualitative_score(ticker, indicator, max_age_days)
        if cached:
            out.append(cached)
    return out
