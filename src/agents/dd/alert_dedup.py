"""SQLite-backed alert deduplication for the DD agent (Phase A vertical slice).

Implements the cooldown spec from the user's design (plan file
mighty-gliding-graham.md, sections 1-2):

  - Directional lock: track (ticker, last_direction, trigger_price,
    last_triggered_at) per the user's pseudocode
  - Neutral-zone flip: a flip from DROP→PUMP (or vice versa) is recognized
    ONLY when current_pct crosses the opposite extreme threshold. A small
    bounce within the neutral zone (e.g. -11% record + current +5%) does
    NOT count as a flip — it would still be in_cooldown
  - High-water mark: a same-direction continuation alert fires when the
    move EXTENDS by >=15% from the LAST TRIGGER PRICE. This prevents
    redundant DDs on slow grinds while catching catastrophic worsening
  - 24h cooldown: same direction within 24h, no HWM extension → BLOCK

Public API:
  check_alert_eligibility(ticker, current_pct, current_price) → (bool, reason)
  mark_alerted(...) — record an alert
  get_latest_alert(ticker) → AlertRecord | None
  get_cooldown_remaining(ticker) → timedelta | None  (diagnostic)

Reuses the production sqlite path + connection settings from
app.backend.services.analysis_service (per plan section "Existing files to
reuse"). The dd_alerts table sits in the same DB file as web_runs.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generator

from app.backend.services.analysis_service import _connect


# ── Tunable constants ───────────────────────────────────────────────────────
# These are the "knobs" called out in the plan. Phase 1 keeps them as module
# constants; future work can hoist to env vars or a config table.

EXTREME_PCT_THRESHOLD     = 0.10    # ±10% trigger threshold (Tier 2 default).
                                    # Used inside neutral-zone flip predicate.
HIGH_WATER_MARK_THRESHOLD = 0.15    # additional 15% from last trigger price = re-alert
COOLDOWN_HOURS            = 24


# ── Schema ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS dd_alerts (
    ticker            TEXT NOT NULL,
    last_direction    TEXT NOT NULL,        -- 'DROP' | 'PUMP'
    trigger_price     REAL NOT NULL,        -- price at moment of last alert (anchors HWM)
    trigger_pct       REAL NOT NULL,        -- pct change at trigger
    last_triggered_at TEXT NOT NULL,        -- ISO datetime UTC (microsecond precision)
    tier              TEXT NOT NULL,        -- e.g. 'tier1_held' | 'tier2_active' | 'news_trigger' | 'admin_trigger'
    alert_reason      TEXT NOT NULL,        -- 'first_breach' | 'direction_flip_*' | 'high_water_mark*' | 'cooldown_expired'
    cluster_id        TEXT,                 -- non-null when alert is part of a sector cluster
    quote_json        TEXT,                 -- full quote snapshot at trigger (audit)
    dd_run_id         TEXT,                 -- link to dd_reports.run_id of the DD report
    sent_status       TEXT DEFAULT 'pending',  -- 'pending' | 'sent' | 'failed' | 'cluster_member'
    PRIMARY KEY (ticker, last_direction, last_triggered_at)
);
CREATE INDEX IF NOT EXISTS dd_alerts_ticker_time ON dd_alerts(ticker, last_triggered_at DESC);
CREATE INDEX IF NOT EXISTS dd_alerts_status_idx  ON dd_alerts(sent_status);
CREATE INDEX IF NOT EXISTS dd_alerts_recent_idx  ON dd_alerts(last_triggered_at DESC);

-- Phase 2B refactor: DD reports live here, NOT in web_runs.
-- Rationale: the web_runs table is for ticker research that the user
-- explicitly initiated and wants permanent history of (History tab).
-- DD reports are auto-fired ephemeral alerts (Auto Due-D tab, daily refresh,
-- 7-day retention). Storing them in the same table caused History pollution.
CREATE TABLE IF NOT EXISTS dd_reports (
    run_id           TEXT PRIMARY KEY,
    run_at           TEXT NOT NULL,         -- ISO datetime UTC
    ticker           TEXT NOT NULL,
    model_name       TEXT NOT NULL,         -- 'dd_agent_qwen' | 'dd_synthetic_trigger' | etc.
    full_result_json TEXT NOT NULL          -- {report:{...}, trigger:{...}, data:{...}}
);
CREATE INDEX IF NOT EXISTS dd_reports_ticker_time ON dd_reports(ticker, run_at DESC);
CREATE INDEX IF NOT EXISTS dd_reports_recent_idx  ON dd_reports(run_at DESC);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Idempotent. Cheap enough to call on every operation."""
    conn.executescript(_DDL)


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    """Open with the same WAL/sync/busy-timeout settings as web_runs.
    Reuses _connect from analysis_service; honors RUN_ARCHIVE_PATH env var."""
    c = _connect()
    _ensure_table(c)
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class AlertRecord:
    ticker:            str
    last_direction:    str        # 'DROP' | 'PUMP'
    trigger_price:     float
    trigger_pct:       float
    last_triggered_at: datetime   # tz-aware UTC
    tier:              str
    alert_reason:      str
    cluster_id:        str | None = None
    quote_json:        str | None = None
    dd_run_id:         str | None = None
    sent_status:       str = "pending"


