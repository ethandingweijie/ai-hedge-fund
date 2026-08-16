"""
tests/test_scheduler_separation.py
==================================
Phase 4 — scheduler separation invariants.

The scheduler service (app/backend/scheduler_service.py) owns fire TIMES;
the arq worker (app/backend/worker.py) owns EXECUTION. These tests pin the
seam between them:

  * every schedule in the registry maps to a task that is actually
    registered in WorkerSettings.functions (an unregistered name would
    enqueue a job the worker immediately fails as "unknown function");
  * the lazy-import targets each scheduled task calls still exist;
  * slot-lock keys use SET NX EX semantics and fail open without Redis;
  * slot key formats (daily / ISO week / quarter) are stable;
  * the legacy *_DISABLED env toggles (plus the master
    HUNDRED_Q_SCHEDULER_DISABLED) are re-read live per iteration.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import pytest

UTC = timezone.utc


# ── Registry ↔ worker registration ───────────────────────────────────────────

def test_every_scheduled_task_is_registered_in_worker():
    from app.backend.scheduler_service import build_schedules
    from app.backend.worker import WorkerSettings

    registered = {fn.__name__ for fn in WorkerSettings.functions}
    for spec in build_schedules():
        assert spec.task in registered, (
            f"schedule '{spec.name}' enqueues '{spec.task}', which is not "
            f"in WorkerSettings.functions — the worker would reject it"
        )


def test_registry_shape():
    from app.backend.scheduler_service import build_schedules

    specs = build_schedules()
    assert len(specs) == 8  # 7 scheduled jobs + R2 daily maintenance

    names = [s.name for s in specs]
    assert len(set(names)) == 8  # unique lock/job-id namespaces

    catch_up = {s.name for s in specs if s.catch_up}
    # Only these two had startup catch-up in the web-era code — preserved.
    assert catch_up == {"vgpm_backfill", "hundred_q_daily_sweep"}


def test_queue_name_matches_worker():
    from app.backend import scheduler_service
    from app.backend.worker import QUEUE_NAME

    assert scheduler_service.QUEUE_NAME == QUEUE_NAME


# ── Lazy-import targets of the worker tasks ─────────────────────────────────

def test_worker_task_cycle_targets_exist():
    """Each scheduled worker task lazy-imports its cycle function; verify
    the names exist WITHOUT calling them (no LLM / DB side effects)."""
    from src.research_ideas.alerts import iv15_scheduler
    from src.research_ideas.contrarian import scheduler as idea_sched
    from src.research_ideas.fundflow import scheduler as fundflow_sched
    from src.research_ideas.hundred_q import scheduler as hq_sched

    assert callable(idea_sched._generate_and_notify)
    assert callable(iv15_scheduler._run_sweep_cycle)
    assert callable(fundflow_sched.run_weekly_cycle)
    assert callable(hq_sched.run_daily_sweep_cycle)
    assert callable(hq_sched.run_weekly_batch_cycle)
    assert callable(hq_sched.run_backstop_cycle)


def test_vgpm_backfill_targets_exist():
    from app.backend.services import screener_service

    assert callable(screener_service.backfill_master_universe)
    assert callable(screener_service._connect)
    assert callable(screener_service._ensure_tables)


# ── Next-fire math (delegated to the legacy modules) ────────────────────────

def test_next_fire_functions_return_positive_delays():
    from app.backend.scheduler_service import build_schedules

    for spec in build_schedules():
        wait = spec.next_fire_fn()
        assert wait > 0, f"{spec.name}: next-fire delay must be positive, got {wait}"


def test_vgpm_fire_targets_utc_0900():
    from app.backend.scheduler_service import (
        VGPM_FIRE_HOUR_UTC,
        _seconds_until_vgpm_fire,
    )

    assert VGPM_FIRE_HOUR_UTC == 9
    # Never more than 24h away.
    assert _seconds_until_vgpm_fire() <= 24 * 3600


# ── Slot key formats ─────────────────────────────────────────────────────────

def test_daily_slot_format():
    from app.backend.scheduler_service import _daily_slot

    assert _daily_slot(datetime(2026, 8, 11, 9, 0, tzinfo=UTC)) == "2026-08-11"
    # A fire at 00:00:05 still belongs to the new day.
    assert _daily_slot(datetime(2026, 8, 12, 0, 0, 5, tzinfo=UTC)) == "2026-08-12"


def test_weekly_slot_format_and_rollover():
    from app.backend.scheduler_service import _weekly_slot

    a = _weekly_slot(datetime(2026, 8, 10, 0, 0, tzinfo=UTC))  # a Monday
    b = _weekly_slot(datetime(2026, 8, 17, 0, 0, tzinfo=UTC))  # next Monday
    assert re.fullmatch(r"\d{4}-W\d{2}", a)
    assert a != b


def test_quarterly_slot_format():
    from app.backend.scheduler_service import _quarterly_slot

    assert _quarterly_slot(datetime(2026, 1, 1, 2, 0, tzinfo=UTC)) == "2026-Q1"
    assert _quarterly_slot(datetime(2026, 4, 1, 2, 0, tzinfo=UTC)) == "2026-Q2"
    assert _quarterly_slot(datetime(2026, 7, 1, 2, 0, tzinfo=UTC)) == "2026-Q3"
    assert _quarterly_slot(datetime(2026, 10, 1, 2, 0, tzinfo=UTC)) == "2026-Q4"


# ── Slot-lock semantics ──────────────────────────────────────────────────────

class _StubRedis:
    """Records set() calls; first SET NX wins, like real Redis."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.held: set[str] = set()

    async def set(self, key, value, nx=None, ex=None):
        self.calls.append((key, value, nx, ex))
        if nx and key in self.held:
            return None
        self.held.add(key)
        return True


