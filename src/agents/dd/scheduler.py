"""
scheduler.py — Decide whether the cron dispatcher should run this tick.

Railway cron may fire every N minutes regardless of US market state.
should_run() is the gate that turns most ticks into no-ops:

  • Weekends         → skip   (no US trading)
  • Before 9:30 ET   → skip   (pre-market, illiquid quotes)
  • After 16:00 ET   → skip   (after-market)
  • US holidays      → skip   (NYSE closed)

Outside market hours, the FMP quote endpoint returns stale prices anyway —
running the dispatcher would just generate noise (same price = no breach
detected) while still hitting the API quota.

Pure functions. All time inputs flow through `now` for testability.

NOT in Phase 2B (kept simple):
  • Volatility-weighted dispatch (per-tier cadence — every 1min for Tier 1
    vs every 5min for Tier 2). Right now we only have Tier 1, and the
    dispatcher always runs all configured tiers.
  • Pre-market / extended-hours support. Easy to add later via env var.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Final


logger = logging.getLogger(__name__)


# Env-var overrides
ENV_FORCE_RUN:        Final[str] = "DD_FORCE_DISPATCH"        # bypass all gates (for testing)
ENV_SKIP_MARKET_GATE: Final[str] = "DD_SKIP_MARKET_HOURS"     # skip just the market-hours gate

# Market hours (ET — US/Eastern). NYSE regular session: 09:30 - 16:00.
_MARKET_OPEN_ET  = time(9, 30)
_MARKET_CLOSE_ET = time(16, 0)


# 2026 NYSE holidays (full-day closures). Hardcoded since the list is small,
# annual, and predictable. Worst case if a holiday is missing: dispatcher
# runs and quotes are stale → no breaches detected → no-op. Failure mode is
# benign.
_HOLIDAYS_2026: Final[set[str]] = {
    "2026-01-01",   # New Year's Day
    "2026-01-19",   # MLK Day
    "2026-02-16",   # Presidents Day
    "2026-04-03",   # Good Friday
    "2026-05-25",   # Memorial Day
    "2026-06-19",   # Juneteenth
    "2026-07-03",   # Independence Day (observed; July 4 falls on Saturday)
    "2026-09-07",   # Labor Day
    "2026-11-26",   # Thanksgiving
    "2026-12-25",   # Christmas
}


@dataclass(frozen=True)
class DispatchDecision:
    should_run: bool
    reason:     str


def should_run(now: datetime | None = None) -> DispatchDecision:
    """Decide whether the dispatcher should execute this tick.

    Args:
      now: optional datetime override (must be tz-aware). Defaults to UTC now.
           Tests pass a fixed moment; production passes None.

    Returns:
      DispatchDecision with `should_run` and a `reason` string for logging.
    """
    if _truthy(os.environ.get(ENV_FORCE_RUN, "")):
        return DispatchDecision(True, "force_dispatch (env override)")

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    et = _to_et(now_utc)

    # Weekend
    if et.weekday() >= 5:    # Saturday=5, Sunday=6
        return DispatchDecision(False, f"weekend ({et.strftime('%A')})")

    # Holiday
    if et.date().isoformat() in _HOLIDAYS_2026:
        return DispatchDecision(False, f"NYSE holiday ({et.date().isoformat()})")

    # Market-hours gate (env-bypassable for testing pre-market / after-hours)
    if not _truthy(os.environ.get(ENV_SKIP_MARKET_GATE, "")):
        et_time = et.time()
        if et_time < _MARKET_OPEN_ET:
            return DispatchDecision(False, f"pre-market ({et_time.strftime('%H:%M')} ET)")
        if et_time >= _MARKET_CLOSE_ET:
            return DispatchDecision(False, f"after-market ({et_time.strftime('%H:%M')} ET)")

    return DispatchDecision(True, f"market open ({et.strftime('%a %H:%M')} ET)")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _truthy(s: str) -> bool:
    return s.strip().lower() in {"true", "1", "yes", "on", "y", "t"}


def _to_et(utc_dt: datetime) -> datetime:
    """Convert tz-aware UTC datetime to US/Eastern.

    Uses zoneinfo (stdlib, Python 3.9+). Handles DST automatically.
    Falls back to a fixed UTC-5 offset if zoneinfo is unavailable (Windows
    without the tzdata package, etc.) — DST will be wrong half the year
    in that fallback, which is a known limitation we'll fix when needed.
    """
    try:
        from zoneinfo import ZoneInfo
        return utc_dt.astimezone(ZoneInfo("America/New_York"))
    except Exception as exc:
        logger.warning(
            "scheduler: zoneinfo unavailable (%s) — using fixed UTC-5; DST not applied",
            exc,
        )
        return utc_dt - timedelta(hours=5)