def _row_to_record(row: tuple) -> AlertRecord:
    """Map sqlite row → AlertRecord. Column order matches the SELECT below."""
    raw_ts = row[4]
    # SQLite ISO timestamps may or may not have tz suffix; normalise to UTC.
    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if "Z" in raw_ts \
         else datetime.fromisoformat(raw_ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return AlertRecord(
        ticker            = row[0],
        last_direction    = row[1],
        trigger_price     = float(row[2]),
        trigger_pct       = float(row[3]),
        last_triggered_at = ts,
        tier              = row[5],
        alert_reason      = row[6],
        cluster_id        = row[7],
        quote_json        = row[8],
        dd_run_id         = row[9],
        sent_status       = row[10] or "pending",
    )


def get_latest_alert(ticker: str) -> AlertRecord | None:
    """Most-recent alert across ALL directions for the ticker.
    Used by check_alert_eligibility — cooldown is cross-direction (one
    record decides whether the next event flips/continues/expires)."""
    with _conn() as c:
        row = c.execute(
            "SELECT ticker, last_direction, trigger_price, trigger_pct, "
            "       last_triggered_at, tier, alert_reason, cluster_id, "
            "       quote_json, dd_run_id, sent_status "
            "FROM dd_alerts WHERE ticker = ? "
            "ORDER BY last_triggered_at DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    return _row_to_record(row) if row else None


# ── Cooldown decision ───────────────────────────────────────────────────────

def check_alert_eligibility(
    ticker: str,
    current_pct: float,
    current_price: float,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether to fire a DD alert for this ticker right now.

    Returns (should_alert, reason).

    Logic ladder (first match wins):
      1. No prior record           → ALERT  (first_breach)
      2. Direction flip            → ALERT  ONLY when current_pct crosses
                                     opposite extreme threshold (≥±10%);
                                     bounces within neutral zone do NOT count.
      3. Cooldown expired (>24h)   → ALERT  (cooldown_expired)
      4. Same dir, additional ≥15% from trigger price → ALERT (high_water_mark)
      5. Otherwise                 → BLOCK  (in_cooldown)

    Note on entry: this function is normally called only AFTER an upstream
    trigger gate (±EXTREME_PCT_THRESHOLD) confirms a current event. The
    explicit threshold-crossing test in step 2 makes the neutral-zone rule
    unambiguous and future-proofs the logic if the gate is ever lowered.

    `now` is for testing — defaults to UTC now."""
    now = now or datetime.now(timezone.utc)
    current_direction = "DROP" if current_pct < 0 else "PUMP"
    record = get_latest_alert(ticker)

    # 1. First breach
    if record is None:
        return True, "first_breach"

    # 2. Direction flip — refined: requires crossing opposite extreme
    flip_confirmed = (
        (record.last_direction == "DROP" and current_pct >=  EXTREME_PCT_THRESHOLD)
        or
        (record.last_direction == "PUMP" and current_pct <= -EXTREME_PCT_THRESHOLD)
    )
    if flip_confirmed:
        return True, f"direction_flip_{record.last_direction}_to_{current_direction}"

    # 3. Cooldown expired
    elapsed = now - record.last_triggered_at
    if elapsed >= timedelta(hours=COOLDOWN_HOURS):
        return True, "cooldown_expired"

    # 4. Same direction, high-water mark — measured FROM TRIGGER PRICE
    if current_direction == record.last_direction:
        if current_direction == "DROP":
            # Drop deeper from trigger = positive `additional`
            additional = (record.trigger_price - current_price) / record.trigger_price
        else:
            # Pump higher from trigger = positive `additional`
            additional = (current_price - record.trigger_price) / record.trigger_price
        if additional >= HIGH_WATER_MARK_THRESHOLD:
            return True, f"high_water_mark(+{additional*100:.1f}% from trigger)"

    # 5. Within cooldown, no flip, no HWM
    elapsed_h = elapsed.total_seconds() / 3600
    return False, f"in_cooldown ({elapsed_h:.1f}h elapsed of {COOLDOWN_HOURS}h)"


def get_cooldown_remaining(
    ticker: str,
    now: datetime | None = None,
) -> timedelta | None:
    """Returns time until the 24h cooldown expires for the most recent
    alert, or None if there's no alert / cooldown already expired.

    Used for diagnostic logging — '[skip] PEGA in cooldown for 4h 32m more.'"""
    now = now or datetime.now(timezone.utc)
    record = get_latest_alert(ticker)
    if record is None:
        return None
    expires_at = record.last_triggered_at + timedelta(hours=COOLDOWN_HOURS)
    remaining  = expires_at - now
    return remaining if remaining.total_seconds() > 0 else None


# ── Recording an alert ──────────────────────────────────────────────────────

def mark_alerted(
    *,
    ticker:      str,
    direction:   str,                 # 'DROP' | 'PUMP'
    pct:         float,
    price:       float,
    tier:        str,
    reason:      str,
    quote:       dict | None = None,
    cluster_id:  str | None = None,
    dd_run_id:   str | None = None,
    sent_status: str = "pending",
    now:         datetime | None = None,
) -> None:
    """Insert a new alert row.

    Composite PK (ticker, direction, triggered_at) lets a ticker have
    multiple records across directions and time. Microsecond-precision
    timestamps make collisions essentially impossible at production
    cadences.

    `now` is for testing — defaults to UTC now."""
    if direction not in ("DROP", "PUMP"):
        raise ValueError(f"direction must be DROP or PUMP, got {direction!r}")
    now = now or datetime.now(timezone.utc)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO dd_alerts "
            "(ticker, last_direction, trigger_price, trigger_pct, "
            " last_triggered_at, tier, alert_reason, cluster_id, "
            " quote_json, dd_run_id, sent_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker.upper(),
                direction,
                float(price),
                float(pct),
                now.isoformat(),
                tier,
                reason,
                cluster_id,
                json.dumps(quote) if quote is not None else None,
                dd_run_id,
                sent_status,
            ),
        )


# ── DD reports (paired storage for the LLM agent's report payload) ─────────


def upsert_dd_report(
    *,
    run_id:           str,
    ticker:           str,
    model_name:       str,
    full_result_json: str,
    run_at:           datetime | None = None,
) -> None:
    """Write/replace a row in the dd_reports table.

    This is the dedicated storage for DD agent reports — separate from
    web_runs (which holds permanent ticker research). The dashboard JOINs
    dd_alerts → dd_reports via run_id to hydrate report content.

    Args:
      run_id:            UUID of the DD run.
      ticker:            Ticker symbol.
      model_name:        e.g. 'dd_agent_qwen', 'dd_synthetic_trigger',
                         'dd_agent_pending'.
      full_result_json:  JSON-serialized {report, trigger, data} blob.
      run_at:            UTC datetime of write. Defaults to now.
    """
    ts = (run_at or datetime.now(timezone.utc)).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dd_reports "
            "(run_id, run_at, ticker, model_name, full_result_json) "
            "VALUES (?,?,?,?,?)",
            (run_id, ts, ticker.upper(), model_name, full_result_json),
        )


# ── Retention cleanup ──────────────────────────────────────────────────────


def cleanup_old_alerts(retention_days: int = 7) -> dict:
    """Delete dd_alerts + dd_reports rows older than `retention_days`.

    Per Phase 2B UX decision: the Auto Due-D dashboard shows only TODAY'S
    alerts (vs. the History tab which is permanent). The dd_alerts +
    dd_reports tables are retained for `retention_days` so a brief audit
    window exists, but old rows are pruned to keep tables small + queries
    fast.

    Pass `retention_days=0` to wipe ALL existing rows (one-shot reset).

    Args:
      retention_days: How many days of dd_alerts to keep. Default 7.

    Returns:
      {"alerts_deleted": int, "reports_deleted": int,
       "cutoff_iso": str, "retention_days": int}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM dd_reports WHERE run_at < ?",
            (cutoff,),
        )
        reports_deleted = cur.rowcount
        cur = conn.execute(
            "DELETE FROM dd_alerts WHERE last_triggered_at < ?",
            (cutoff,),
        )
        alerts_deleted = cur.rowcount
        conn.commit()
    return {
        "alerts_deleted":   alerts_deleted,
        "reports_deleted":  reports_deleted,
        "cutoff_iso":       cutoff,
        "retention_days":   retention_days,
    }


def purge_legacy_dd_rows_from_web_runs() -> dict:
    """One-shot housekeeping: delete DD-prefixed rows that were written to
    web_runs by the pre-Phase-2B architecture.

    Also clears any rows with model_name='synthetic-dd-trigger' (the original
    name before Phase 2A renamed it to 'dd_synthetic_trigger') and the legacy
    'dd_agent_pending'/'dd_agent_qwen*'/etc. prefixes.

    Idempotent. Safe to run repeatedly. Returns the count of rows purged.
    Web ticker-research rows (any model_name not starting with 'dd_' nor
    equal to 'synthetic-dd-trigger') are NEVER touched.
    """
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM web_runs "
            "WHERE model_name LIKE 'dd_%' OR model_name = 'synthetic-dd-trigger'"
        )
        purged = cur.rowcount
        conn.commit()
    return {"web_runs_purged": purged}


# ── Test helper (do not use in production code) ─────────────────────────────

def _clear_all_alerts_for_test() -> None:
    """Truncate the dd_alerts table. Use ONLY in test fixtures."""
    with _conn() as c:
        c.execute("DELETE FROM dd_alerts")
