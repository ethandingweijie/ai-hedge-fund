"""
src/research_ideas/contrarian/scheduler.py
============================================
Daily auto-generation of "Research Idea of the Day".

Fires once per day at Singapore Time 8:00 AM (= UTC 00:00). Self-running
background thread; survives FastAPI restarts (calculates next-fire on
startup) and skips today's slot if an idea was already generated today
(idempotency).

On a successful generation:
  • Idea persisted via contrarian_storage.save_idea()
  • Slack push fired via notifier.notify_slack() if SLACK_WEBHOOK_URL set

Env knobs:
  IDEA_SCHEDULER_DISABLED=true   — set to disable the auto-generation
  IDEA_SCHEDULER_HOUR_UTC=0      — override the UTC fire hour (default 0 = SGT 8am)
  APP_BASE_URL=https://...       — used in the Slack "Open in app" button
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
_FIRE_HOUR_UTC = int(os.environ.get("IDEA_SCHEDULER_HOUR_UTC", "0"))
_DISABLED = os.environ.get("IDEA_SCHEDULER_DISABLED", "false").lower() == "true"


def _seconds_until_next_fire() -> float:
    """Compute seconds from now until the next scheduled fire time."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _idea_already_generated_today() -> bool:
    """Idempotency check — has an idea been generated within the last 20 hours?"""
    try:
        from app.backend.services import contrarian_storage
        latest = contrarian_storage.get_latest_idea()
        if not latest:
            return False
        try:
            generated_at = datetime.fromisoformat(
                latest["generated_at"].replace("Z", "+00:00")
            )
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
            return age_hours < 20
        except Exception:
            return False
    except Exception as exc:
        logger.warning("scheduler: idempotency check failed: %s", exc)
        return False


def _generate_and_notify() -> None:
    """One scheduled generation cycle."""
    if _idea_already_generated_today():
        logger.info("scheduler: idea already generated within last 20h — skipping")
        return

    try:
        from src.research_ideas.contrarian.idea_generator import (
            generate_idea_of_the_day,
        )
        from app.backend.services import contrarian_storage
        from src.research_ideas.contrarian.notifier import notify_slack

        # Exclude recent tickers (~last week) to avoid duplicates
        recent = contrarian_storage.list_ideas(limit=7, include_deleted=True)
        exclude = [r.get("ticker") for r in recent if r.get("ticker")]

        logger.info("scheduler: kicking off daily idea generation...")
        idea = generate_idea_of_the_day(exclude_tickers=exclude)
        if not idea:
            logger.error("scheduler: idea generation returned None")
            return

        contrarian_storage.save_idea(idea)
        logger.info(
            "scheduler: persisted idea %s (%s) — mode=%s",
            idea.get("idea_id", "?"),
            idea.get("ticker", "?"),
            idea.get("idea_mode", "?"),
        )

        # Best-effort Slack push (no-op if SLACK_WEBHOOK_URL not set)
        notify_slack(idea, app_base_url=os.environ.get("APP_BASE_URL"))
    except Exception as exc:
        logger.exception("scheduler: generation cycle failed: %s", exc)


def _run_forever() -> None:
    """Background-thread loop. Sleeps until next fire then generates."""
    logger.info(
        "Idea-of-the-day scheduler started — next fire at %02d:00 UTC daily "
        "(SGT 08:00 if fire-hour-UTC=0). Disabled=%s.",
        _FIRE_HOUR_UTC, _DISABLED,
    )
    while True:
        if _DISABLED:
            # If toggled off via env, still loop but just sleep an hour and
            # re-check (allows hot-toggle without restarting the app).
            time.sleep(3600)
            continue
        wait_s = _seconds_until_next_fire()
        logger.info(
            "scheduler: sleeping %.0fs until next fire (%.1fh)",
            wait_s, wait_s / 3600,
        )
        time.sleep(wait_s + 5)  # +5s to ensure we're past the boundary
        _generate_and_notify()


def start_scheduler() -> Optional[threading.Thread]:
    """
    Spawn the scheduler daemon thread. Called once at FastAPI startup.

    Returns the thread (or None if disabled). The thread is daemon=True so
    it dies cleanly with the process. Holding a strong reference at the
    module level prevents GC.
    """
    if _DISABLED:
        logger.info("Idea-of-the-day scheduler DISABLED via IDEA_SCHEDULER_DISABLED env")
        return None
    t = threading.Thread(target=_run_forever, name="idea-scheduler", daemon=True)
    t.start()
    _SCHEDULER_THREAD_REF.append(t)  # strong ref
    return t


_SCHEDULER_THREAD_REF: list = []
