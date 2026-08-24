"""
tests/test_worker.py
====================
Phase 2c — arq worker task definitions.

Runs without Redis or network: the progress bus is monkeypatched, the
pipeline is faked, and the tests assert task logic (event ordering,
terminal markers, error paths, kind dispatch).
"""
import asyncio

import pytest

from app.backend import worker


# ── WorkerSettings ────────────────────────────────────────────────────────────

def test_worker_settings_shape():
    ws = worker.WorkerSettings
    names = {f.__name__ for f in ws.functions}
    assert names == {
        "run_analysis_pipeline_task",
        "run_research_job_task",
        "run_hedge_fund_graph_task",
        # Phase 4 — scheduled jobs enqueued by scheduler_service.py
        "run_vgpm_backfill_task",
        "run_idea_of_the_day_task",
        "run_iv15_sweep_task",
        "run_fundflow_brief_task",
        "run_hundred_q_daily_sweep_task",
        "run_hundred_q_weekly_batch_task",
        "run_hundred_q_backstop_task",
        # R2 — daily housekeeping (stale-checkpoint prune)
        "run_maintenance_task",
        # Workstream R1.e — analyst-report Drive folder sync (8am cron +
        # manual trigger via the research routes)
        "run_drive_sync_task",
    }
    assert ws.max_jobs == 10
    assert ws.job_timeout == 3600       # 60 min — VGPM backfill can exceed 30
    assert ws.retry_jobs is False
    assert ws.queue_name == "arq:queue"


def test_research_kinds_cover_spawn_points():
    assert worker.RESEARCH_KINDS == {
        "idea_of_the_day_gen", "hk50_qual", "hk50_qual_ticker",
        "refresh", "score_adhoc", "hundred_q_refresh",
        # Workstream Q2 — on-demand full-table qualitative sweep
        "qual_sweep",
        # Workstream R1.e — analyst-report Drive folder sync
        "drive_sync",
    }


def test_unknown_research_kind_fails_job_store(monkeypatch):
    """R1 contract change (2026-08-17 prod incident): an unknown kind must
    FAIL the job-store row (so find_in_flight_job stops deduping against a
    zombie pending row) and return an error dict — not bare-raise, which
    left the row pending for ~30 min."""
    from app.backend.services import complacency_job_store as job_store

    failed: list = []
    monkeypatch.setattr(job_store, "fail_job",
                        lambda job_id, err: failed.append((job_id, err)))

    out = asyncio.run(worker.run_research_job_task({}, "job-1", "nope", {}))

    assert out["ok"] is False
    assert out["kind"] == "nope"
    assert "unknown" in out["error"]
    assert len(failed) == 1
    assert failed[0][0] == "job-1"
    assert "nope" in failed[0][1]


# ── R1.e drive sync task ──────────────────────────────────────────────────────

def test_drive_sync_task_noop_without_folder(monkeypatch):
    """Backward gate: with DRIVE_SYNC_FOLDER unset the cron entry is a
    no-op — no job row, no sync attempt."""
    monkeypatch.delenv("DRIVE_SYNC_FOLDER", raising=False)

    def _fail(*a, **kw):
        pytest.fail("sync must not run when DRIVE_SYNC_FOLDER is unset")

    monkeypatch.setattr("app.backend.services.drive_sync.sync_drive_folder",
                        _fail, raising=False)
    out = asyncio.run(worker.run_drive_sync_task({}))
    assert out == {"ran": False, "skipped": True}


def test_drive_sync_task_completes_job_store(monkeypatch):
    from app.backend.services import complacency_job_store as job_store

    monkeypatch.setenv("DRIVE_SYNC_FOLDER", "1sVyHVhQ9i-fOb2hwcovX3bMjYQHf6FX1")
    events = []
    monkeypatch.setattr(job_store, "create_job",
                        lambda kind, **kw: events.append(("create", kind)) or "job-ds")
    monkeypatch.setattr(job_store, "update_progress",
                        lambda job_id, status, msg: events.append(("progress", msg)))
    monkeypatch.setattr(job_store, "complete_job",
                        lambda job_id, payload: events.append(("complete", payload)))
    monkeypatch.setattr(job_store, "fail_job",
                        lambda job_id, err: events.append(("fail", err)))
    monkeypatch.setattr(
        "app.backend.services.drive_sync.sync_drive_folder",
        lambda *a, **kw: {"listed": 5, "matched": 5, "unmatched": [],
                          "downloaded": 0, "unchanged": 5, "extracted": 0,
                          "gated": 0, "errors": []})

    out = asyncio.run(worker.run_drive_sync_task({}))

    assert out["ran"] is True and out["job_id"] == "job-ds"
    assert out["result"]["listed"] == 5
    assert events[0] == ("create", "drive_sync")
    assert events[-1][0] == "complete"
    assert events[-1][1]["unchanged"] == 5


