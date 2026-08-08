"""
tests/test_analysis_queue_mode.py
=================================
Phase 2d — /analysis/run queue mode (Redis present → arq worker + bus SSE).

Runs without Redis or network: the queue_client helpers get a fake Redis,
the progress bus is monkeypatched, and the tests assert the route's queue
branch — claim/waiter logic, enqueue fallback, and the SSE contract emitted
from progress_bus events.

The in-process path (Redis absent) is untouched by Phase 2d and stays
covered by the existing suites.
"""
import asyncio
import json

from app.backend.routes import analysis as A
from app.backend.services import progress_bus, queue_client


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeRedis:
    """Minimal async redis stub covering set(nx)/get/delete."""

    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


def _patch_fake_redis(monkeypatch, fake=None):
    fake = fake or _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(queue_client, "get_redis", _get_redis)
    return fake


def _parse_sse(chunk: str):
    """('event: X\\ndata: {...}\\n\\n') -> (name, payload dict)."""
    lines = chunk.strip().split("\n")
    name = lines[0].split(":", 1)[1].strip()
    data = json.loads(lines[1].split(":", 1)[1].strip())
    return name, data


def _drain(gen):
    """Collect all chunks of an async generator."""
    async def go():
        return [c async for c in gen]
    return asyncio.run(go())


def _fake_iter_events(events):
    """Stand-in for progress_bus.iter_events yielding a fixed event list."""
    async def gen(run_id):
        for e in events:
            yield e
    return gen


# ── queue_client helpers ──────────────────────────────────────────────────────

def test_queue_mode_enabled_tracks_redis_ready(monkeypatch):
    async def ready():
        return False
    monkeypatch.setattr(queue_client, "redis_ready", ready)
    assert asyncio.run(queue_client.queue_mode_enabled()) is False


def test_claim_run_first_wins(monkeypatch):
    fake = _patch_fake_redis(monkeypatch)
    assert asyncio.run(queue_client.claim_run("MSFT::", "run-1")) is True
    assert asyncio.run(queue_client.claim_run("MSFT::", "run-2")) is False
    assert asyncio.run(queue_client.get_runner_run_id("MSFT::")) == "run-1"
    # Key namespaced with the shared prefix + TTL applied
    assert queue_client.DEDUP_PREFIX + "MSFT::" in fake.store


def test_release_run_clears_slot(monkeypatch):
    _patch_fake_redis(monkeypatch)
    asyncio.run(queue_client.claim_run("MSFT::", "run-1"))
    asyncio.run(queue_client.release_run("MSFT::"))
    assert asyncio.run(queue_client.get_runner_run_id("MSFT::")) is None


# ── _queue_stream_generator SSE contract ──────────────────────────────────────

def test_runner_stream_start_progress_complete(monkeypatch):
    events = [
        {"phase": "macro_regime_classifier", "status": "running", "summary": "s1"},
        {"phase": "pipeline_complete", "status": "done", "summary": "ok",
         "run_id": "run-9", "completed": True},
    ]
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events(events))

    chunks = _drain(A._queue_stream_generator(
        "run-9", "MSFT", "m", ["a1", "a2"], waiter=False))

    name, start = _parse_sse(chunks[0])
    assert name == "start"
    # 2 selected agents + 21 fixed phases
    assert start == {"ticker": "MSFT", "model": "m", "total_done_phases": 23}

    name, prog = _parse_sse(chunks[1])
    assert name == "progress"
    assert prog["phase"] == "macro_regime_classifier"

    name, done = _parse_sse(chunks[2])
    assert name == "complete"
    assert done == {"run_id": "run-9", "ticker": "MSFT"}


