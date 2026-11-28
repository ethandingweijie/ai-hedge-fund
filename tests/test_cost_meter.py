"""Per-run LLM cost metering (pricing 2026-11-28)."""
import json

import httpx
import pytest

from src.llm import cost_meter as cm


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setitem(cm._READY, "v", False)
    yield _db
    _db.close_all_connections()


@pytest.fixture
def hooked():
    cm.uninstall()
    assert cm.install(force=True)
    yield
    cm.uninstall()


ANTHROPIC_JSON = {"model": "claude-sonnet-4-6", "usage": {
    "input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 300,
    "server_tool_use": {"web_search_requests": 3}}}


# ── extraction ──────────────────────────────────────────────────────────────

def test_anthropic_json():
    model, u = cm.parse_json_body(json.dumps(ANTHROPIC_JSON).encode())
    assert model == "claude-sonnet-4-6"
    assert u == {"input": 1000, "output": 200, "cache_read": 5000, "cache_write": 300, "searches": 3}


def test_openai_chat_json_splits_cached_tokens_out_of_input():
    body = {"model": "qwen3.8-flash", "usage": {"prompt_tokens": 900, "completion_tokens": 50,
                                                "prompt_tokens_details": {"cached_tokens": 400}}}
    _, u = cm.parse_json_body(json.dumps(body).encode())
    assert (u["input"], u["cache_read"], u["output"]) == (500, 400, 50)


def test_responses_api_counts_web_search_calls():
    body = {"model": "qwen3.8-flash", "output": [{"type": "web_search_call"}, {"type": "web_search_call"},
                                                {"type": "message"}],
            "usage": {"input_tokens": 3000, "output_tokens": 700, "input_tokens_details": {"cached_tokens": 0}}}
    _, u = cm.parse_json_body(json.dumps(body).encode())
    assert u["searches"] == 2 and u["input"] == 3000


