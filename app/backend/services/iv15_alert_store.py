"""
app/backend/services/iv15_alert_store.py
=========================================
Persistence for the IV15 "price reached fair value" alert monitor.

Two tables (created on first use) in the shared run archive — Postgres in
production, SQLite locally — via the dual-mode `src.data.db` layer:

  iv15_alert_state  — per (cohort, ticker) hysteresis state so we alert ONCE
                      on the downward cross below IV15 and only re-arm after
                      the price recovers above the re-arm band. Survives
                      restarts and deploys (the whole point of persisting it).

  iv15_sweep_log    — one row per sweep, used for daily idempotency (don't
                      double-fire if the service restarts near the 08:00 SGT
                      slot) and for a lightweight audit trail.

2026-11-27: ported off a private sqlite3 connection. In production that
connection followed RUN_ARCHIVE_PATH to a Windows path that does not exist in
the container, so every sweep since August failed before reading state. On
Postgres the August hysteresis rows are read back, so the revived monitor does
not re-fire every name at once.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from src.data import db

logger = logging.getLogger(__name__)


_STATE_DDL = """
CREATE TABLE IF NOT EXISTS iv15_alert_state (
    cohort        TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    state         TEXT NOT NULL,
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

_ensured_for: Optional[str] = None
_ensure_lock = threading.Lock()


def _ensure_tables() -> None:
    """Create the tables once per database target (re-checked if the target changes)."""
    global _ensured_for
    target = "pg" if db.is_postgres() else db.get_db_path()
    if _ensured_for == target:
        return
    with _ensure_lock:
        if _ensured_for == target:
            return
        db.ensure_table(_STATE_DDL)
        db.ensure_table(_SWEEP_DDL)
        for idx in _INDEXES:
            db.execute(idx)
        _ensured_for = target


# ─── Hysteresis state ──────────────────────────────────────────────────────


def get_all_states(cohort: str) -> dict[str, dict]:
    """Return {ticker: state_dict} for one cohort."""
    _ensure_tables()
    rows = db.query(
        "SELECT ticker, state, last_p_iv15, last_price, last_iv15, "
        "last_alert_at, updated_at FROM iv15_alert_state WHERE cohort = ?",
        [cohort],
    )
    out: dict[str, dict] = {}
    for r in rows:
        out[r["ticker"]] = {
            "ticker": r["ticker"],
            "state": r["state"],
            "last_p_iv15": r["last_p_iv15"],
            "last_price": r["last_price"],
            "last_iv15": r["last_iv15"],
            "last_alert_at": r["last_alert_at"],
            "updated_at": r["updated_at"],
        }
    return out


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
    sym = ticker.upper()
    prev = db.query_one(
        "SELECT last_alert_at FROM iv15_alert_state WHERE cohort = ? AND ticker = ?",
        [cohort, sym],
    )
    last_alert_at = now if alerted else (prev["last_alert_at"] if prev else None)
    db.execute(
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
        [cohort, sym, state, p_iv15, price, iv15, last_alert_at, now],
    )


# ─── Sweep log + idempotency ───────────────────────────────────────────────


def record_sweep(*, checked: int, fired: int, rearmed: int, skipped: int,
                  detail: Optional[dict] = None) -> None:
    _ensure_tables()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO iv15_sweep_log (run_at, checked, fired, rearmed, skipped, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [now, checked, fired, rearmed, skipped, json.dumps(detail or {})],
    )


def last_sweep_at() -> Optional[str]:
    """ISO timestamp of the most recent sweep, or None."""
    _ensure_tables()
    row = db.query_one("SELECT run_at FROM iv15_sweep_log ORDER BY run_at DESC LIMIT 1")
    return row["run_at"] if row else None


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
