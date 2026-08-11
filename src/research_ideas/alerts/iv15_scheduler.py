"""
src/research_ideas/alerts/iv15_scheduler.py
============================================
Daily auto-run of the IV15 "price reached fair value" sweep.

Fires once per day at Singapore Time 8:00 AM (= UTC 00:00). As of Phase 4
the FIRE TIME lives in the dedicated scheduler service
(app/backend/scheduler_service.py), which enqueues run_iv15_sweep_task on
the arq worker; the worker calls _run_sweep_cycle() below. Skipping today's
slot when a sweep already ran (idempotency via the iv15_sweep_log table)
happens inside the cycle, so a restart near the boundary won't double-fire.

On each fire it runs run_iv15_sweep(dry_run=False), which compares a fresh
live quote for every SW46 + HK50 name against that name's stored per-share
IV15 and posts ONE consolidated Slack alert for the names that newly crossed
to/below fair value (P/IV15 ≤ trigger band). Hysteresis in iv15_alert_store
means each name alerts once per downward cross, not every morning it sits
cheap.

Env knobs:
  IV15_ALERT_DISABLED=true   — disable the daily sweep (read by the
                               scheduler service)
  IV15_ALERT_HOUR_UTC=0      — override UTC fire hour (default 0 = SGT 8am)
  IV15_ALERT_BAND=1.00       — trigger band on live P/IV15 (see iv15_monitor)
  IV15_REARM_BAND=1.05       — re-arm band on live P/IV15
  SLACK_WEBHOOK_URL=https://...   — required for delivery (no-op without it)
  APP_BASE_URL=https://...        — used in the Slack "Open in app" buttons
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# Fire at 00:00 UTC = 08:00 Singapore Time (SGT is UTC+8 year-round)
_FIRE_HOUR_UTC = int(os.environ.get("IV15_ALERT_HOUR_UTC", "0"))

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