def test_gemini_usage_metadata():
    body = {"modelVersion": "gemini-3.8-flash", "usageMetadata": {
        "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 30}}
    model, u = cm.parse_json_body(json.dumps(body).encode())
    assert model == "gemini-3.8-flash" and u["output"] == 50


def test_anthropic_sse_takes_input_from_start_and_cumulative_output_from_delta():
    lines = [
        'data: {"type":"message_start","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":1200,'
        '"output_tokens":1,"cache_read_input_tokens":0}}}',
        'data: {"type":"message_delta","usage":{"output_tokens":340,"server_tool_use":{"web_search_requests":2}}}',
    ]
    model, u = cm.parse_sse_lines(lines)
    assert model == "claude-sonnet-4-6" and (u["input"], u["output"], u["searches"]) == (1200, 340, 2)


def test_no_usage_is_reported_as_such():
    assert cm.parse_json_body(b'{"id": "x"}') == (None, None)
    assert cm.parse_sse_lines(["data: [DONE]"]) == (None, None)


# ── attribution ─────────────────────────────────────────────────────────────

def _client(body: dict, sse: bool = False):
    def handler(request):
        if sse:
            text = "".join(f"data: {json.dumps(e)}\n\n" for e in body)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_httpx_hook_attributes_to_the_run_and_ignores_other_hosts(hooked, store):
    with cm.metered_run("r1", ticker="AAPL", run_type="new", model_name="claude-sonnet-4-6") as m:
        _client(ANTHROPIC_JSON).post("https://api.anthropic.com/v1/messages", json={"model": "claude-sonnet-4-6"})
        _client({"ok": 1}).get("https://financialmodelingprep.com/api/v3/quote/AAPL")
    assert m.totals()["calls"] == 1 and m.totals()["searches"] == 3
    row = dict(store.query_one("SELECT * FROM run_costs WHERE run_id = ?", ["r1"]))
    assert row["status"] == "ok" and row["input_tokens"] == 1000 and row["run_type"] == "new"


def test_streamed_response_is_teed_not_consumed(hooked, store):
    events = [{"type": "message_start", "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 10}}},
              {"type": "content_block_delta", "delta": {"text": "hi"}},
              {"type": "message_delta", "usage": {"output_tokens": 7}}]
    with cm.metered_run("r2") as m:
        c = _client(events, sse=True)
        with c.stream("POST", "https://api.anthropic.com/v1/messages", json={"model": "claude-sonnet-4-6"}) as r:
            text = b"".join(r.iter_bytes()).decode()
    assert '"text": "hi"' in text                                     # the caller still gets the whole stream
    t = m.totals()
    assert (t["input"], t["output"]) == (10, 7)


def test_bind_carries_the_meter_into_a_bare_executor(hooked, store):
    from concurrent.futures import ThreadPoolExecutor

    def call():
        _client(ANTHROPIC_JSON).post("https://api.anthropic.com/v1/messages", json={})

    with cm.metered_run("r3") as m:
        with ThreadPoolExecutor(1) as ex:
            ex.submit(call).result()                                  # bare: lost to the run
            ex.submit(cm.bind(call)).result()                         # bound: counted
    assert m.totals()["calls"] == 1


def test_run_config_submit_carries_the_meter(hooked, store):
    from concurrent.futures import ThreadPoolExecutor
    from src.utils import run_config
    with cm.metered_run("r4") as m:
        with ThreadPoolExecutor(1) as ex:
            run_config.submit(ex, lambda: _client(ANTHROPIC_JSON).post("https://api.anthropic.com/v1/messages",
                                                                       json={})).result()
    assert m.totals()["calls"] == 1


def test_a_failed_run_is_saved_with_its_status(store):
    with pytest.raises(RuntimeError):
        with cm.metered_run("r5"):
            raise RuntimeError("boom")
    assert store.query_one("SELECT status FROM run_costs WHERE run_id = ?", ["r5"])[0] == "error"


def test_hook_failures_never_break_the_request(hooked, monkeypatch):
    monkeypatch.setattr(cm, "parse_json_body", lambda b: 1 / 0)
    r = _client(ANTHROPIC_JSON).post("https://api.anthropic.com/v1/messages", json={})
    assert r.status_code == 200


def test_unattributed_calls_flush_to_the_daily_table(hooked, store):
    cm._UNATTRIBUTED.by_model.clear()
    _client(ANTHROPIC_JSON).post("https://api.anthropic.com/v1/messages", json={})
    assert cm.flush_unattributed(force=True) == 1
    row = dict(store.query_one("SELECT * FROM llm_spend_daily"))
    assert row["model"] == "anthropic:claude-sonnet-4-6" and row["searches"] == 3


# ── pricing + report ────────────────────────────────────────────────────────

def test_cost_of_prices_tokens_and_searches_and_flags_unknown_models():
    c = cm.cost_of({"anthropic:claude-sonnet-4-6": {"calls": 1, "input": 1_000_000, "output": 100_000,
                                                    "cache_read": 0, "cache_write": 0, "searches": 10},
                    "qwen:qwen9-mystery": {"calls": 1, "input": 5, "output": 5}})
    assert c["tokens_usd"] == pytest.approx(3.0 + 1.5) and c["search_usd"] == pytest.approx(0.10)
    assert c["unpriced"] == ["qwen:qwen9-mystery"]


def test_report_compares_measured_cost_per_credit_with_the_plans():
    def row(t, inp, out):
        return {"status": "ok", "run_type": t, "breakdown_json": json.dumps(
            {"qwen:qwen3.8-flash": {"calls": 10, "calls_no_usage": 0, "input": inp, "output": out,
                                    "cache_read": 0, "cache_write": 0, "searches": 1}})}
    rows = [row("covered", 200_000, 20_000)] * 4 + [row("new", 600_000, 60_000)] * 2
    r = cm.cost_report(rows=rows, daily=[])
    cov = r["by_run_type"]["covered"]
    assert cov["runs"] == 4 and cov["median_usd"] == pytest.approx(0.2 * 0.15 + 0.02 * 0.47 + 0.01, abs=1e-4)
    assert r["plans_at_full_use"]["starter"]["worst_case_margin"] > 0.9
    assert r["usage_coverage"] == 1.0


class _Chunks(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A live stream (no preloaded body), split mid-line to test reassembly."""

    def __init__(self, data: bytes, size: int = 17):
        self.parts = [data[i:i + size] for i in range(0, len(data), size)]

    def __iter__(self):
        yield from self.parts

    async def __aiter__(self):
        for p in self.parts:
            yield p


SSE = ('data: {"type":"message_start","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":1200}}}\n\n'
       'data: {"type":"content_block_delta","delta":{"text":"hello"}}\n\n'
       'data: {"type":"message_delta","usage":{"output_tokens":340,"server_tool_use":{"web_search_requests":2}}}\n\n'
       ).encode()


def test_live_sync_stream_is_teed(hooked, store):
    c = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_Chunks(SSE))))
    with cm.metered_run("s1") as m:
        with c.stream("POST", "https://api.anthropic.com/v1/messages", json={}) as r:
            got = b"".join(r.iter_bytes())
    assert got == SSE
    t = m.totals()
    assert (t["calls"], t["input"], t["output"], t["searches"]) == (1, 1200, 340, 2)


def test_live_async_stream_is_teed(hooked, store):
    import asyncio

    async def go():
        c = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_Chunks(SSE))))
        async with c.stream("POST", "https://dashscope-intl.aliyuncs.com/apps/anthropic/v1/messages", json={}) as r:
            return b"".join([b async for b in r.aiter_bytes()])

    with cm.metered_run("s2") as m:
        assert asyncio.run(go()) == SSE
    assert m.snapshot()["qwen:claude-sonnet-4-6"]["output"] == 340
