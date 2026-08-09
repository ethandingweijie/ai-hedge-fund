"""
tests/test_research_queue_mode.py
=================================
Phase 2e — /research/* queue mode (Redis present → arq worker).

Runs without Redis or network: queue_client is patched with fakes, the job
stores are stubbed, and the tests assert the dual-mode dispatch — enqueue
when Redis is up, _spawn_background fallback when it isn't, dedup contract
intact in both modes.
"""
import asyncio

import pytest

from app.backend.routes import research as R
from app.backend.services import queue_client


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Hermetic against Phase 3c: if a local Redis happens to be running,
    the real per-user limits would start rejecting these test triggers."""
    async def _allow(**kwargs):
        return None
    monkeypatch.setattr(
        "app.backend.services.rate_limiter.check_limits", _allow)


# ── fakes ─────────────────────────────────────────────────────────────────────

class _FakeJobStore:
    def __init__(self, in_flight=None):
        self.in_flight = in_flight
        self.created = []

    def find_in_flight_job(self, kind, ticker=None):
        return self.in_flight

    def create_job(self, kind, ticker=None, user_id=None):
        job_id = f"job-{len(self.created)}"
        self.created.append((kind, ticker, user_id))
        return job_id


class _FakePool:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, _job_id=None, _queue_name=None,
                          _expires=None, **kwargs):
        self.calls.append({"name": name, "_job_id": _job_id,
                           "_queue_name": _queue_name, **kwargs})


def _queue_on(monkeypatch, pool=None):
    """Enable queue mode and capture enqueues."""
    pool = pool or _FakePool()

    async def enabled():
        return True

    async def get_pool():
        return pool

    monkeypatch.setattr(queue_client, "queue_mode_enabled", enabled)
    monkeypatch.setattr(queue_client, "get_arq_pool", get_pool)
    return pool


def _queue_off(monkeypatch):
    async def enabled():
        return False
    monkeypatch.setattr(queue_client, "queue_mode_enabled", enabled)


def _capture_spawns(monkeypatch):
    """Replace _spawn_background so no real task/thread is started."""
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        return None

    monkeypatch.setattr(R, "_spawn_background", fake_spawn)
    return spawned


# ── queue_client.enqueue_research_job ─────────────────────────────────────────

def test_enqueue_research_job_shape(monkeypatch):
    pool = _queue_on(monkeypatch)
    asyncio.run(queue_client.enqueue_research_job(
        "job-7", "refresh", {"max_workers": 3}))

    assert len(pool.calls) == 1
    call = pool.calls[0]
    assert call["name"] == "run_research_job_task"
    assert call["_job_id"] == "research:job-7"
    assert call["_queue_name"] == "arq:queue"
    assert call["job_id"] == "job-7"
    assert call["kind"] == "refresh"
    assert call["params"] == {"max_workers": 3}


# ── _maybe_enqueue_research ───────────────────────────────────────────────────

def test_maybe_enqueue_returns_false_when_queue_off(monkeypatch):
    _queue_off(monkeypatch)
    assert asyncio.run(
        R._maybe_enqueue_research("j", "refresh", {})) is False


def test_maybe_enqueue_returns_true_when_enqueued(monkeypatch):
    pool = _queue_on(monkeypatch)
    assert asyncio.run(
        R._maybe_enqueue_research("j", "refresh", {"max_workers": 3})) is True
    assert pool.calls[0]["kind"] == "refresh"


def test_maybe_enqueue_falls_back_on_enqueue_error(monkeypatch):
    _queue_on(monkeypatch)

    async def boom(job_id, kind, params):
        raise RuntimeError("arq pool down")

    monkeypatch.setattr(queue_client, "enqueue_research_job", boom)
    assert asyncio.run(
        R._maybe_enqueue_research("j", "refresh", {})) is False


# ── route: idea-of-the-day ────────────────────────────────────────────────────

def test_idea_gen_route_enqueues_in_queue_mode(monkeypatch):
    pool = _queue_on(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.trigger_idea_generation(mode="deep_value", actor=None))

    assert out == {"job_id": "job-0", "status": "pending",
                   "started_at": None, "deduped": False, "mode": "deep_value"}
    assert len(pool.calls) == 1
    assert pool.calls[0]["kind"] == "idea_of_the_day_gen"
    assert pool.calls[0]["params"] == {"mode": "deep_value"}
    assert spawned == []  # nothing spawned in-process


def test_idea_gen_route_spawns_when_queue_off(monkeypatch):
    _queue_off(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.trigger_idea_generation(mode=None, actor=None))

    assert out["job_id"] == "job-0" and out["deduped"] is False
    assert len(spawned) == 1
    spawned[0].close()  # never awaited — close to avoid warnings


def test_idea_gen_route_dedup_contract_unchanged(monkeypatch):
    pool = _queue_on(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    existing = {"job_id": "job-live", "status": "running",
                "started_at": "t", "kind": "idea_of_the_day_gen"}
    monkeypatch.setattr(R, "job_store", _FakeJobStore(in_flight=existing))

    out = asyncio.run(R.trigger_idea_generation(actor=None))

    assert out["deduped"] is True and out["job_id"] == "job-live"
    assert pool.calls == [] and spawned == []


# ── route: complacency refresh ────────────────────────────────────────────────

def test_complacency_refresh_route_enqueues(monkeypatch):
    pool = _queue_on(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.refresh_complacency(max_workers=5, actor=None))

    assert out["job_id"] == "job-0" and out["deduped"] is False
    assert pool.calls[0]["kind"] == "refresh"
    assert pool.calls[0]["params"] == {"max_workers": 5}
    assert spawned == []


# ── route: ad-hoc score ───────────────────────────────────────────────────────

def test_score_adhoc_route_enqueues_with_ticker(monkeypatch):
    pool = _queue_on(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.score_complacency_adhoc(ticker="nvda", force_qual=True, actor=None))

    assert out["job_id"] == "job-0" and out["deduped"] is False
    assert pool.calls[0]["kind"] == "score_adhoc"
    assert pool.calls[0]["params"] == {"ticker": "NVDA", "force_qual": True}
    assert spawned == []


# ── route: hk50 qual (batch + single ticker) ─────────────────────────────────

def test_hk50_qual_route_enqueues(monkeypatch):
    pool = _queue_on(monkeypatch)
    _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.hk50_qual_deep_research(top_n=10, force_refresh=True, actor=None))

    assert out["deduped"] is False
    assert pool.calls[0]["kind"] == "hk50_qual"
    assert pool.calls[0]["params"] == {"top_n": 10, "force_refresh": True}


def test_hk50_qual_ticker_route_enqueues(monkeypatch):
    pool = _queue_on(monkeypatch)
    _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "job_store", _FakeJobStore())

    out = asyncio.run(R.hk50_qual_one_ticker(ticker="0700", force_refresh=False, actor=None))

    assert out["deduped"] is False
    assert pool.calls[0]["kind"] == "hk50_qual_ticker"
    assert pool.calls[0]["params"] == {"needle": "0700", "force_refresh": False}


# ── route: hundred-q refresh (to_thread store variant) ───────────────────────

def test_hundred_q_refresh_route_enqueues(monkeypatch):
    pool = _queue_on(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "hundred_q_job_store", _FakeJobStore())

    out = asyncio.run(R.refresh_hundred_q(actor=None))

    assert out == {"job_id": "job-0", "status": "pending"}
    assert pool.calls[0]["kind"] == "hundred_q_refresh"
    assert pool.calls[0]["params"] == {}
    assert spawned == []


def test_hundred_q_refresh_route_spawns_when_queue_off(monkeypatch):
    _queue_off(monkeypatch)
    spawned = _capture_spawns(monkeypatch)
    monkeypatch.setattr(R, "hundred_q_job_store", _FakeJobStore())

    out = asyncio.run(R.refresh_hundred_q(actor=None))

    assert out == {"job_id": "job-0", "status": "pending"}
    assert len(spawned) == 1
    spawned[0].close()
