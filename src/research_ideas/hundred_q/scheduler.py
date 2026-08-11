"""
src/research_ideas/hundred_q/scheduler.py
============================================
Three scheduled cycles for the 100-Question screener. As of Phase 4 the
FIRE TIMES live in the dedicated scheduler service
(app/backend/scheduler_service.py), which enqueues run_hundred_q_*_task on
the arq worker; the worker calls the cycle functions below. Each cycle is
independently idempotent (via storage.get_latest_run_by_type) and
independently disable-able via env var (read by the scheduler service).

1. Daily event-trigger sweep   — fires ~00:30 UTC (SGT 08:30), checks
   new-filing/insider-buy/earnings/price-shock detectors across the
   watchlist. Idempotent: skips if a sweep completed in the last ~20h.
   Startup catch-up (the web-era thread ran a missed sweep on boot) is
   preserved by the scheduler service enqueuing this task once at start;
   the 20h gate below decides whether it actually runs.
2. Weekly full quant batch     — fires Sunday 01:00 UTC, rescoring the
   entire pilot universe (cheap — reads through the Knowledge Graph
   cache). Idempotent: skips if a weekly_quant run completed in the
   last ~6 days.
3. Quarterly annual backstop   — fires on the 1st of each quarter month
   at 02:00 UTC, refreshing any stale (>365d) qualitative question for
   Active/On-Deck tickers only. Idempotent: skips if a backstop cycle
   completed in the last ~80 days.

Env knobs (read by the scheduler service, hot-re-read each iteration):
  HUNDRED_Q_SCHEDULER_DISABLED=true        — disable all three
  HUNDRED_Q_DAILY_SWEEP_DISABLED=true      — disable #1 only
  HUNDRED_Q_WEEKLY_BATCH_DISABLED=true     — disable #2 only
  HUNDRED_Q_BACKSTOP_DISABLED=true         — disable #3 only
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DAILY_FIRE_HOUR_UTC = int(os.environ.get("HUNDRED_Q_DAILY_SWEEP_HOUR_UTC", "0"))
_DAILY_FIRE_MINUTE_UTC = 30
_WEEKLY_FIRE_WEEKDAY = 6   # Sunday
_WEEKLY_FIRE_HOUR_UTC = 1
_BACKSTOP_FIRE_HOUR_UTC = 2

_DAILY_IDEMPOTENCY_HOURS = 20
_WEEKLY_IDEMPOTENCY_HOURS = 6 * 24
_BACKSTOP_IDEMPOTENCY_DAYS = 80


def _already_ran_within(run_type: str, hours: Optional[float] = None, days: Optional[float] = None) -> bool:
    from src.research_ideas.hundred_q import storage

    try:
        latest = storage.get_latest_run_by_type(run_type)
        if not latest or not latest.get("created_at"):
            return False
        created = datetime.fromisoformat(str(latest["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        threshold_hours = hours if hours is not None else (days * 24 if days is not None else 0)
        return age_hours < threshold_hours
    except Exception as exc:
        logger.warning("hundred_q scheduler: idempotency check for %s failed: %s", run_type, exc)
        return False


# ── 1. Daily event-trigger sweep ────────────────────────────────────────────

def _seconds_until_next_daily_fire() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_DAILY_FIRE_HOUR_UTC, minute=_DAILY_FIRE_MINUTE_UTC, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_daily_sweep_cycle(force: bool = False) -> Optional[list[dict]]:
    """One scheduled daily-sweep cycle. Exposed so an admin endpoint can
    trigger the identical path on demand. `force` bypasses idempotency."""
    if not force and _already_ran_within("daily_trigger_sweep", hours=_DAILY_IDEMPOTENCY_HOURS):
        logger.info("hundred_q: daily trigger sweep already ran within %dh — skipping", _DAILY_IDEMPOTENCY_HOURS)
        return None
    try:
        from src.research_ideas.hundred_q.runner import run_daily_trigger_sweep
        logger.info("hundred_q: daily trigger sweep starting...")
        fired = run_daily_trigger_sweep()
        logger.info("hundred_q: daily trigger sweep complete — %d event(s) fired", len(fired))
        return fired
    except Exception as exc:
        logger.exception("hundred_q: daily trigger sweep failed: %s", exc)
        return None


# ── 2. Weekly full quant batch ───────────────────────────────────────────────

def _seconds_until_next_weekly_fire() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_WEEKLY_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    days_ahead = (_WEEKLY_FIRE_WEEKDAY - target.weekday()) % 7
    target += timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def run_weekly_batch_cycle(force: bool = False) -> Optional[dict]:
    if not force and _already_ran_within("weekly_quant", hours=_WEEKLY_IDEMPOTENCY_HOURS):
        logger.info("hundred_q: weekly quant batch already ran within %dh — skipping", _WEEKLY_IDEMPOTENCY_HOURS)
        return None
    try:
        from src.research_ideas.hundred_q.runner import run_full_quant_batch
        logger.info("hundred_q: weekly full quant batch starting...")
        cohort = run_full_quant_batch(max_workers=6, save=True, run_type="weekly_quant")
        logger.info(
            "hundred_q: weekly quant batch complete — run_id=%s tickers=%d tiers=%s",
            cohort.run_id, cohort.ticker_count, cohort.tier_counts,
        )
        return cohort.model_dump()
    except Exception as exc:
        logger.exception("hundred_q: weekly quant batch failed: %s", exc)
        return None


# ── 3. Quarterly annual backstop ─────────────────────────────────────────────

_QUARTER_MONTHS = (1, 4, 7, 10)


def _seconds_until_next_quarterly_fire() -> float:
    now = datetime.now(timezone.utc)
    candidates = []
    for year in (now.year, now.year + 1):
        for month in _QUARTER_MONTHS:
            target = datetime(year, month, 1, _BACKSTOP_FIRE_HOUR_UTC, 0, 0, tzinfo=timezone.utc)
            if target > now:
                candidates.append(target)
    target = min(candidates)
    return (target - now).total_seconds()


def run_backstop_cycle(force: bool = False) -> Optional[list[dict]]:
    if not force and _already_ran_within("annual_backstop", days=_BACKSTOP_IDEMPOTENCY_DAYS):
        logger.info("hundred_q: annual backstop already ran within %dd — skipping", _BACKSTOP_IDEMPOTENCY_DAYS)
        return None
    try:
        from src.research_ideas.hundred_q.runner import run_quarterly_annual_backstop
        logger.info("hundred_q: quarterly annual backstop starting...")
        touched = run_quarterly_annual_backstop()
        logger.info("hundred_q: annual backstop complete — %d ticker(s) had stale questions refreshed", len(touched))
        return touched
    except Exception as exc:
        logger.exception("hundred_q: annual backstop failed: %s", exc)
        return None
