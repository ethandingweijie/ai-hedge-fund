"""
src/research_ideas/hundred_q/scheduler.py
============================================
Phase 3 — three self-running background-thread schedulers, mirroring
src/research_ideas/contrarian/scheduler.py (daily) and
src/research_ideas/fundflow/scheduler.py (weekly). Each is independently
idempotent (via storage.get_latest_run_by_type) and independently
disable-able via env var, matching this repo's existing scheduler
conventions rather than inventing a 4th pattern (see the approved plan's
justification for in-process daemon threads over the DD-style Railway
cron-dispatcher: this feature writes heavily to SQLite per run, at a much
lower frequency than 5-minute intraday polling).

1. Daily event-trigger sweep   — fires ~00:30 UTC (SGT 08:30), checks
   new-filing/insider-buy/earnings/price-shock detectors across the
   watchlist. Idempotent: skips if a sweep completed in the last ~20h.
2. Weekly full quant batch     — fires Sunday 01:00 UTC, rescoring the
   entire pilot universe (cheap — reads through the Knowledge Graph
   cache). Idempotent: skips if a weekly_quant run completed in the
   last ~6 days.
3. Quarterly annual backstop   — fires on the 1st of each quarter month
   at 02:00 UTC, refreshing any stale (>365d) qualitative question for
   Active/On-Deck tickers only. Idempotent: skips if a backstop cycle
   completed in the last ~80 days.

Env knobs:
  HUNDRED_Q_SCHEDULER_DISABLED=true        — disable all three
  HUNDRED_Q_DAILY_SWEEP_DISABLED=true      — disable #1 only
  HUNDRED_Q_WEEKLY_BATCH_DISABLED=true     — disable #2 only
  HUNDRED_Q_BACKSTOP_DISABLED=true         — disable #3 only
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_ALL_DISABLED = os.environ.get("HUNDRED_Q_SCHEDULER_DISABLED", "false").lower() == "true"
_DAILY_DISABLED = _ALL_DISABLED or os.environ.get("HUNDRED_Q_DAILY_SWEEP_DISABLED", "false").lower() == "true"
_WEEKLY_DISABLED = _ALL_DISABLED or os.environ.get("HUNDRED_Q_WEEKLY_BATCH_DISABLED", "false").lower() == "true"
_BACKSTOP_DISABLED = _ALL_DISABLED or os.environ.get("HUNDRED_Q_BACKSTOP_DISABLED", "false").lower() == "true"

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


def _run_daily_forever() -> None:
    logger.info(
        "hundred_q daily trigger-sweep scheduler started — fires %02d:%02d UTC daily. Disabled=%s.",
        _DAILY_FIRE_HOUR_UTC, _DAILY_FIRE_MINUTE_UTC, _DAILY_DISABLED,
    )
    if not _already_ran_within("daily_trigger_sweep", hours=_DAILY_IDEMPOTENCY_HOURS):
        run_daily_sweep_cycle()
    while True:
        if _DAILY_DISABLED:
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_daily_fire()
        logger.info("hundred_q daily sweep: sleeping %.0fs until next fire (%.1fh)", wait_s, wait_s / 3600)
        time.sleep(wait_s + 5)
        run_daily_sweep_cycle()


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


def _run_weekly_forever() -> None:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    logger.info(
        "hundred_q weekly quant-batch scheduler started — fires %s %02d:00 UTC. Disabled=%s.",
        days[_WEEKLY_FIRE_WEEKDAY], _WEEKLY_FIRE_HOUR_UTC, _WEEKLY_DISABLED,
    )
    while True:
        if _WEEKLY_DISABLED:
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_weekly_fire()
        logger.info("hundred_q weekly batch: sleeping %.0fs until next fire (%.1f days)", wait_s, wait_s / 86400)
        time.sleep(wait_s + 5)
        run_weekly_batch_cycle()


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


def _run_backstop_forever() -> None:
    logger.info(
        "hundred_q quarterly annual-backstop scheduler started — fires 1st of Jan/Apr/Jul/Oct at %02d:00 UTC. Disabled=%s.",
        _BACKSTOP_FIRE_HOUR_UTC, _BACKSTOP_DISABLED,
    )
    while True:
        if _BACKSTOP_DISABLED:
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_quarterly_fire()
        logger.info("hundred_q annual backstop: sleeping %.0fs until next fire (%.1f days)", wait_s, wait_s / 86400)
        time.sleep(wait_s + 5)
        run_backstop_cycle()


# ── Entry point ───────────────────────────────────────────────────────────

_SCHEDULER_THREAD_REFS: list[threading.Thread] = []


def start_hundred_q_schedulers() -> list[threading.Thread]:
    """Spawn all three daemon-thread schedulers. Called once at FastAPI
    startup. Each is independently disable-able; a strong module-level
    reference to the threads prevents GC."""
    started: list[threading.Thread] = []
    if not _DAILY_DISABLED:
        t = threading.Thread(target=_run_daily_forever, name="hundred-q-daily-sweep", daemon=True)
        t.start()
        started.append(t)
    else:
        logger.info("hundred_q daily trigger-sweep scheduler DISABLED via env")

    if not _WEEKLY_DISABLED:
        t = threading.Thread(target=_run_weekly_forever, name="hundred-q-weekly-batch", daemon=True)
        t.start()
        started.append(t)
    else:
        logger.info("hundred_q weekly quant-batch scheduler DISABLED via env")

    if not _BACKSTOP_DISABLED:
        t = threading.Thread(target=_run_backstop_forever, name="hundred-q-annual-backstop", daemon=True)
        t.start()
        started.append(t)
    else:
        logger.info("hundred_q annual-backstop scheduler DISABLED via env")

    _SCHEDULER_THREAD_REFS.extend(started)
    return started
