"""Item 3 (owner, 2026-11-28): one self-tuning Qwen limiter shared by every process through Redis."""
import threading

import pytest

fakeredis = pytest.importorskip("fakeredis")
pytest.importorskip("lupa")

from src.llm import global_limiter as gl


@pytest.fixture
def r(monkeypatch):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server)
    monkeypatch.setitem(gl._CLIENT, "c", client)
    monkeypatch.setitem(gl._CLIENT, "down_until", 0.0)
    monkeypatch.setenv("GLOBAL_LLM_LIMITER", "on")
    monkeypatch.setenv("LLM_RATE_START", "2")
    monkeypatch.setenv("LLM_BURST_SECONDS", "2")
    monkeypatch.setenv("LLM_COOLDOWN_S", "0.2")
    return client, server


def _state(client, model="m"):
    return {k.decode(): float(v) for k, v in client.hgetall(f"llm:limiter:{model}").items()
            if k.decode() in ("rate", "req", "tok")}


def test_burst_then_waits(r, monkeypatch):
    monkeypatch.setattr(gl.time, "sleep", lambda s: None)
    waits = [gl.try_acquire("m", max_wait_s=0.0) for _ in range(6)]
    assert waits[:4] == [0.0] * 4          # burst = rate 2 x 2 s
    st = _state(r[0])
    assert st["rate"] == 2.0 and st["req"] < 1.0


def test_shared_across_processes(r):
    """Two clients on one server (= two processes / replicas) drain ONE bucket."""
    client, server = r
    import fakeredis
    other = fakeredis.FakeRedis(server=server)
    for _ in range(2):
        gl.try_acquire("m")
    gl._CLIENT["c"] = other
    for _ in range(2):
        gl.try_acquire("m")
    assert _state(other)["req"] < 1.0      # the second "process" saw the first one's spend


def test_429_halves_rate_and_pauses_everyone(r):
    client, _ = r
    gl.try_acquire("m")
    gl.feedback("m", 429)
    assert _state(client)["rate"] == 1.0
    assert client.pttl("llm:limiter:m:cooldown") > 0
    gl.feedback("m", 429)
    assert _state(client)["rate"] == 0.5    # floor LLM_RATE_MIN


def test_successes_raise_the_rate_up_to_the_cap(r, monkeypatch):
    client, _ = r
    monkeypatch.setenv("LLM_RATE_STEP_EVERY", "5")
    monkeypatch.setenv("LLM_RATE_MAX", "2.5")
    gl.try_acquire("m")
    for _ in range(5):
        gl.feedback("m", 200)
    assert _state(client)["rate"] == 2.25
    for _ in range(20):
        gl.feedback("m", 200)
    assert _state(client)["rate"] == 2.5


def test_tokens_charge_the_tpm_bucket(r):
    client, _ = r
    gl.try_acquire("qwen3.8-flash")
    before = _state(client, "qwen3.8-flash")["tok"]
    gl.feedback("qwen3.8-flash", 200, tokens=100_000)
    assert _state(client, "qwen3.8-flash")["tok"] == pytest.approx(before - 100_000)


def test_batch_keeps_a_reserve_for_customers(r, monkeypatch):
    monkeypatch.setattr(gl.time, "sleep", lambda s: None)
    tok = gl.PRIORITY.set("batch")
    try:
        got = [gl.try_acquire("m", max_wait_s=0.0) for _ in range(4)]
    finally:
        gl.PRIORITY.reset(tok)
    st = _state(r[0])
    assert st["req"] >= 0.3 * 4 - 1e-6     # batch stopped at the 30% reserve of a 4-token burst
    assert gl.try_acquire("m", max_wait_s=0.0) == 0.0    # an interactive caller still gets one


def test_no_redis_falls_back(monkeypatch):
    monkeypatch.setitem(gl._CLIENT, "c", None)
    monkeypatch.setitem(gl._CLIENT, "down_until", 1e18)
    assert gl.try_acquire("m") is None
    gl.feedback("m", 429)                   # no error
    monkeypatch.setenv("GLOBAL_LLM_LIMITER", "off")
    assert gl.try_acquire("m") is None


def test_throttle_uses_the_shared_limiter_and_falls_back(r, monkeypatch):
    from src.research_ideas.complacency import qwen_throttle as qt
    client, _ = r
    qt.acquire()
    assert _state(client, "qwen3.8-flash")["req"] < 4.0          # drew from the shared bucket
    calls = []
    monkeypatch.setattr(qt, "_local_acquire", lambda w=1.0, m=60.0: calls.append(w) or 0.0)
    monkeypatch.setitem(gl._CLIENT, "c", None)
    monkeypatch.setitem(gl._CLIENT, "down_until", 1e18)
    qt.acquire()
    assert calls == [1.0]                                         # Redis gone -> the old local bucket


def test_cost_meter_teaches_the_limiter(r):
    from src.llm import cost_meter as cm
    client, _ = r
    gl.try_acquire("qwen3.8-flash")
    before = _state(client, "qwen3.8-flash")["tok"]
    cm.record("qwen", "qwen3.8-flash", {"input": 1000, "output": 500, "cache_read": 2000})
    assert _state(client, "qwen3.8-flash")["tok"] == pytest.approx(before - 3500)
    cm.observe("qwen", "/compatible-mode/v1/chat/completions", b'{"model": "qwen3.8-flash"}', 429, b"")
    assert _state(client, "qwen3.8-flash")["rate"] == 1.0
    cm.record("anthropic", "claude-sonnet-4-6", {"input": 10})    # other providers never touch it
    assert client.exists("llm:limiter:claude-sonnet-4-6") == 0


def test_scheduled_jobs_run_at_batch_priority():
    import asyncio
    from app.backend import worker

    async def go(jid):
        await worker._on_job_start({"job_id": jid})
        return gl.PRIORITY.get()

    assert asyncio.run(go("sched:regional_comps_refresh:2026-11-28")) == "batch"
    assert asyncio.run(go("cron:run_drive_sync_task")) == "batch"
    assert asyncio.run(go("2c9fa921-5431-432d-8223-3d86bb4f05a2")) == "interactive"


def test_fmp_shares_one_fixed_budget(r, monkeypatch):
    client, server = r
    monkeypatch.setattr(gl.time, "sleep", lambda s: None)
    for _ in range(3):
        assert gl.try_acquire_fixed("fmp", 10.0, 3.0, max_wait_s=0.0) == 0.0
    st = {k.decode(): float(v) for k, v in client.hgetall("llm:limiter:fmp").items() if k.decode() in ("rate", "req")}
    assert st["rate"] == 10.0 and st["req"] < 1.0
    from src.tools import api
    monkeypatch.setattr(api, "_FMP_THROTTLE_DISABLED", False)
    api._fmp_acquire()                                            # goes through the shared bucket, no local wait
