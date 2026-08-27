"""
app/backend/scheduler_service.py
================================
Phase 4 — dedicated scheduler service. Owns the fire TIMES of every
scheduled job; the heavy work runs in the arq worker.

Why this exists
---------------
Until Phase 4 the web process ran 5 daemon-thread schedulers (7 fire
schedules). Every web deploy/restart re-ran startup catch-up logic, and a
second web replica (or an accidentally mis-booted worker, which happened)
would double-fire. This service moves timing out of the request path:

    scheduler (this)  --enqueue-->  Redis queue  --execute-->  arq worker

Start (Railway `scheduler` service) — Variables:
    START_CMD=python -m app.backend.scheduler_service
    REDIS_URL  (reference the Redis plugin)
    DATABASE_URL (reference the Postgres plugin — the sync_schema
                  predeploy inherited from railway.toml needs it)
(Dockerfile.web's CMD expands $START_CMD; the health responder on $PORT
satisfies the inherited healthcheckPath "/".)

Schedules (all fire times UTC — Railway containers run UTC)
------------------------------------------------------------
  name                  task (worker.py)               fires
  vgpm_backfill         run_vgpm_backfill_task         daily 09:00 + startup catch-up
  idea_of_the_day       run_idea_of_the_day_task       daily IDEA_SCHEDULER_HOUR_UTC (0)
  iv15_sweep            run_iv15_sweep_task            daily IV15_ALERT_HOUR_UTC (0)
  fundflow_brief        run_fundflow_brief_task        weekly FUNDFLOW_SCHEDULER_* (Mon 00:00)
  hundred_q_daily_sweep run_hundred_q_daily_sweep_task daily 00:30 + startup catch-up
  hundred_q_weekly_batch run_hundred_q_weekly_batch_task Sunday 01:00
  hundred_q_backstop    run_hundred_q_backstop_task    1st Jan/Apr/Jul/Oct 02:00
  maintenance           run_maintenance_task           daily 03:10 (R2)

Next-fire computation is delegated to the existing scheduler modules'
`_seconds_until_next_*` functions, so their HOUR_UTC / WEEKDAY env
overrides keep working unchanged.

Same-day retry (R2)
-------------------
A separate recheck loop asks each spec's worker-side idempotency gate
(`gate_fn`) every SCHEDULER_RECHECK_S (default 1800s) whether the CURRENT
slot's work is actually done. A failed job never writes its gate, so the
loop re-enqueues the deterministic job id until the gate appears or the
slot rolls. arq paces this: it refuses to enqueue while the failed run's
job key (6 h TTL) or result key (keep_result 1 h) exists, so retries land
at most ~hourly. `recheck_ok` vetoes a retry when firing now would be
illegitimate (VGPM must not backfill before its 09:00 slot). Gate
exceptions are treated as "done" (logged once). maintenance has no gate
and is never rechecked.

Safety (three independent layers)
---------------------------------
1. Slot locks — each fire acquires `sched_lock:{name}:{slot}` via SET NX EX
   (slot = UTC date / ISO year-week / year-quarter of the fire moment), so
   two scheduler replicas can never double-enqueue one slot. Redis down →
   fail OPEN (enqueue anyway), because layer 2+3 still protect.
2. Worker-side idempotency — every task's cycle function keeps its original
   DB-timestamp gate (20 h / 6 d / 80 d / master_universe 09:00 slot).
3. Deterministic job ids — `sched:{name}:{slot}` makes any duplicate
   visible in Redis.

Startup catch-up (preserved from the web-era behaviour): vgpm_backfill and
hundred_q_daily_sweep enqueue once at startup; the WORKER-side gate decides
whether there is actually work to do. Note one deliberate improvement over
the old web behaviour: if catch-up already filled today's VGPM slot, the
09:00 timer skips (the old code would have re-run the backfill).

Env knobs
---------
  SCHEDULER_SERVICE_DISABLED=true      — kill switch: health responder only
  VGPM_BACKFILL_DISABLED=true          — new in Phase 4 (web had none)
  IDEA_SCHEDULER_DISABLED / IV15_ALERT_DISABLED / FUNDFLOW_SCHEDULER_DISABLED
  HUNDRED_Q_SCHEDULER_DISABLED (+ _DAILY_SWEEP / _WEEKLY_BATCH / _BACKSTOP)
  MAINTENANCE_DISABLED=true            — R2 daily housekeeping job
  SCHEDULER_RECHECK_DISABLED=true      — R2 same-day-retry loop kill switch
  SCHEDULER_RECHECK_S=1800             — recheck loop interval (seconds)
All toggles are re-read every loop iteration (hot-toggle by setting the var
and redeploying/restarting this service only).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: How long an enqueued scheduled job may sit in the queue before arq drops
#: it. The worker picks up within seconds; this only covers worker outages.
JOB_EXPIRES_S = 6 * 3600

#: Slot-lock TTLs — comfortably longer than the slot's idempotency window,
#: shorter than the slot period (a late restart must not miss the NEXT slot).
_TTL_DAILY_S = 20 * 3600
_TTL_WEEKLY_S = 6 * 86400
_TTL_QUARTERLY_S = 80 * 86400

#: VGPM fires at 09:00 UTC. The web-era code computed "9am local time", but
#: Railway containers run UTC, so this is the exact production behaviour.
VGPM_FIRE_HOUR_UTC = 9

#: R2 maintenance fires daily at 03:10 UTC — clear of every other fire time
#: (00:00/00:30/01:00/02:00/09:00) so its DB prune never races a scheduled
#: job's writes.
MAINTENANCE_FIRE_HOUR_UTC = 3
MAINTENANCE_FIRE_MINUTE_UTC = 10

#: R2 same-day-retry loop interval (seconds). Failed jobs retry at most
#: ~hourly anyway (arq keeps a failed run's result key for keep_result=1h),
#: so 30 min is a cheap cadence.
_DEFAULT_RECHECK_S = 1800

#: Max sleep chunk while waiting for a fire — keeps env-disable hot-toggle
#: responsive (worst case ~5 min instead of sleeping through to the fire).
_SLEEP_CHUNK_S = 300.0

QUEUE_NAME = "arq:queue"  # must match app.backend.worker.QUEUE_NAME


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "false").lower() == "true"


# ── Slot keys ────────────────────────────────────────────────────────────────

def _daily_slot(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def _weekly_slot(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%G-W%V")  # ISO year-week (handles year boundaries)


def _quarterly_slot(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year}-Q{(now.month - 1) // 3 + 1}"


def _seconds_until_vgpm_fire() -> float:
    """Seconds until the next 09:00 UTC boundary."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=VGPM_FIRE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_maintenance_fire() -> float:
    """Seconds until the next 03:10 UTC boundary (R2 housekeeping)."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=MAINTENANCE_FIRE_HOUR_UTC,
                         minute=MAINTENANCE_FIRE_MINUTE_UTC,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _recheck_interval_s() -> float:
    try:
        v = float(os.environ.get("SCHEDULER_RECHECK_S", str(_DEFAULT_RECHECK_S)))
    except ValueError:
        return float(_DEFAULT_RECHECK_S)
    return v if v >= 60 else float(_DEFAULT_RECHECK_S)


# ── Schedule registry ────────────────────────────────────────────────────────

@dataclass
class ScheduleSpec:
    name: str                              # lock / job-id namespace
    task: str                              # arq function name in worker.py
    next_fire_fn: Callable[[], float]      # seconds until next fire
    slot_fn: Callable[[], str]             # slot key for the fire moment
    lock_ttl_s: int                        # SET NX EX on the slot lock
    is_disabled: Callable[[], bool]        # re-read every iteration
    catch_up: bool = False                 # enqueue once at startup
    # R2 same-day retry — gate_fn returns True when the CURRENT slot's work
    # is already done (the worker-side idempotency check). While it returns
    # False the recheck loop re-enqueues the deterministic job id.
    # recheck_ok (optional) vetoes a retry when firing now is illegitimate
    # (e.g. VGPM before its 09:00 slot). No gate_fn → no recheck.
    gate_fn: Optional[Callable[[], bool]] = None
    recheck_ok: Optional[Callable[[], bool]] = None


def build_schedules() -> list[ScheduleSpec]:
    """The 9 fire schedules. Next-fire math is delegated to the existing
    scheduler modules so their HOUR_UTC / WEEKDAY overrides keep working."""
    from src.research_ideas.alerts import iv15_scheduler as iv15_sched
    from src.research_ideas.contrarian import scheduler as idea_sched
    from src.research_ideas.fundflow import scheduler as fundflow_sched
    from src.research_ideas.hundred_q import scheduler as hq_sched
    from src.data import regional_comps as _rc

    def _hq_all_or(specific: str) -> Callable[[], bool]:
        return lambda: _env_flag("HUNDRED_Q_SCHEDULER_DISABLED") or _env_flag(specific)

    # R2 recheck gates — the SAME worker-side idempotency checks the tasks
    # run internally. True == the current slot's work is already done.
    def _vgpm_done() -> bool:
        from app.backend import worker
        return not worker._vgpm_backfill_due()

    def _vgpm_recheck_ok() -> bool:
        # Never backfill before the nominal 09:00 slot — an early recheck
        # retry would change the product behaviour the slot encodes.
        now = datetime.now(timezone.utc)
        return now >= now.replace(hour=VGPM_FIRE_HOUR_UTC, minute=0,
                                  second=0, microsecond=0)

    return [
        ScheduleSpec(
            name="vgpm_backfill",
            task="run_vgpm_backfill_task",
            next_fire_fn=_seconds_until_vgpm_fire,
            slot_fn=_daily_slot,
            lock_ttl_s=_TTL_DAILY_S,
            is_disabled=lambda: _env_flag("VGPM_BACKFILL_DISABLED"),
            catch_up=True,
            gate_fn=_vgpm_done,
            recheck_ok=_vgpm_recheck_ok,
        ),
        ScheduleSpec(
            name="idea_of_the_day",
            task="run_idea_of_the_day_task",
            next_fire_fn=idea_sched._seconds_until_next_fire,
            slot_fn=_daily_slot,
            lock_ttl_s=_TTL_DAILY_S,
            is_disabled=lambda: _env_flag("IDEA_SCHEDULER_DISABLED"),
            gate_fn=idea_sched._idea_already_generated_today,
        ),
        ScheduleSpec(
            name="iv15_sweep",
            task="run_iv15_sweep_task",
            next_fire_fn=iv15_sched._seconds_until_next_fire,
            slot_fn=_daily_slot,
            lock_ttl_s=_TTL_DAILY_S,
            is_disabled=lambda: _env_flag("IV15_ALERT_DISABLED"),
            gate_fn=iv15_sched._swept_today,
        ),
        ScheduleSpec(
            name="regional_comps_refresh",
            task="run_regional_comps_refresh_task",
            next_fire_fn=_rc.seconds_until_next_fire,
            slot_fn=_weekly_slot,
            lock_ttl_s=_TTL_WEEKLY_S,
            is_disabled=lambda: _env_flag("REGIONAL_COMPS_SCHEDULER_DISABLED"),
            gate_fn=_rc.already_ran_this_week,
        ),
        ScheduleSpec(
            name="fundflow_brief",
            task="run_fundflow_brief_task",
            next_fire_fn=fundflow_sched._seconds_until_next_fire,
            slot_fn=_weekly_slot,
            lock_ttl_s=_TTL_WEEKLY_S,
            is_disabled=lambda: _env_flag("FUNDFLOW_SCHEDULER_DISABLED"),
            gate_fn=fundflow_sched._already_ran_this_week,
        ),
        ScheduleSpec(
            name="hundred_q_daily_sweep",
            task="run_hundred_q_daily_sweep_task",
            next_fire_fn=hq_sched._seconds_until_next_daily_fire,
            slot_fn=_daily_slot,
            lock_ttl_s=_TTL_DAILY_S,
            is_disabled=_hq_all_or("HUNDRED_Q_DAILY_SWEEP_DISABLED"),
            catch_up=True,
            gate_fn=lambda: hq_sched._already_ran_within(
                "daily_trigger_sweep", hours=hq_sched._DAILY_IDEMPOTENCY_HOURS),
        ),
        ScheduleSpec(
            name="hundred_q_weekly_batch",
            task="run_hundred_q_weekly_batch_task",
            next_fire_fn=hq_sched._seconds_until_next_weekly_fire,
            slot_fn=_weekly_slot,
            lock_ttl_s=_TTL_WEEKLY_S,
            is_disabled=_hq_all_or("HUNDRED_Q_WEEKLY_BATCH_DISABLED"),
            gate_fn=lambda: hq_sched._already_ran_within(
                "weekly_quant", hours=hq_sched._WEEKLY_IDEMPOTENCY_HOURS),
        ),
        ScheduleSpec(
            name="hundred_q_backstop",
            task="run_hundred_q_backstop_task",
            next_fire_fn=hq_sched._seconds_until_next_quarterly_fire,
            slot_fn=_quarterly_slot,
            lock_ttl_s=_TTL_QUARTERLY_S,
            is_disabled=_hq_all_or("HUNDRED_Q_BACKSTOP_DISABLED"),
            gate_fn=lambda: hq_sched._already_ran_within(
                "annual_backstop", days=hq_sched._BACKSTOP_IDEMPOTENCY_DAYS),
        ),
        ScheduleSpec(
            name="maintenance",
            task="run_maintenance_task",
            next_fire_fn=_seconds_until_maintenance_fire,
            slot_fn=_daily_slot,
            lock_ttl_s=_TTL_DAILY_S,
            is_disabled=lambda: _env_flag("MAINTENANCE_DISABLED"),
            # No gate_fn on purpose: housekeeping is cheap and idempotent;
            # a missed prune simply runs tomorrow.
        ),
    ]


# ── Redis: slot lock + arq enqueue ──────────────────────────────────────────

async def _acquire_slot_lock(name: str, slot: str, ttl_s: int) -> bool:
    """SET NX EX on sched_lock:{name}:{slot}. True == we own this slot.

    Fails OPEN (returns True) when Redis is absent/erroring: the worker-side
    idempotency gates are the real backstop, and losing a daily backfill to
    a Redis hiccup would be worse than a (harmless) duplicate enqueue.
    """
    from app.backend.services.redis_client import get_redis

    r = await get_redis()
    if r is None:
        return True
    try:
        return bool(await r.set(f"sched_lock:{name}:{slot}", "1", nx=True, ex=ttl_s))
    except Exception as exc:
        logger.warning("slot lock for %s/%s failed (%s) — failing open", name, slot, exc)
        return True


_arq_pool = None


async def _get_pool():
    """Lazily-created arq pool, reused across fires (single event loop)."""
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool

        from app.backend.worker import build_redis_settings

        _arq_pool = await create_pool(build_redis_settings())
    return _arq_pool


async def _enqueue(spec: ScheduleSpec, slot: str) -> bool:
    """Enqueue the slot's job. Returns False when arq already holds a job or
    result key for this deterministic job id (duplicate enqueue / a failed
    run's keep_result window still open) — that is the mechanism that paces
    the R2 recheck retries, not an error."""
    from app.backend.worker import QUEUE_NAME as WORKER_QUEUE

    pool = await _get_pool()
    job_id = f"sched:{spec.name}:{slot}"
    job = await pool.enqueue_job(
        spec.task,
        _job_id=job_id,
        _queue_name=WORKER_QUEUE,
        _expires=JOB_EXPIRES_S,
    )
    if job is None:
        logger.info("%s: job %s already queued or finished recently — skipping",
                    spec.name, job_id)
        return False
    logger.info("%s: enqueued %s (job_id=%s)", spec.name, spec.task, job_id)
    return True


async def _fire(spec: ScheduleSpec, slot: str, catch_up: bool = False) -> None:
    label = f"{spec.name} startup catch-up" if catch_up else spec.name
    if spec.is_disabled():
        logger.info("%s: disabled via env — not firing for slot %s", spec.name, slot)
        return
    if not await _acquire_slot_lock(spec.name, slot, spec.lock_ttl_s):
        logger.info(
            "%s: slot %s already claimed by another replica — skipping",
            spec.name, slot,
        )
        return
    try:
        await _enqueue(spec, slot)
    except Exception:
        logger.exception("%s: enqueue failed for slot %s", label, slot)


# ── Per-schedule loop ────────────────────────────────────────────────────────

async def _run_schedule(spec: ScheduleSpec) -> None:
    logger.info("schedule '%s' started (task=%s, catch_up=%s)",
                spec.name, spec.task, spec.catch_up)

    if spec.catch_up:
        try:
            await _fire(spec, spec.slot_fn(), catch_up=True)
        except Exception:
            logger.exception("%s: startup catch-up failed", spec.name)

    while True:
        try:
            if spec.is_disabled():
                # Sleep-and-recheck rather than exit, so the env toggle can
                # be hot-enabled (same convention as the web-era threads).
                await asyncio.sleep(_SLEEP_CHUNK_S)
                continue

            wait_s = spec.next_fire_fn()
            logger.info("%s: next fire in %.0fs (%.1fh)",
                        spec.name, wait_s, wait_s / 3600)

            # Chunked sleep so an env disable toggle takes effect promptly.
            deadline = time.monotonic() + wait_s + 5  # +5s past the boundary
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if spec.is_disabled():
                    break
                await asyncio.sleep(min(remaining, _SLEEP_CHUNK_S))

            if spec.is_disabled():
                continue

            await _fire(spec, spec.slot_fn())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s: loop error — retrying in 5 min", spec.name)
            await asyncio.sleep(_SLEEP_CHUNK_S)


# ── R2 same-day retry: gate-driven recheck loop ──────────────────────────────

async def _run_recheck_loop(specs: list[ScheduleSpec]) -> None:
    """Retry failed scheduled jobs within their slot.

    Every SCHEDULER_RECHECK_S (default 1800s), ask each gated spec's
    worker-side idempotency gate whether the CURRENT slot's work actually
    happened. A failed job never writes its gate, so the loop re-enqueues
    the deterministic job id until the gate appears or the slot rolls over.
    arq paces the retries itself: it refuses to enqueue while the failed
    run's job key (expires=6h) or result key (keep_result=1h) exists, so a
    broken job retries at most ~hourly.

    Safety: no slot-lock changes (a recheck enqueue goes through _enqueue's
    deterministic job id); gate exceptions are treated as "done" and logged
    once; SCHEDULER_RECHECK_DISABLED=true kills the loop's behaviour without
    stopping the service.
    """
    logger.info("recheck loop started (interval %.0fs, %d gated specs)",
                _recheck_interval_s(),
                sum(1 for s in specs if s.gate_fn is not None))
    gate_err_logged: set[str] = set()
    while True:
        await asyncio.sleep(_recheck_interval_s())
        if _env_flag("SCHEDULER_RECHECK_DISABLED"):
            continue
        for spec in specs:
            if spec.gate_fn is None or spec.is_disabled():
                continue
            if spec.recheck_ok is not None:
                try:
                    if not spec.recheck_ok():
                        continue
                except Exception:
                    continue
            try:
                done = await asyncio.to_thread(spec.gate_fn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if spec.name not in gate_err_logged:
                    logger.warning(
                        "recheck: %s gate raised (%s: %s) — treating slot as done",
                        spec.name, type(exc).__name__, exc)
                    gate_err_logged.add(spec.name)
                continue
            if done:
                continue
            slot = spec.slot_fn()
            try:
                if await _enqueue(spec, slot):
                    logger.info("recheck: %s gate open for slot %s — re-enqueued",
                                spec.name, slot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("recheck: %s enqueue failed for slot %s",
                                 spec.name, slot)


# ── Entry point ──────────────────────────────────────────────────────────────

async def _run_all() -> None:
    specs = build_schedules()
    logger.info("scheduler service starting: %d schedules (%s)",
                len(specs), ", ".join(s.name for s in specs))
    tasks = [asyncio.create_task(_run_schedule(s)) for s in specs]
    tasks.append(asyncio.create_task(_run_recheck_loop(specs)))
    await asyncio.gather(*tasks)


def _keep_alive() -> None:
    while True:
        time.sleep(3600)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    from app.backend.health_responder import start_health_responder

    start_health_responder()

    if _env_flag("SCHEDULER_SERVICE_DISABLED"):
        logger.info(
            "scheduler service DISABLED via SCHEDULER_SERVICE_DISABLED — "
            "running health responder only"
        )
        _keep_alive()
        return

    try:
        asyncio.run(_run_all())
    except KeyboardInterrupt:
        logger.info("scheduler service stopped")


if __name__ == "__main__":
    main()
