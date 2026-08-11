"""
src/research_ideas/contrarian/scheduler.py
============================================
Daily auto-generation of "Research Idea of the Day".

Fires once per day at Singapore Time 8:00 AM (= UTC 00:00). As of Phase 4
the FIRE TIME lives in the dedicated scheduler service
(app/backend/scheduler_service.py), which enqueues run_idea_of_the_day_task
on the arq worker; the worker calls _generate_and_notify() below. Skipping
today's slot when an idea was already generated (idempotency) happens inside
the cycle.

On a successful generation:
  • Idea persisted via contrarian_storage.save_idea()
  • Slack push fired via notifier.notify_slack() if SLACK_WEBHOOK_URL set

Env knobs:
  IDEA_SCHEDULER_DISABLED=true   — set to disable the auto-generation
                                   (read by the scheduler service)
  IDEA_SCHEDULER_HOUR_UTC=0      — override the UTC fire hour (default 0 = SGT 8am)
  APP_BASE_URL=https://...       — used in the Slack "Open in app" button
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# Fire at 00:00 UTC = 08:00 Singapore Time (SGT is UTC+8 year-round)
_FIRE_HOUR_UTC = int(os.environ.get("IDEA_SCHEDULER_HOUR_UTC", "0"))


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
