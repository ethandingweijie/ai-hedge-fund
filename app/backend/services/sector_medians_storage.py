"""
app/backend/services/sector_medians_storage.py
================================================
SQLite cache for live sector medians (EV/Sales, P/E, FCF Yield, ...).

The Complacency scorer reads from here in preference to the static Nov-2025
defaults in `scoring.SECTOR_EV_SALES_MEDIAN`. Refreshed weekly via the
sector_medians.refresh_sector_medians() fetcher.

One row per (refreshed_at, sector, metric). Lookup is by sector + metric,
sorted refreshed_at DESC, max_age guarded.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from pathlib import Path
from typing import Optional

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
CREATE TABLE IF NOT EXISTS sector_medians (
    refreshed_at  TEXT NOT NULL,         -- ISO timestamp
    sector        TEXT NOT NULL,         -- GICS sector name
    metric        TEXT NOT NULL,         -- 'ev_sales' | 'pe' | 'fcf_yield'
    median_value  REAL NOT NULL,
    p25_value     REAL,
    p75_value     REAL,
    sample_size   INTEGER NOT NULL,
    universe      TEXT,                  -- e.g. 'sp500'
    PRIMARY KEY (refreshed_at, sector, metric)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sector_medians_lookup "
    "ON sector_medians(sector, metric, refreshed_at DESC)",
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


def _sanitize(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return float(v)


def save_sector_medians_batch(
    refreshed_at: str,
    rows: list[dict],
    universe: str = "sp500",
) -> int:
    """
    Persist a batch of medians from a single refresh run.

    `rows` is a list of dicts:
        { "sector": "Technology", "metric": "ev_sales",
          "median": 5.8, "p25": 3.1, "p75": 9.2, "sample_size": 68 }
    Returns number of rows written.
    """
    if not rows:
        return 0
    _ensure_table()
    conn = _connect()
    try:
        for r in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO sector_medians
                    (refreshed_at, sector, metric, median_value,
                     p25_value, p75_value, sample_size, universe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refreshed_at,
                    r["sector"],
                    r["metric"],
                    _sanitize(r["median"]),
                    _sanitize(r.get("p25")),
                    _sanitize(r.get("p75")),
                    int(r["sample_size"]),
                    universe,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def get_latest_sector_median(
    sector: str,
    metric: str = "ev_sales",
    max_age_days: int = 14,
) -> Optional[dict]:
    """
    Returns the most recent row for (sector, metric) IF it's within
    max_age_days. None if missing or stale.

      {sector, metric, median, p25, p75, sample_size, refreshed_at, age_days}
    """
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT refreshed_at, median_value, p25_value, p75_value, sample_size
            FROM sector_medians
            WHERE sector = ? AND metric = ?
            ORDER BY refreshed_at DESC
            LIMIT 1
            """,
            (sector, metric),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None

    from datetime import datetime, timezone
    try:
        refreshed_dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        if refreshed_dt.tzinfo is None:
            refreshed_dt = refreshed_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - refreshed_dt
        age_days = age.total_seconds() / 86400.0
    except Exception:
        age_days = 999

    if age_days > max_age_days:
        return None

    return {
        "sector": sector,
        "metric": metric,
        "median": row[1],
        "p25": row[2],
        "p75": row[3],
        "sample_size": row[4],
        "refreshed_at": row[0],
        "age_days": age_days,
    }


def get_latest_refresh_timestamp() -> Optional[str]:
    """ISO timestamp of the most recent sector-medians refresh, or None."""
    _ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT MAX(refreshed_at) FROM sector_medians"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def list_latest_sector_medians(metric: str = "ev_sales") -> list[dict]:
    """All sectors' latest medians for a given metric."""
    _ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT sector, median_value, p25_value, p75_value, sample_size, refreshed_at
            FROM sector_medians sm1
            WHERE metric = ?
              AND refreshed_at = (
                SELECT MAX(refreshed_at) FROM sector_medians sm2
                WHERE sm2.sector = sm1.sector AND sm2.metric = sm1.metric
              )
            ORDER BY sector
            """,
            (metric,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "sector": r[0],
            "median": r[1],
            "p25": r[2],
            "p75": r[3],
            "sample_size": r[4],
            "refreshed_at": r[5],
        }
        for r in rows
    ]