# ── analysis pipeline task ────────────────────────────────────────────────────

class _BusRecorder:
    def __init__(self):
        self.published = []   # (run_id, event)
        self.phases = {}      # (ticker, phase) -> trimmed event
        self.cleared = []

    async def publish_event(self, run_id, event):
        self.published.append((run_id, event))

    async def set_phase_event(self, ticker, phase, event):
        self.phases[(ticker, phase)] = event

    async def clear_ticker(self, ticker):
        self.cleared.append(ticker)


def _patch_bus(monkeypatch, rec):
    monkeypatch.setattr(worker.progress_bus, "publish_event", rec.publish_event)
    monkeypatch.setattr(worker.progress_bus, "set_phase_event", rec.set_phase_event)
    monkeypatch.setattr(worker.progress_bus, "clear_ticker", rec.clear_ticker)


def test_analysis_task_streams_progress_and_completes(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)

    async def fake_pipeline(ticker, model_name, api_keys, on_phase,
                            user_id=None, run_id=None):
        on_phase("macro_regime_classifier", "running", "Classifying regime",
                 "", ticker, "t0", None)
        on_phase("deep_research_agent", "Done", "Research complete",
                 "r", ticker, "t1", {"k": 1})
        await asyncio.sleep(0)
        return run_id, {"final_action": "BUY"}

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline",
        fake_pipeline)

    out = asyncio.run(worker.run_analysis_pipeline_task(
        {}, ticker="msft", model_name="m", api_keys={}, run_id="run-123"))

    assert out == {"run_id": "run-123", "ticker": "msft", "ok": True}
    phases = [e["phase"] for rid, e in rec.published if rid == "run-123"]
    assert phases == ["macro_regime_classifier", "deep_research_agent",
                      "pipeline_complete"]
    terminal = rec.published[-1][1]
    assert terminal["completed"] is True
    assert terminal["run_id"] == "run-123"
    # partial_data forwarded on the run channel...
    assert rec.published[1][1]["partial_data"] == {"k": 1}
    # ...but the per-ticker phase map stores the trimmed event
    assert ("MSFT", "deep_research_agent") in rec.phases
    assert "partial_data" not in rec.phases[("MSFT", "deep_research_agent")]


def test_analysis_task_publishes_error_event_and_raises(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)

    async def boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(worker.run_analysis_pipeline_task(
            {}, ticker="aapl", model_name="m", api_keys={}, run_id="run-err"))

    errs = [e for rid, e in rec.published if e["phase"] == "pipeline_error"]
    assert len(errs) == 1
    assert errs[0]["completed"] is True
    assert "kaboom" in errs[0]["summary"]
    assert ("AAPL", "pipeline_error") in rec.phases


def test_analysis_task_mints_run_id_when_absent(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    seen = {}

    async def fake_pipeline(**kwargs):
        seen["run_id"] = kwargs.get("run_id")
        return kwargs.get("run_id"), {}

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline",
        fake_pipeline)

    out = asyncio.run(worker.run_analysis_pipeline_task(
        {}, ticker="nvda", model_name="m", api_keys={}))

    assert out["ok"] is True
    assert out["run_id"] == seen["run_id"]
    assert len(out["run_id"]) == 36  # uuid4 string


# ── research job task ─────────────────────────────────────────────────────────

def test_research_dispatch_runs_runner_on_thread(monkeypatch):
    calls = []
    from app.backend.routes import research as R

    monkeypatch.setattr(
        R, "_execute_idea_gen_job",
        lambda job_id, mode: calls.append((job_id, mode)))

    out = asyncio.run(worker.run_research_job_task(
        {}, "job-9", "idea_of_the_day_gen", {"mode": "deep_value"}))

    assert out == {"job_id": "job-9", "kind": "idea_of_the_day_gen", "ok": True}
    assert calls == [("job-9", "deep_value")]


