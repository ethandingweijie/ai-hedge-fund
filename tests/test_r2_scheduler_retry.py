"""
tests/test_r2_scheduler_retry.py
================================
R2 batch — scheduler same-day retry (gate-driven recheck) + maintenance
task wiring:

* app/backend/scheduler_service.py::_run_recheck_loop — retries a slot
  whose worker-side idempotency gate is still open, honours recheck_ok
  vetoes, survives gate exceptions, respects the disable kill switch.
* app/backend/scheduler_service.py::build_schedules — all 8 specs, correct
  gate_fn/recheck_ok wiring (maintenance deliberately ungated).
* app/backend/scheduler_service.py::_enqueue — duplicate-enqueue handling
  (arq returns None when the deterministic job id already exists).
* app/backend/worker.py::_sched_gate_outcome + run_maintenance_task.
"""
import asyncio
import logging
from datetime import datetime, timezone

import pytest

import app.backend.scheduler_service as sched_svc
from app.backend.scheduler_service import ScheduleSpec


def _stub_spec(
    name: str = "stub",
    *,
    gate=None,
    recheck_ok=None,
    disabled: bool = False,
    slot: str = "2026-08-16",
) -> ScheduleSpec:
    return ScheduleSpec(
        name=name,
        task=f"run_{name}_task",
        next_fire_fn=lambda: 3600.0,
        slot_fn=lambda: slot,
        lock_ttl_s=60,
        is_disabled=lambda: disabled,
        gate_fn=gate,
        recheck_ok=recheck_ok,
    )


def _run_recheck_once(specs, monkeypatch, hold_s: float = 0.15) -> list[tuple[str, str]]:
    """Run the recheck loop briefly with a near-zero interval and a recorded
    _enqueue stub; returns [(spec_name, slot), ...] of re-enqueues."""
    enqueued: list[tuple[str, str]] = []

    async def fake_enqueue(spec, slot):
        enqueued.append((spec.name, slot))
        return True

    monkeypatch.setattr(sched_svc, "_recheck_interval_s", lambda: 0.01)
    monkeypatch.setattr(sched_svc, "_enqueue", fake_enqueue)

    async def driver():
        task = asyncio.create_task(sched_svc._run_recheck_loop(specs))
        await asyncio.sleep(hold_s)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    return enqueued


# ── _run_recheck_loop behaviour ──────────────────────────────────────────────

def test_recheck_gate_done_no_enqueue(monkeypatch):
    spec = _stub_spec(gate=lambda: True)
    assert _run_recheck_once([spec], monkeypatch) == []


def test_recheck_gate_open_reenqueues_current_slot(monkeypatch):
    spec = _stub_spec("idea_of_the_day", gate=lambda: False, slot="2026-08-16")
    hits = _run_recheck_once([spec], monkeypatch)
    assert hits and all(h == ("idea_of_the_day", "2026-08-16") for h in hits)


def test_recheck_ok_veto_skips_gate_and_enqueue(monkeypatch):
    gate_calls = []

    def _gate():
        gate_calls.append(1)
        return False

    spec = _stub_spec(gate=_gate, recheck_ok=lambda: False)  # e.g. VGPM pre-09:00
    assert _run_recheck_once([spec], monkeypatch) == []
    assert gate_calls == []  # vetoed BEFORE the gate is consulted


def test_recheck_gate_exception_skips_without_crash(monkeypatch, caplog):
    def _boom():
        raise RuntimeError("pg down")

    spec = _stub_spec("flaky", gate=_boom)
    with caplog.at_level(logging.WARNING, logger="app.backend.scheduler_service"):
        hits = _run_recheck_once([spec], monkeypatch, hold_s=0.2)
    assert hits == []
    # logged exactly ONCE despite multiple loop iterations hitting the exception
    gate_warnings = [r for r in caplog.records if "gate raised" in r.getMessage()]
    assert len(gate_warnings) == 1


def test_recheck_skips_disabled_and_ungated_specs(monkeypatch):
    disabled = _stub_spec("disabled_spec", gate=lambda: False, disabled=True)
    ungated = _stub_spec("maintenance")  # no gate_fn → never rechecked
    assert _run_recheck_once([disabled, ungated], monkeypatch) == []


def test_recheck_kill_switch(monkeypatch):
    monkeypatch.setenv("SCHEDULER_RECHECK_DISABLED", "true")
    spec = _stub_spec(gate=lambda: False)
    assert _run_recheck_once([spec], monkeypatch) == []


def test_recheck_enqueue_error_is_swallowed(monkeypatch, caplog):
    """A broken enqueue (Redis down) must not kill the loop or the spec."""
    async def bad_enqueue(spec, slot):
        raise ConnectionError("redis down")

    monkeypatch.setattr(sched_svc, "_recheck_interval_s", lambda: 0.01)
    monkeypatch.setattr(sched_svc, "_enqueue", bad_enqueue)

    async def driver():
        task = asyncio.create_task(sched_svc._run_recheck_loop(
            [_stub_spec(gate=lambda: False)]))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())  # must not raise


# ── _enqueue: arq duplicate handling paces the retries ───────────────────────

class _FakePool:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def enqueue_job(self, task, *, _job_id=None, _queue_name=None, _expires=None):
        self.calls.append((task, _job_id, _queue_name))
        return self._result