def test_slot_lock_uses_set_nx_ex(monkeypatch):
    from app.backend.services import redis_client
    from app.backend.scheduler_service import _acquire_slot_lock

    stub = _StubRedis()

    async def fake_get_redis():
        return stub

    monkeypatch.setattr(redis_client, "get_redis", fake_get_redis)

    got = asyncio.run(_acquire_slot_lock("vgpm_backfill", "2026-08-11", 72000))
    assert got is True
    key, value, nx, ex = stub.calls[0]
    assert key == "sched_lock:vgpm_backfill:2026-08-11"
    assert nx is True
    assert ex == 72000

    # Second replica asking for the same slot loses.
    assert asyncio.run(_acquire_slot_lock("vgpm_backfill", "2026-08-11", 72000)) is False
    # A different slot is independent.
    assert asyncio.run(_acquire_slot_lock("vgpm_backfill", "2026-08-12", 72000)) is True


def test_slot_lock_fails_open_without_redis(monkeypatch):
    from app.backend.services import redis_client
    from app.backend.scheduler_service import _acquire_slot_lock

    async def fake_get_redis():
        return None

    monkeypatch.setattr(redis_client, "get_redis", fake_get_redis)
    assert asyncio.run(_acquire_slot_lock("iv15_sweep", "2026-08-11", 72000)) is True


def test_slot_lock_fails_open_on_redis_error(monkeypatch):
    from app.backend.services import redis_client
    from app.backend.scheduler_service import _acquire_slot_lock

    class _Broken:
        async def set(self, key, value, nx=None, ex=None):
            raise ConnectionError("redis gone")

    async def fake_get_redis():
        return _Broken()

    monkeypatch.setattr(redis_client, "get_redis", fake_get_redis)
    assert asyncio.run(_acquire_slot_lock("iv15_sweep", "2026-08-11", 72000)) is True


# ── Env toggles are re-read live (hot-toggle) ────────────────────────────────

def test_env_disable_flags_read_per_call(monkeypatch):
    from app.backend.scheduler_service import build_schedules

    specs = {s.name: s for s in build_schedules()}

    cases = [
        ("vgpm_backfill", ["VGPM_BACKFILL_DISABLED"]),
        ("idea_of_the_day", ["IDEA_SCHEDULER_DISABLED"]),
        ("iv15_sweep", ["IV15_ALERT_DISABLED"]),
        ("fundflow_brief", ["FUNDFLOW_SCHEDULER_DISABLED"]),
        ("hundred_q_daily_sweep",
         ["HUNDRED_Q_DAILY_SWEEP_DISABLED", "HUNDRED_Q_SCHEDULER_DISABLED"]),
        ("hundred_q_weekly_batch",
         ["HUNDRED_Q_WEEKLY_BATCH_DISABLED", "HUNDRED_Q_SCHEDULER_DISABLED"]),
        ("hundred_q_backstop",
         ["HUNDRED_Q_BACKSTOP_DISABLED", "HUNDRED_Q_SCHEDULER_DISABLED"]),
    ]

    for name, env_vars in cases:
        spec = specs[name]
        assert spec.is_disabled() is False, f"{name} disabled with no env set"
        for var in env_vars:
            monkeypatch.setenv(var, "true")
            assert spec.is_disabled() is True, f"{name} should honour {var}"
            monkeypatch.delenv(var)
        assert spec.is_disabled() is False, f"{name} flag not hot-re-read"
