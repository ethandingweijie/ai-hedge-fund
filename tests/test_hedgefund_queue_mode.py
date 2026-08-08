"""
tests/test_hedgefund_queue_mode.py
==================================
Phase 2f — /hedge-fund/run queue mode (Redis present → arq worker + bus SSE).

Runs without Redis or network: the graph stack is faked, the progress bus is
monkeypatched, and the tests assert (a) the worker task returns the parsed
final payload in the same shape as the in-process CompleteEvent data, and
(b) the web stream generator maps bus events + arq result store onto the
start/progress/complete/error SSE contract.
"""
import asyncio
import json

from app.backend import worker
from app.backend.models.schemas import HedgeFundRequest
from app.backend.routes import hedge_fund as HF
from app.backend.services import progress_bus, queue_client


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_sse(chunk: str):
    lines = chunk.strip().split("\n")
    name = lines[0].split(":", 1)[1].strip()
    data = json.loads(lines[1].split(":", 1)[1].strip())
    return name, data


def _drain(gen):
    async def go():
        return [c async for c in gen]
    return asyncio.run(go())


def _fake_iter_events(events):
    async def gen(run_id):
        for e in events:
            yield e
    return gen


class _FakeJob:
    """arq Job stand-in: result() returns a canned value or raises."""

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def result(self, timeout=None):
        if self._exc:
            raise self._exc
        return self._result


class _BusRecorder:
    def __init__(self):
        self.published = []

    async def publish_event(self, run_id, event):
        self.published.append((run_id, event))

    async def set_phase_event(self, ticker, phase, event):
        pass


def _patch_bus(monkeypatch, rec):
    monkeypatch.setattr(worker.progress_bus, "publish_event", rec.publish_event)
    monkeypatch.setattr(worker.progress_bus, "set_phase_event", rec.set_phase_event)


def _patch_graph_stack(monkeypatch, result=None, exc=None):
    """Fake the graph services the worker imports lazily."""
    import app.backend.services.graph as graph_mod
    import app.backend.services.portfolio as portfolio_mod

    class _Graph:
        def compile(self):
            return object()

    async def fake_run_graph_async(**kwargs):
        if exc:
            raise exc
        return result

    monkeypatch.setattr(graph_mod, "create_graph", lambda **kw: _Graph())
    monkeypatch.setattr(graph_mod, "run_graph_async", fake_run_graph_async)
    monkeypatch.setattr(portfolio_mod, "create_portfolio", lambda *a: object())


class _Msg:
    def __init__(self, content):
        self.content = content


_MIN_PAYLOAD = {
    "tickers": ["AAPL"],
    "graph_nodes": [],
    "graph_edges": [],
    "start_date": "2024-01-01",
    "end_date": "2024-03-31",
    "initial_cash": 100000.0,
}


# ── worker task ───────────────────────────────────────────────────────────────

