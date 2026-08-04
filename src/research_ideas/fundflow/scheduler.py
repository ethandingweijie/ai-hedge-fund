"""
src/research_ideas/fundflow/scheduler.py
=========================================
WEEKLY auto-generation of the geographic fund-flow brief.

Mirrors src/research_ideas/contrarian/scheduler.py, with a weekly cadence
instead of daily. Self-running background thread; survives FastAPI restarts
(recomputes next-fire on startup) and skips the slot if a run already
completed inside the current week (idempotency).

Default fire: Monday 00:00 UTC = Monday 08:00 Singapore Time. Monday morning
SGT lands after Friday's US close and before the new US week opens, so the
brief summarises a complete week of flows rather than a half-formed one.

On a successful run:
  • Cohort persisted via fundflow_storage.save_fundflow_run()
  • Summary written by DeepSeek (falls back to the computed draft)
  • Slack push fired via notifier.notify_slack() if SLACK_WEBHOOK_URL set

Env knobs:
  FUNDFLOW_SCHEDULER_DISABLED=true  — disable the weekly run
  FUNDFLOW_SCHEDULER_WEEKDAY=0      — 0=Mon … 6=Sun (default 0)
  FUNDFLOW_SCHEDULER_HOUR_UTC=0     — UTC fire hour (default 0 = SGT 08:00)
  SLACK_WEBHOOK_URL=https://...     — omit to skip the Slack push
  APP_BASE_URL=https://...          — used in the Slack "Open in app" button
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


_FIRE_HOUR_UTC = int(os.environ.get("FUNDFLOW_SCHEDULER_HOUR_UTC", "0"))
_FIRE_WEEKDAY = int(os.environ.get("FUNDFLOW_SCHEDULER_WEEKDAY", "0"))  # 0 = Monday
_DISABLED = os.environ.get("FUNDFLOW_SCHEDULER_DISABLED", "false").lower() == "true"

# A run inside this window means the week's slot is already filled. Set below
# a full week so a restart on the fire day cannot double-post, but high enough
# that a run a few hours early still counts.
_IDEMPOTENCY_HOURS = 6 * 24


def _seconds_until_next_fire() -> float:
    """Seconds from now until the next scheduled weekday/hour boundary."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    days_ahead = (_FIRE_WEEKDAY - target.weekday()) % 7
    target += timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def _already_ran_this_week() -> bool:
    """Idempotency — did a cohort run complete within the last ~6 days?"""
    try:
        from app.backend.services import fundflow_storage
        latest = fundflow_storage.get_latest_fundflow_run()
        if not latest or not latest.get("created_at"):
            return False
        created = datetime.fromisoformat(
            str(latest["created_at"]).replace("Z", "+00:00")
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        return age_h < _IDEMPOTENCY_HOURS
    except Exception as exc:
        logger.warning("fundflow scheduler: idempotency check failed: %s", exc)
        return False


def run_weekly_cycle(force: bool = False, notify: bool = True) -> Optional[dict]:
    """
    One scheduled cycle: score the universe, persist, push to Slack.

    Exposed (not private) so the API can trigger the identical path on demand
    — a "test the Slack push" button should exercise the real code, not a
    parallel copy of it. `force` bypasses the weekly idempotency guard.

    Returns the cohort dict on success, None if skipped or failed.
    """
    if not force and _already_ran_this_week():
        logger.info("fundflow scheduler: a run completed within the last 6 days — skipping")
        return None

    try:
        from src.research_ideas.fundflow.runner import run_fundflow
        from src.research_ideas.fundflow.notifier import notify_slack

        logger.info("fundflow scheduler: starting weekly geographic flow run…")
        cohort = run_fundflow(save=True, narrate_summary=True)
        if cohort is None or cohort.region_count == 0:
            logger.error("fundflow scheduler: run produced no regions — not notifying")
            return None

        payload = cohort.model_dump()
        logger.info(
            "fundflow scheduler: run %s persisted — %d regions, %d inflow / %d outflow, summary via %s",
            cohort.run_id, cohort.region_count, cohort.inflow_count,
            cohort.outflow_count,
            cohort.summary.summary_source if cohort.summary else "none",
        )

        if notify:
            notify_slack(payload, app_base_url=os.environ.get("APP_BASE_URL"))
        return payload
    except Exception as exc:
        logger.exception("fundflow scheduler: weekly cycle failed: %s", exc)
        return None


def _run_forever() -> None:
    """Background-thread loop. Sleeps until the next weekly slot, then runs."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    logger.info(
        "Fund-flow WEEKLY scheduler started — fires %s %02d:00 UTC. Disabled=%s.",
        days[_FIRE_WEEKDAY % 7], _FIRE_HOUR_UTC, _DISABLED,
    )
    while True:
        if _DISABLED:
            # Sleep-and-recheck rather than exit, so the env toggle can be
            # flipped without restarting the app.
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_fire()
        logger.info(
            "fundflow scheduler: sleeping %.0fs until next fire (%.1fh / %.1f days)",
            wait_s, wait_s / 3600, wait_s / 86400,
        )
        time.sleep(wait_s + 5)   # +5s to land past the boundary
        run_weekly_cycle()


def start_fundflow_scheduler() -> Optional[threading.Thread]:
    """
    Spawn the weekly scheduler daemon thread. Called once at FastAPI startup.

    Returns the thread (or None if disabled). daemon=True so it dies with the
    process; the module-level list holds a strong reference so it is not GC'd.
    """
    if _DISABLED:
        logger.info("Fund-flow weekly scheduler DISABLED via FUNDFLOW_SCHEDULER_DISABLED")
        return None
    t = threading.Thread(target=_run_forever, name="fundflow-scheduler", daemon=True)
    t.start()
    _SCHEDULER_THREAD_REF.append(t)
    return t


_SCHEDULER_THREAD_REF: list = []