def test_runner_stream_defaults_to_12_investors(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([
        {"phase": "pipeline_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]))
    chunks = _drain(A._queue_stream_generator(
        "run-x", "AAPL", "m", None, waiter=False))
    _, start = _parse_sse(chunks[0])
    assert start["total_done_phases"] == 12 + 21


def test_runner_stream_error_event(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([
        {"phase": "pipeline_error", "status": "error",
         "summary": "RuntimeError: kaboom", "completed": True},
    ]))
    chunks = _drain(A._queue_stream_generator(
        "run-e", "TSLA", "m", None, waiter=False))
    name, err = _parse_sse(chunks[-1])
    assert name == "error"
    assert err == {"error": "RuntimeError: kaboom"}


def test_waiter_stream_hands_back_cached_run(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([
        {"phase": "pipeline_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]))
    monkeypatch.setattr(
        A.analysis_service, "get_cached_run",
        lambda ticker, within_minutes=None, agents=None:
            {"run_id": "runner-1", "ticker": ticker, "run_at": "2026-08-08T00:00:00"})

    chunks = _drain(A._queue_stream_generator(
        "runner-1", "NVDA", "m", None, waiter=True))

    name, start = _parse_sse(chunks[0])
    assert name == "start" and start["total_done_phases"] == 0
    name, queued = _parse_sse(chunks[1])
    assert name == "progress" and queued["phase"] == "pipeline_queued"
    name, cached = _parse_sse(chunks[2])
    assert name == "cached"
    assert cached["run_id"] == "runner-1" and cached["ticker"] == "NVDA"


def test_waiter_stream_without_cache_emits_complete(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([
        {"phase": "pipeline_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]))
    monkeypatch.setattr(
        A.analysis_service, "get_cached_run",
        lambda ticker, within_minutes=None, agents=None: None)

    chunks = _drain(A._queue_stream_generator(
        "runner-1", "NVDA", "m", None, waiter=True))
    name, done = _parse_sse(chunks[-1])
    assert name == "complete" and done["run_id"] == "runner-1"


# ── _start_queue_run: claim / waiter / fallback ──────────────────────────────

def test_start_queue_run_runner_path_enqueues(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([]))
    _patch_fake_redis(monkeypatch)  # real claim logic against the fake redis

    enqueued = []

    async def fake_enqueue(**kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(queue_client, "enqueue_analysis", fake_enqueue)

    resp = asyncio.run(A._start_queue_run("MSFT", "m", ["a1"], 1, {"k": "v"}))
    assert resp is not None
    assert resp.media_type == "text/event-stream"

    assert len(enqueued) == 1
    job = enqueued[0]
    assert job["ticker"] == "MSFT"
    assert job["selected_agents"] == ["a1"]
    assert job["user_id"] == 1
    assert job["api_keys"] == {"k": "v"}
    assert len(job["run_id"]) == 36

    # Runner stream announces the full phase total
    chunks = _drain(resp.body_iterator)
    _, start = _parse_sse(chunks[0])
    assert start["total_done_phases"] == 1 + 21


def test_start_queue_run_waiter_subscribes_to_runner(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([
        {"phase": "pipeline_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]))
    monkeypatch.setattr(
        A.analysis_service, "get_cached_run",
        lambda ticker, within_minutes=None, agents=None: None)

    async def claim_fails(key, run_id):
        return False

    async def runner_id(key):
        return "runner-abc"

    monkeypatch.setattr(queue_client, "claim_run", claim_fails)
    monkeypatch.setattr(queue_client, "get_runner_run_id", runner_id)

    resp = asyncio.run(A._start_queue_run("MSFT", "m", None, None, {}))
    chunks = _drain(resp.body_iterator)
    _, start = _parse_sse(chunks[0])
    assert start["total_done_phases"] == 0          # waiter flavour
    _, queued = _parse_sse(chunks[1])
    assert queued["phase"] == "pipeline_queued"
    name, _ = _parse_sse(chunks[-1])
    assert name == "complete"


def test_start_queue_run_reclaims_expired_slot(monkeypatch):
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events([]))

    calls = {"claim": 0}

    async def claim_second_wins(key, run_id):
        calls["claim"] += 1
        return calls["claim"] == 2  # first claim loses, retry wins

    async def no_runner(key):
        return None

    enqueued = []

    async def fake_enqueue(**kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(queue_client, "claim_run", claim_second_wins)
    monkeypatch.setattr(queue_client, "get_runner_run_id", no_runner)
    monkeypatch.setattr(queue_client, "enqueue_analysis", fake_enqueue)

    resp = asyncio.run(A._start_queue_run("MSFT", "m", None, None, {}))
    assert resp is not None
    assert calls["claim"] == 2
    assert len(enqueued) == 1


def test_start_queue_run_enqueue_failure_falls_back(monkeypatch):
    fake = _patch_fake_redis(monkeypatch)

    async def enqueue_boom(**kwargs):
        raise RuntimeError("arq pool down")

    monkeypatch.setattr(queue_client, "enqueue_analysis", enqueue_boom)

    resp = asyncio.run(A._start_queue_run("MSFT", "m", None, None, {}))
    assert resp is None  # caller falls through to the in-process path
    # ...and the dedup slot was released so the in-process run can proceed
    assert fake.store == {}


def test_start_queue_run_unreadable_slot_falls_back(monkeypatch):
    async def claim_fails(key, run_id):
        return False

    async def no_runner(key):
        return None

    monkeypatch.setattr(queue_client, "claim_run", claim_fails)
    monkeypatch.setattr(queue_client, "get_runner_run_id", no_runner)

    assert asyncio.run(A._start_queue_run("MSFT", "m", None, None, {})) is None


# ── GET /analysis/status/{ticker} dual mode ──────────────────────────────────

def test_status_prefers_bus_when_populated(monkeypatch):
    async def fake_map(t):
        assert t == "MSFT"
        return {
            "__latest__": {"phase": "p1", "status": "running",
                           "summary": "s", "timestamp": "t0"},
            "p1": {"phase": "p1", "status": "running",
                   "summary": "s", "timestamp": "t0"},
        }

    monkeypatch.setattr(progress_bus, "get_phase_map", fake_map)
    out = asyncio.run(A.get_pipeline_status(" msft "))
    assert out["ticker"] == "MSFT"
    assert out["in_progress"] is True
    assert out["phase"] == "p1"
    assert out["all_phases"] == {"p1": {"phase": "p1", "status": "running",
                                        "summary": "s", "timestamp": "t0"}}


def test_status_bus_completed_means_not_in_progress(monkeypatch):
    async def fake_map(t):
        return {"__latest__": {"phase": "pipeline_complete", "status": "done",
                               "summary": "ok", "completed": True}}

    monkeypatch.setattr(progress_bus, "get_phase_map", fake_map)
    out = asyncio.run(A.get_pipeline_status("MSFT"))
    assert out["in_progress"] is False
    assert out["phase"] == "pipeline_complete"


def test_status_falls_back_to_local_dicts(monkeypatch):
    async def empty_map(t):
        return {}

    monkeypatch.setattr(progress_bus, "get_phase_map", empty_map)
    monkeypatch.setattr(A, "_live_phases", {})
    monkeypatch.setattr(A, "_live_phase_maps", {})
    monkeypatch.setattr(A, "_in_flight", {})

    out = asyncio.run(A.get_pipeline_status("MSFT"))
    assert out == {"ticker": "MSFT", "in_progress": False, "phase": None,
                   "status": None, "summary": None, "timestamp": None,
                   "all_phases": {}}