def test_graph_task_returns_final_data(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    graph_result = {
        "messages": [_Msg('{"actions": [{"ticker": "AAPL", "action": "buy"}]}')],
        "data": {
            "analyst_signals": {"warren_buffett": {"signal": "bullish"}},
            "current_prices": {"AAPL": 200.5},
        },
    }
    _patch_graph_stack(monkeypatch, result=graph_result)

    out = asyncio.run(worker.run_hedge_fund_graph_task(
        {}, "run-hf1", None, _MIN_PAYLOAD))

    assert out["run_id"] == "run-hf1" and out["ok"] is True
    assert out["final_data"] == {
        "decisions": {"actions": [{"ticker": "AAPL", "action": "buy"}]},
        "analyst_signals": {"warren_buffett": {"signal": "bullish"}},
        "current_prices": {"AAPL": 200.5},
    }
    terminal = [e for rid, e in rec.published if e.get("completed")]
    assert len(terminal) == 1 and terminal[0]["phase"] == "graph_complete"


def test_graph_task_final_data_none_when_no_messages(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    _patch_graph_stack(monkeypatch, result={"messages": [], "data": {}})

    out = asyncio.run(worker.run_hedge_fund_graph_task(
        {}, "run-hf2", None, _MIN_PAYLOAD))

    # result dict is truthy, but with no messages there are no decisions —
    # final_data=None is the signal the web stream turns into the same
    # "Failed to generate hedge fund decisions" error the in-process path emits
    assert out["final_data"] is None


def test_graph_task_error_publishes_and_raises(monkeypatch):
    rec = _BusRecorder()
    _patch_bus(monkeypatch, rec)
    _patch_graph_stack(monkeypatch, exc=RuntimeError("graph blew up"))

    try:
        asyncio.run(worker.run_hedge_fund_graph_task(
            {}, "run-hf3", None, _MIN_PAYLOAD))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass

    errs = [e for rid, e in rec.published if e["phase"] == "graph_error"]
    assert len(errs) == 1
    assert errs[0]["completed"] is True
    assert "graph blew up" in errs[0]["summary"]


# ── queue_client ──────────────────────────────────────────────────────────────

def test_enqueue_hedge_fund_run_shape(monkeypatch):
    calls = []

    class _Pool:
        async def enqueue_job(self, name, _job_id=None, _queue_name=None,
                              _expires=None, **kwargs):
            calls.append({"name": name, "_job_id": _job_id, **kwargs})
            return _FakeJob(result={"final_data": {}})

    async def enabled():
        return True

    async def get_pool():
        return _Pool()

    monkeypatch.setattr(queue_client, "queue_mode_enabled", enabled)
    monkeypatch.setattr(queue_client, "get_arq_pool", get_pool)

    job = asyncio.run(queue_client.enqueue_hedge_fund_run("run-x", None, {"a": 1}))
    assert isinstance(job, _FakeJob)
    assert calls[0]["name"] == "run_hedge_fund_graph_task"
    assert calls[0]["_job_id"] == "hedgefund:run-x"
    assert calls[0]["run_id"] == "run-x"
    assert calls[0]["request_payload"] == {"a": 1}


# ── web: _start_queue_run ─────────────────────────────────────────────────────

def test_start_queue_run_serializes_payload(monkeypatch):
    recorded = {}

    async def fake_enqueue(run_id, user_id, request_payload):
        recorded.update(run_id=run_id, user_id=user_id, payload=request_payload)
        return _FakeJob(result={"final_data": {"decisions": {}}})

    monkeypatch.setattr(queue_client, "enqueue_hedge_fund_run", fake_enqueue)

    req = HedgeFundRequest(**_MIN_PAYLOAD)
    resp = asyncio.run(HF._start_queue_run(req))

    assert resp.media_type == "text/event-stream"
    assert len(recorded["run_id"]) == 16          # same format as in-process
    assert recorded["user_id"] is None
    assert recorded["payload"] == req.model_dump(mode="json")


# ── web: _queue_stream_generator ──────────────────────────────────────────────

def test_stream_maps_progress_and_complete(monkeypatch):
    final = {"decisions": {"actions": []}, "analyst_signals": {},
             "current_prices": {"AAPL": 1.0}}
    events = [
        {"phase": "agent_progress", "agent": "warren_buffett", "ticker": "AAPL",
         "status": "analyzing", "timestamp": "t0", "analysis": "text"},
        {"phase": "graph_complete", "status": "done", "summary": "ok",
         "run_id": "run-1", "completed": True},
    ]
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events(events))

    chunks = _drain(HF._queue_stream_generator("run-1", _FakeJob(result={"final_data": final})))

    name, start = _parse_sse(chunks[0])
    assert name == "start" and start["type"] == "start"

    name, prog = _parse_sse(chunks[1])
    assert name == "progress"
    assert prog["agent"] == "warren_buffett" and prog["ticker"] == "AAPL"
    assert prog["status"] == "analyzing" and prog["analysis"] == "text"

    name, done = _parse_sse(chunks[2])
    assert name == "complete" and done["data"] == final


def test_stream_graph_error(monkeypatch):
    events = [
        {"phase": "graph_error", "status": "error",
         "summary": "ValueError: bad graph", "completed": True},
    ]
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events(events))

    chunks = _drain(HF._queue_stream_generator("run-2", _FakeJob()))
    name, err = _parse_sse(chunks[-1])
    assert name == "error" and err["message"] == "ValueError: bad graph"


def test_stream_missing_final_data_matches_in_process_error(monkeypatch):
    events = [
        {"phase": "graph_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events(events))

    chunks = _drain(HF._queue_stream_generator(
        "run-3", _FakeJob(result={"final_data": None})))
    name, err = _parse_sse(chunks[-1])
    assert name == "error"
    assert err["message"] == "Failed to generate hedge fund decisions"


def test_stream_result_fetch_failure(monkeypatch):
    events = [
        {"phase": "graph_complete", "status": "done", "summary": "ok",
         "completed": True},
    ]
    monkeypatch.setattr(progress_bus, "iter_events", _fake_iter_events(events))

    chunks = _drain(HF._queue_stream_generator(
        "run-4", _FakeJob(exc=RuntimeError("result expired"))))
    name, err = _parse_sse(chunks[-1])
    assert name == "error"
    assert "result expired" in err["message"]