def test_enqueue_returns_false_on_duplicate(monkeypatch):
    pool = _FakePool(None)  # arq: job key or result key already exists
    monkeypatch.setattr(sched_svc, "_get_pool", _const_pool(pool))
    spec = _stub_spec("iv15_sweep", slot="2026-08-16")
    assert asyncio.run(sched_svc._enqueue(spec, "2026-08-16")) is False
    (task, job_id, queue) = pool.calls[0]
    assert task == "run_iv15_sweep_task"
    assert job_id == "sched:iv15_sweep:2026-08-16"  # deterministic id
    assert queue == sched_svc.QUEUE_NAME


def test_enqueue_returns_true_when_accepted(monkeypatch):
    pool = _FakePool(object())  # arq accepted the job
    monkeypatch.setattr(sched_svc, "_get_pool", _const_pool(pool))
    spec = _stub_spec("fundflow_brief", slot="2026-W33")
    assert asyncio.run(sched_svc._enqueue(spec, "2026-W33")) is True


def _const_pool(pool):
    async def _get():
        return pool
    return _get


# ── build_schedules wiring ───────────────────────────────────────────────────

GATED = {
    "vgpm_backfill", "idea_of_the_day", "iv15_sweep", "fundflow_brief",
    "hundred_q_daily_sweep", "hundred_q_weekly_batch", "hundred_q_backstop",
    # W2 — weekly exchange-comp refresh (US/HK/SG). Gated so a run that dies
    # partway through is retried rather than skipped for the week.
    "regional_comps_refresh",
}


def test_build_schedules_wiring():
    specs = {s.name: s for s in sched_svc.build_schedules()}
    assert set(specs) == GATED | {"maintenance"}

    for name in GATED:
        assert specs[name].gate_fn is not None, f"{name} lost its R2 gate"
    assert specs["maintenance"].gate_fn is None  # deliberate: never rechecked
    assert specs["maintenance"].task == "run_maintenance_task"

    # recheck_ok is a VGPM-only veto (no backfill before the 09:00 slot)
    assert specs["vgpm_backfill"].recheck_ok is not None
    for name in set(specs) - {"vgpm_backfill"}:
        assert specs[name].recheck_ok is None, f"{name} should have no veto"

    # VGPM veto semantics: True iff current time >= today's 09:00 UTC
    now = datetime.now(timezone.utc)
    expected = now >= now.replace(hour=sched_svc.VGPM_FIRE_HOUR_UTC, minute=0,
                                  second=0, microsecond=0)
    assert specs["vgpm_backfill"].recheck_ok() is expected


def test_maintenance_fire_time_clears_other_jobs():
    # 03:10 UTC sits clear of 00:00 / 00:30 / 01:00 / 02:00 / 09:00 fires
    assert sched_svc.MAINTENANCE_FIRE_HOUR_UTC == 3
    assert sched_svc.MAINTENANCE_FIRE_MINUTE_UTC == 10
    wait = sched_svc._seconds_until_maintenance_fire()
    assert 0 < wait <= 86400


def test_recheck_interval_floor(monkeypatch):
    monkeypatch.delenv("SCHEDULER_RECHECK_S", raising=False)
    assert sched_svc._recheck_interval_s() == float(sched_svc._DEFAULT_RECHECK_S)
    monkeypatch.setenv("SCHEDULER_RECHECK_S", "5")          # below floor
    assert sched_svc._recheck_interval_s() == float(sched_svc._DEFAULT_RECHECK_S)
    monkeypatch.setenv("SCHEDULER_RECHECK_S", "not-a-number")
    assert sched_svc._recheck_interval_s() == float(sched_svc._DEFAULT_RECHECK_S)
    monkeypatch.setenv("SCHEDULER_RECHECK_S", "120")
    assert sched_svc._recheck_interval_s() == 120.0


# ── worker-side: gate outcome classification + maintenance task ──────────────

def test_sched_gate_outcome_done():
    from app.backend.worker import _sched_gate_outcome
    assert _sched_gate_outcome("t", lambda: True) is True


def test_sched_gate_outcome_open_logs(caplog):
    from app.backend.worker import _sched_gate_outcome
    with caplog.at_level(logging.WARNING, logger="app.backend.worker"):
        assert _sched_gate_outcome("t", lambda: False) is False
    assert any("gate is still open" in r.getMessage() for r in caplog.records)


def test_sched_gate_outcome_exception_is_open(caplog):
    from app.backend.worker import _sched_gate_outcome

    def _boom():
        raise RuntimeError("db down")

    with caplog.at_level(logging.WARNING, logger="app.backend.worker"):
        assert _sched_gate_outcome("t", _boom) is False
    assert any("gate check failed" in r.getMessage() for r in caplog.records)


def test_run_maintenance_task(monkeypatch, caplog):
    from app.backend import worker
    from app.backend.services import analysis_service as asvc

    monkeypatch.setattr(asvc, "cleanup_stale_checkpoints", lambda: 3)
    with caplog.at_level(logging.INFO, logger="app.backend.worker"):
        out = asyncio.run(worker.run_maintenance_task({}))
    assert out == {"ran": True, "deleted": 3}
    assert any("removed 3 stale checkpoint rows" in r.getMessage()
               for r in caplog.records)


def test_maintenance_registered_in_worker_settings():
    from app.backend import worker
    names = {f.__name__ for f in worker.WorkerSettings.functions}
    assert "run_maintenance_task" in names
