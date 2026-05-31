"""
app/backend/services/iv15_alert_store.py
=========================================
SQLite persistence for the IV15 "price reached fair value" alert monitor.

Two tables (both auto-migrating, created on first read/write), living in the
shared run_archive.db so admins have one place to back up state:

  iv15_alert_state  — per (cohort, ticker) hysteresis state so we alert ONCE
                      on the downward cross below IV15 and only re-arm after
                      the price recovers above the re-arm band. Survives
                      restarts (the whole point of persisting it).

  iv15_sweep_log    — one row per sweep, used for daily idempotency (don't
                      double-fire if the service restarts near the 08:00 SGT
                      slot) and for a lightweight audit trail.

Mirrors the connection / path / auto-migrate pattern of sw46_storage.py and
hk50_storage.py.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── DB path (shared with the other research-idea stores) ──────────────────


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


_STATE_DDL = """
CREATE TABLE IF NOT EXISTS iv15_alert_state (
    cohort        TEXT NOT NULL,        -- 'sw46' | 'hk50'
    ticker        TEXT NOT NULL,        -- the quote symbol used (upper-cased)
    state         TEXT NOT NULL,        -- 'armed' | 'triggered'
    last_p_iv15   REAL,
    last_price    REAL,
    last_iv15     REAL,
    last_alert_at TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (cohort, ticker)
)
"""

_SWEEP_DDL = """
CREATE TABLE IF NOT EXISTS iv15_sweep_log (
    run_at   TEXT NOT NULL,
    checked  INTEGER,
    fired    INTEGER,
    rearmed  INTEGER,
    skipped  INTEGER,
    detail   TEXT
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_iv15_sweep_run_at ON iv15_sweep_log(run_at DESC)",
]


def _ensure_tables() -> None:
    import os

    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(_STATE_DDL)
        conn.execute(_SWEEP_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


# ─── Hysteresis state ──────────────────────────────────────────────────────


def get_all_states(cohort: str) -> dict[str, dict]:
    """Return {ticker: state_dict} for one cohort."""
    _ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ticker, state, last_p_iv15, last_price, last_iv15, "
            "last_alert_at, updated_at FROM iv15_alert_state WHERE cohort = ?",
            (cohort,),
        ).fetchall()
    finally:
        conn.close()
    return {
        r[0]: {
            "ticker": r[0],
            "state": r[1],
            "last_p_iv15": r[2],
            "last_price": r[3],
            "last_iv15": r[4],
            "last_alert_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    }


def upsert_state(
    *,
    cohort: str,
    ticker: str,
    state: str,
    p_iv15: Optional[float],
    price: Optional[float],
    iv15: Optional[float],
    alerted: bool = False,
) -> None:
    """Insert or update the hysteresis state for one (cohort, ticker).

    When `alerted` is True the last_alert_at timestamp is bumped; otherwise the
    previous last_alert_at is preserved.
    """
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect()
    try:
        prev = conn.execute(
            "SELECT last_alert_at FROM iv15_alert_state WHERE cohort = ? AND ticker = ?",
            (cohort, ticker.upper()),
        ).fetchone()
        last_alert_at = now if alerted else (prev[0] if prev else None)
        conn.execute(
            """
            INSERT INTO iv15_alert_state
                (cohort, ticker, state, last_p_iv15, last_price, last_iv15,
                 last_alert_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cohort, ticker) DO UPDATE SET
                state = excluded.state,
                last_p_iv15 = excluded.last_p_iv15,
                last_price = excluded.last_price,
                last_iv15 = excluded.last_iv15,
                last_alert_at = excluded.last_alert_at,
                updated_at = excluded.updated_at
            """,
            (cohort, ticker.upper(), state, p_iv15, price, iv15, last_alert_at, now),
        )
        conn.commit()
    finally:
        conn.close()


# ─── Sweep log + idempotency ───────────────────────────────────────────────


def record_sweep(*, checked: int, fired: int, rearmed: int, skipped: int,
                  detail: Optional[dict] = None) -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO iv15_sweep_log (run_at, checked, fired, rearmed, skipped, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, checked, fired, rearmed, skipped, json.dumps(detail or {})),
        )
        conn.commit()
    finally:
        conn.close()


def last_sweep_at() -> Optional[str]:
    """ISO timestamp of the most recent sweep, or None."""
    _ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT run_at FROM iv15_sweep_log ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def swept_within_hours(hours: float) -> bool:
    """True if a sweep ran within the last `hours` — used for daily idempotency."""
    last = last_sweep_at()
    if not last:
        return False
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return age_h < hours
    except Exception:
        return False
