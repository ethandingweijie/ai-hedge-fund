"""
app/backend/services/sector_medians_storage.py
================================================
Cache for live sector medians (EV/Sales, P/E, FCF Yield, ...).

The Complacency scorer reads from here in preference to the static Nov-2025
defaults in `scoring.SECTOR_EV_SALES_MEDIAN`. Refreshed weekly via the
sector_medians.refresh_sector_medians() fetcher.

One row per (refreshed_at, sector, metric). Lookup is by sector + metric,
sorted refreshed_at DESC, max_age guarded.

Storage (S1 batch, 2026-08-16): dual-mode via src.data.db — SQLite locally,
Postgres in production. The refresh runs in the scheduler/worker while the
scorer reads happen on the web replicas, so a per-process SQLite file meant
the web tier never saw fresh medians. sector_medians was already copied to
PG by the 2026-08 migration.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from src.data import db as _db

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS sector_medians (
    refreshed_at  TEXT NOT NULL,
    sector        TEXT NOT NULL,
    metric        TEXT NOT NULL,
    median_value  REAL NOT NULL,
    p25_value     REAL,
    p75_value     REAL,
    sample_size   INTEGER NOT NULL,
    universe      TEXT,
    PRIMARY KEY (refreshed_at, sector, metric)
)
"""
# refreshed_at = ISO timestamp; sector = GICS sector name;
# metric = 'ev_sales' | 'pe' | 'fcf_yield'; universe e.g. 'sp500'.

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sector_medians_lookup "
    "ON sector_medians(sector, metric, refreshed_at DESC)",
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
        logger.warning("sector_medians_storage _ensure_table: %s", exc)


def _sanitize(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return float(v)


# ON CONFLICT form works on BOTH SQLite and Postgres (no INSERT OR REPLACE).
_SAVE_SQL = """
INSERT INTO sector_medians
    (refreshed_at, sector, metric, median_value,
     p25_value, p75_value, sample_size, universe)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(refreshed_at, sector, metric) DO UPDATE SET
    median_value = excluded.median_value,
    p25_value = excluded.p25_value,
    p75_value = excluded.p75_value,
    sample_size = excluded.sample_size,
    universe = excluded.universe
"""


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
    params = [
        [
            refreshed_at,
            r["sector"],
            r["metric"],
            _sanitize(r["median"]),
            _sanitize(r.get("p25")),
            _sanitize(r.get("p75")),
            int(r["sample_size"]),
            universe,
        ]
        for r in rows
    ]
    _db.executemany(_SAVE_SQL, params)
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
    row = _db.query_one(
        "SELECT refreshed_at, median_value, p25_value, p75_value, sample_size "
        "FROM sector_medians "
        "WHERE sector = ? AND metric = ? "
        "ORDER BY refreshed_at DESC "
        "LIMIT 1",
        [sector, metric],
    )
    if not row:
        return None

    from datetime import datetime, timezone
    try:
        refreshed_dt = datetime.fromisoformat(row["refreshed_at"].replace("Z", "+00:00"))
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
        "median": row["median_value"],
        "p25": row["p25_value"],
        "p75": row["p75_value"],
        "sample_size": row["sample_size"],
        "refreshed_at": row["refreshed_at"],
        "age_days": age_days,
    }


def get_latest_refresh_timestamp() -> Optional[str]:
    """ISO timestamp of the most recent sector-medians refresh, or None."""
    _ensure_table()
    row = _db.query_one(
        "SELECT MAX(refreshed_at) AS latest FROM sector_medians"
    )
    if not row:
        return None
    return row["latest"] or None


def list_latest_sector_medians(metric: str = "ev_sales") -> list[dict]:
    """All sectors' latest medians for a given metric."""
    _ensure_table()
    rows = _db.query(
        "SELECT sector, median_value, p25_value, p75_value, sample_size, refreshed_at "
        "FROM sector_medians sm1 "
        "WHERE metric = ? "
        "  AND refreshed_at = ( "
        "    SELECT MAX(refreshed_at) FROM sector_medians sm2 "
        "    WHERE sm2.sector = sm1.sector AND sm2.metric = sm1.metric "
        "  ) "
        "ORDER BY sector",
        [metric],
    )
    return [
        {
            "sector": r["sector"],
            "median": r["median_value"],
            "p25": r["p25_value"],
            "p75": r["p75_value"],
            "sample_size": r["sample_size"],
            "refreshed_at": r["refreshed_at"],
        }
        for r in rows
    ]
