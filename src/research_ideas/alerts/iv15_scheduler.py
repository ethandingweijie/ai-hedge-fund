"""
src/research_ideas/alerts/iv15_scheduler.py
============================================
Daily auto-run of the IV15 "price reached fair value" sweep.

Fires once per day at Singapore Time 8:00 AM (= UTC 00:00). Self-running
background thread; survives FastAPI restarts (computes next-fire on startup)
and skips today's slot if a sweep already ran today (idempotency via the
iv15_sweep_log table), so a restart near the boundary won't double-fire.

On each fire it runs run_iv15_sweep(dry_run=False), which compares a fresh
live quote for every SW46 + HK50 name against that name's stored per-share
IV15 and posts ONE consolidated Slack alert for the names that newly crossed
to/below fair value (P/IV15 ≤ trigger band). Hysteresis in iv15_alert_store
means each name alerts once per downward cross, not every morning it sits
cheap.

Env knobs:
  IV15_ALERT_DISABLED=true   — disable the daily sweep
  IV15_ALERT_HOUR_UTC=0      — override UTC fire hour (default 0 = SGT 8am)
  IV15_ALERT_BAND=1.00       — trigger band on live P/IV15 (see iv15_monitor)
  IV15_REARM_BAND=1.05       — re-arm band on live P/IV15
  SLACK_WEBHOOK_URL=https://...   — required for delivery (no-op without it)
  APP_BASE_URL=https://...        — used in the Slack "Open in app" buttons
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Fire at 00:00 UTC = 08:00 Singapore Time (SGT is UTC+8 year-round)
_FIRE_HOUR_UTC = int(os.environ.get("IV15_ALERT_HOUR_UTC", "0"))
_DISABLED = os.environ.get("IV15_ALERT_DISABLED", "false").lower() == "true"

# How recently a sweep must have run to count as "already done today".
_IDEMPOTENCY_HOURS = 20.0


def _seconds_until_next_fire() -> float:
    """Compute seconds from now until the next scheduled fire time."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _swept_today() -> bool:
    """Idempotency — has a sweep run within the last _IDEMPOTENCY_HOURS hours?"""
    try:
        from app.backend.services import iv15_alert_store as store
        return store.swept_within_hours(_IDEMPOTENCY_HOURS)
    except Exception as exc:
        logger.warning("iv15_scheduler: idempotency check failed: %s", exc)
        return False


def _run_sweep_cycle() -> None:
    """One scheduled sweep cycle."""
    if _swept_today():
        logger.info(
            "iv15_scheduler: sweep already ran within last %.0fh — skipping",
            _IDEMPOTENCY_HOURS,
        )
        return
    try:
        from src.research_ideas.alerts.iv15_monitor import run_iv15_sweep

        logger.info("iv15_scheduler: kicking off daily IV15 sweep...")
        result = run_iv15_sweep(dry_run=False)
        logger.info(
            "iv15_scheduler: sweep done — checked=%d fired=%d rearmed=%d "
            "skipped=%d errors=%d slack=%s",
            result.checked, len(result.fired), len(result.rearmed),
            len(result.skipped_no_quote), len(result.errors),
            result.posted_to_slack,
        )
    except Exception as exc:
        logger.exception("iv15_scheduler: sweep cycle failed: %s", exc)


def _run_forever() -> None:
    """Background-thread loop. Sleeps until next fire then runs the sweep."""
    logger.info(
        "IV15 alert scheduler started — next fire at %02d:00 UTC daily "
        "(SGT 08:00 if fire-hour-UTC=0). Disabled=%s.",
        _FIRE_HOUR_UTC, _DISABLED,
    )
    while True:
        if _DISABLED:
            # Toggled off via env — keep looping so it can be hot-enabled.
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_fire()
        logger.info(
            "iv15_scheduler: sleeping %.0fs until next fire (%.1fh)",
            wait_s, wait_s / 3600,
        )
        time.sleep(wait_s + 5)  # +5s to ensure we're past the boundary
        _run_sweep_cycle()


def start_iv15_scheduler() -> Optional[threading.Thread]:
    """
    Spawn the IV15-sweep daemon thread. Called once at FastAPI startup.

    Returns the thread (or None if disabled). The thread is daemon=True so it
    dies cleanly with the process. A module-level strong reference prevents GC.
    """
    if _DISABLED:
        logger.info("IV15 alert scheduler DISABLED via IV15_ALERT_DISABLED env")
        return None
    t = threading.Thread(target=_run_forever, name="iv15-alert-scheduler", daemon=True)
    t.start()
    _SCHEDULER_THREAD_REF.append(t)  # strong ref
    return t


_SCHEDULER_THREAD_REF: list = []