def test_hundred_q_refresh_success(monkeypatch):
    from app.backend.services import complacency_job_store as store

    events = []
    monkeypatch.setattr(store, "update_progress",
                        lambda job_id, status, msg: events.append(("progress", status)))
    monkeypatch.setattr(store, "complete_job",
                        lambda job_id, payload: events.append(("complete", payload)))
    monkeypatch.setattr(store, "fail_job",
                        lambda job_id, err: events.append(("fail", err)))

    class _Cohort:
        run_id = "hq-1"
        ticker_count = 30
        tier_counts = {"A": 5}
        failed_tickers = []

    import src.research_ideas.hundred_q.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_full_quant_batch", lambda *a: _Cohort())

    asyncio.run(worker._run_hundred_q_refresh("job-hq"))

    assert events[0] == ("progress", "running")
    assert events[1][0] == "complete"
    assert events[1][1]["run_id"] == "hq-1"
    assert events[1][1]["ticker_count"] == 30


# ── Phase 4 scheduled tasks ───────────────────────────────────────────────────

def _patch_screener(monkeypatch, cached_at):
    import app.backend.services.screener_service as ss

    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "_get_master_universe_cached_at", lambda: cached_at)


class _LockStubRedis:
    """Just enough of redis.asyncio for redis_locks.try_lock/unlock."""

    def __init__(self):
        self.store: dict = {}
        self.set_calls: list = []
        self.eval_calls: list = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls.append(keys_and_args)
        key, token = keys_and_args[0], keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


def _patch_lock_redis(monkeypatch, client):
    async def _get_redis():
        return client

    monkeypatch.setattr("app.backend.services.redis_client.get_redis", _get_redis)


def test_vgpm_backfill_task_skips_when_already_ran_today(monkeypatch):
    from datetime import datetime, timedelta, timezone

    # A cached_at after today's 09:00 UTC slot, whenever the suite runs.
    fresh = (
        datetime.now(timezone.utc).replace(
            hour=9, minute=0, second=0, microsecond=0)
        + timedelta(minutes=5)
    ).isoformat()
    _patch_screener(monkeypatch, fresh)

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: pytest.fail("backfill must not run when today's slot is filled"))

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out == {"ran": False}


def test_vgpm_backfill_task_runs_when_stale(monkeypatch):
    _patch_screener(monkeypatch, "2020-01-01T00:00:00+00:00")
    _patch_lock_redis(monkeypatch, _LockStubRedis())

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: {"scored": 42, "total": 50})

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out == {"ran": True, "scored": 42, "total": 50}


def test_vgpm_backfill_task_runs_when_never_backfilled(monkeypatch):
    _patch_screener(monkeypatch, None)
    _patch_lock_redis(monkeypatch, _LockStubRedis())

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: {"scored": 1, "total": 1})

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out["ran"] is True


def test_vgpm_backfill_task_skips_when_lock_held(monkeypatch):
    """Phase 5: a backfill already running on a web replica (admin trigger)
    must suppress the scheduled worker run."""
    _patch_screener(monkeypatch, "2020-01-01T00:00:00+00:00")  # stale → wants to run

    stub = _LockStubRedis()
    stub.store["lock:vgpm_backfill"] = "held-elsewhere"
    _patch_lock_redis(monkeypatch, stub)

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: pytest.fail("backfill must not run while the lock is held"))

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out == {"ran": False}


def test_vgpm_backfill_task_acquires_and_releases_shared_lock(monkeypatch):
    """The task must take the SAME lock the admin route uses (canonical
    name + TTL) and release it when done."""
    _patch_screener(monkeypatch, None)
    stub = _LockStubRedis()
    _patch_lock_redis(monkeypatch, stub)

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: {"scored": 1, "total": 1})

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out["ran"] is True

    acquire = stub.set_calls[0]
    assert acquire["key"] == "lock:vgpm_backfill"
    assert acquire["nx"] is True
    assert acquire["ex"] == 7200
    assert "lock:vgpm_backfill" not in stub.store      # released
    assert stub.eval_calls, "release must go through compare-and-delete"


def test_vgpm_backfill_task_releases_lock_on_failure(monkeypatch):
    _patch_screener(monkeypatch, None)
    stub = _LockStubRedis()
    _patch_lock_redis(monkeypatch, stub)

    import app.backend.services.screener_service as ss

    def _boom(**kw):
        raise RuntimeError("FMP down")

    monkeypatch.setattr(ss, "backfill_master_universe", _boom)

    with pytest.raises(RuntimeError):
        asyncio.run(worker.run_vgpm_backfill_task({}))
    assert "lock:vgpm_backfill" not in stub.store


