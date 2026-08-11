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
    }
    assert ws.max_jobs == 10
    assert ws.job_timeout == 3600       # 60 min — VGPM backfill can exceed 30
    assert ws.retry_jobs is False
    assert ws.queue_name == "arq:queue"


def test_research_kinds_cover_spawn_points():
    assert worker.RESEARCH_KINDS == {
        "idea_of_the_day_gen", "hk50_qual", "hk50_qual_ticker",
        "refresh", "score_adhoc", "hundred_q_refresh",
    }


def test_unknown_research_kind_raises():
    with pytest.raises(ValueError, match="unknown research job kind"):
        asyncio.run(worker.run_research_job_task({}, "job-1", "nope", {}))


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
                            selected_agents=None, user_id=None, run_id=None):
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

class _FakeConn:
    def __init__(self, cached_at):
        self._cached_at = cached_at

    def execute(self, sql):
        conn = self

        class _Row:
            def fetchone(self):
                return (conn._cached_at,) if conn._cached_at is not None else None

        return _Row()

    def close(self):
        pass


def _patch_screener(monkeypatch, cached_at):
    import app.backend.services.screener_service as ss

    monkeypatch.setattr(ss, "_ensure_tables", lambda: None)
    monkeypatch.setattr(ss, "_connect", lambda: _FakeConn(cached_at))


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

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: {"scored": 42, "total": 50})

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out == {"ran": True, "scored": 42, "total": 50}


def test_vgpm_backfill_task_runs_when_never_backfilled(monkeypatch):
    _patch_screener(monkeypatch, None)

    import app.backend.services.screener_service as ss
    monkeypatch.setattr(
        ss, "backfill_master_universe",
        lambda **kw: {"scored": 1, "total": 1})

    out = asyncio.run(worker.run_vgpm_backfill_task({}))
    assert out["ran"] is True


def test_scheduled_tasks_delegate_to_cycle_functions(monkeypatch):
    """Each scheduled task must call the legacy cycle function (which keeps
    its own idempotency gate) — never a reimplemented copy of the work."""
    calls = []

    monkeypatch.setattr(
        "src.research_ideas.contrarian.scheduler._generate_and_notify",
        lambda: calls.append("idea"))
    monkeypatch.setattr(
        "src.research_ideas.alerts.iv15_scheduler._run_sweep_cycle",
        lambda: calls.append("iv15"))
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