def test_scheduled_tasks_delegate_to_cycle_functions(monkeypatch):
    """Each scheduled task must call the legacy cycle function (which keeps
    its own idempotency gate) — never a reimplemented copy of the work.

    R2: a falsy cycle outcome is classified via the SAME idempotency gate
    the scheduler's recheck loop uses, so the gates are stubbed True here
    to model a successful cycle (a real success writes its gate)."""
    calls = []

    monkeypatch.setattr(
        "src.research_ideas.contrarian.scheduler._generate_and_notify",
        lambda: calls.append("idea"))
    monkeypatch.setattr(
        "src.research_ideas.contrarian.scheduler._idea_already_generated_today",
        lambda: True)
    monkeypatch.setattr(
        "src.research_ideas.alerts.iv15_scheduler._run_sweep_cycle",
        lambda: calls.append("iv15"))
    monkeypatch.setattr(
        "src.research_ideas.alerts.iv15_scheduler._swept_today",
        lambda: True)
    monkeypatch.setattr(
        "src.research_ideas.fundflow.scheduler.run_weekly_cycle",
        lambda: calls.append("fundflow") or {"run_id": "ff-1"})
    monkeypatch.setattr(
        "src.research_ideas.hundred_q.scheduler.run_daily_sweep_cycle",
        lambda: calls.append("hq_daily") or [])
    monkeypatch.setattr(
        "src.research_ideas.hundred_q.scheduler.run_weekly_batch_cycle",
        lambda: calls.append("hq_weekly") or {"run_id": "hq-1"})
    monkeypatch.setattr(
        "src.research_ideas.hundred_q.scheduler.run_backstop_cycle",
        lambda: calls.append("hq_backstop") or [])

    assert asyncio.run(worker.run_idea_of_the_day_task({})) == {"ran": True}
    assert asyncio.run(worker.run_iv15_sweep_task({})) == {"ran": True}
    assert asyncio.run(worker.run_fundflow_brief_task({})) == {"ran": True}
    assert asyncio.run(worker.run_hundred_q_daily_sweep_task({})) == {"ran": True}
    assert asyncio.run(worker.run_hundred_q_weekly_batch_task({})) == {"ran": True}
    assert asyncio.run(worker.run_hundred_q_backstop_task({})) == {"ran": True}

    assert calls == ["idea", "iv15", "fundflow",
                     "hq_daily", "hq_weekly", "hq_backstop"]


def test_fundflow_task_reports_skip(monkeypatch):
    """run_weekly_cycle() returns None when its 6-day gate skips — the task
    must surface that as ran=False, not as a success."""
    monkeypatch.setattr(
        "src.research_ideas.fundflow.scheduler.run_weekly_cycle",
        lambda: None)
    assert asyncio.run(worker.run_fundflow_brief_task({})) == {"ran": False}


# ── R1: dedup slot release on every exit path ─────────────────────────────────

class _DeleteTrackerRedis:
    """Just enough of redis.asyncio for queue_client.release_run."""

    def __init__(self):
        self.deleted: list = []

    async def delete(self, key):
        self.deleted.append(key)
        return 1


def _patch_queue_redis(monkeypatch):
    tracker = _DeleteTrackerRedis()

    async def _get_redis():
        return tracker

    monkeypatch.setattr(
        "app.backend.services.queue_client.get_redis", _get_redis)
    return tracker


def test_analysis_task_releases_dedup_slot_on_success(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    tracker = _patch_queue_redis(monkeypatch)

    async def fake_pipeline(**kwargs):
        return kwargs.get("run_id"), {}

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline",
        fake_pipeline)

    out = asyncio.run(worker.run_analysis_pipeline_task(
        {}, ticker="msft", model_name="m", api_keys={},
        selected_agents=["b_agent", "a_agent"], run_id="run-9"))

    assert out["ok"] is True
    # Per-ticker key (M2 Track E) — the legacy selected_agents kwarg carried
    # by pre-deploy enqueued jobs is accepted but never forwarded, and the
    # released dedup slot is the ticker alone.
    assert tracker.deleted == ["analysis_dedup:msft"]


def test_analysis_task_releases_dedup_slot_on_failure(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    tracker = _patch_queue_redis(monkeypatch)

    async def boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "app.backend.services.analysis_service.run_analysis_pipeline", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(worker.run_analysis_pipeline_task(
            {}, ticker="aapl", model_name="m", api_keys={},
            run_id="run-err"))

    assert tracker.deleted == ["analysis_dedup:aapl"]
