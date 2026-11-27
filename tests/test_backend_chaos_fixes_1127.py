"""Backend reliability fixes from the 2026-11-27 chaos audit.

1. The 30-min run cache is scoped to (ticker, user, provider family) and runs
   off the event loop without parsing the full result blob.
2. Cancel is per run id; a cancel-then-resubmit cannot let both runs finish or
   let the cancelled run remove the new run's dedup slot; cancel checks
   ownership and only purges the caller's rows; progress_bus is imported.
3. /analysis/company/{t} runs yfinance off the loop with a timeout.
4. /analysis/run rejects malformed tickers with 422 before any work.
5. Portfolio holdings reject inf/nan/out-of-range numbers and bad tickers.
6. 500s carry a generic message + request id, never a traceback.

No network: the pipeline, yfinance, rate limiter and queue are monkeypatched.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.database import get_db
from app.backend.request_guards import is_valid_ticker_input
from app.backend.routes import analysis as A
from app.backend.services import analysis_service as svc
from app.backend.services import progress_bus, queue_client


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "run_archive_test.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    svc._ensure_web_runs_table()
    return db_path


def _insert(db_path, run_id, ticker, user_id, model, *, checkpoint=False, minutes_ago=1):
    from datetime import datetime, timedelta
    run_at = (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    payload = {"checkpoint": "phase3"} if checkpoint else {"data": {"x": 1}}
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, run_at, ticker, model, json.dumps(payload), 1 if checkpoint else 0, user_id),
    )
    conn.commit()
    conn.close()


def _parse_sse(chunk: str):
    name = data = None
    for line in chunk.strip().splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return name, data


@pytest.fixture
def analysis_client(monkeypatch):
    monkeypatch.setattr(A, "_get_user", lambda request, db: None)
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


# ══ 1. cache scoping ══════════════════════════════════════════════════════════

class TestCacheScope:
    def test_another_users_run_is_never_served(self, tmp_db):
        _insert(tmp_db, "r-u1", "MSFT", 1, "claude-sonnet-4-6")
        assert svc.get_cached_run("MSFT", 30, user_id=1, model_name="claude-sonnet-4-6")["run_id"] == "r-u1"
        assert svc.get_cached_run("MSFT", 30, user_id=2, model_name="claude-sonnet-4-6") is None
        assert svc.get_cached_run("MSFT", 30, user_id=None, model_name="claude-sonnet-4-6") is None

    def test_provider_family_is_part_of_the_key(self, tmp_db):
        _insert(tmp_db, "r-claude", "MSFT", 1, "claude-sonnet-4-6")
        assert svc.get_cached_run("MSFT", 30, user_id=1, model_name="qwen3.8-flash") is None
        _insert(tmp_db, "r-qwen", "MSFT", 1, "qwen3.8-flash")
        assert svc.get_cached_run("MSFT", 30, user_id=1, model_name="Qwen3.8-plus")["run_id"] == "r-qwen"
        assert svc.get_cached_run("MSFT", 30, user_id=1, model_name="claude-opus-4")["run_id"] == "r-claude"

    def test_anonymous_scope_and_checkpoints_and_window(self, tmp_db):
        _insert(tmp_db, "r-anon", "AAPL", None, "claude-sonnet-4-6")
        _insert(tmp_db, "r-ck", "AAPL", None, "claude-sonnet-4-6", checkpoint=True, minutes_ago=0)
        _insert(tmp_db, "r-old", "NVDA", None, "claude-sonnet-4-6", minutes_ago=90)
        hit = svc.get_cached_run("AAPL", 30, user_id=None, model_name="claude-sonnet-4-6")
        assert hit["run_id"] == "r-anon"
        # only identifying columns -- the result blob is no longer parsed
        assert set(hit) == {"run_id", "run_at", "ticker"}
        assert svc.get_cached_run("NVDA", 30) is None

    def test_route_passes_caller_and_model_and_runs_off_loop(self, monkeypatch):
        calls = []
        import threading
        main = threading.get_ident()

        def fake_cached(ticker, within_minutes=None, user_id=None, model_name=None):
            calls.append((ticker, within_minutes, user_id, model_name, threading.get_ident() != main))
            return {"run_id": "c-1", "ticker": ticker, "run_at": "2026-11-27T00:00:00"}

        monkeypatch.setattr(svc, "get_cached_run", fake_cached)
        monkeypatch.setattr(A, "_get_user", lambda request, db: SimpleNamespace(id=42))
        resp = asyncio.run(A.run_analysis({"ticker": "700.HK", "model": "qwen3.8-flash"},
                                          request=None, db=None))
        assert resp.media_type == "text/event-stream"
        assert calls == [("00700.HK", 30, 42, "qwen3.8-flash", True)]


# ══ 4. ticker validation ══════════════════════════════════════════════════════

GOOD = ["AAPL", "brk.b", "BRK-B", "0700.HK", "00700.HK", "2888", "D05.SI", "Z74.SI", " MSFT "]
BAD = ["AAPL;DROP", "../../ETC", "\x00AAPL", "A" * 50, "ＡＡＰＬ", "^GSPC", "AAPL HK",
       "AA..PL", "", "   ", "AAPL.HK.X", "<script>"]


@pytest.mark.parametrize("t", GOOD)
def test_valid_ticker_forms(t):
    assert is_valid_ticker_input(t)


@pytest.mark.parametrize("t", BAD + [123, None, ["AAPL"], {"t": 1}])
def test_invalid_ticker_forms(t):
    assert not is_valid_ticker_input(t)


@pytest.mark.parametrize("t", BAD + [123, None, ["AAPL"], {"x": 1}, 1.5, True])
def test_run_rejects_bad_ticker_with_422_before_any_work(analysis_client, monkeypatch, t):
    def boom(*a, **k):
        raise AssertionError("cache lookup reached with an invalid ticker")
    monkeypatch.setattr(svc, "get_cached_run", boom)
    r = analysis_client.post("/analysis/run", json={"ticker": t})
    assert r.status_code == 422, (t, r.status_code, r.text)


def test_run_rejects_bad_model_with_422(analysis_client, monkeypatch):
    monkeypatch.setattr(svc, "get_cached_run", lambda *a, **k: None)
    r = analysis_client.post("/analysis/run", json={"ticker": "AAPL", "model": "x; rm -rf /"})
    assert r.status_code == 422
    r = analysis_client.post("/analysis/run", json={"ticker": "AAPL", "model": 7})
    assert r.status_code == 422


@pytest.mark.parametrize("raw,canon", [("700.HK", "00700.HK"), ("00700.HK", "00700.HK"),
                                       ("2888", "02888.HK"), ("d05", "D05.SI"), ("brk.b", "BRK.B")])
def test_valid_tickers_keep_canonicalisation(analysis_client, monkeypatch, raw, canon):
    seen = []
    monkeypatch.setattr(svc, "get_cached_run",
                        lambda ticker, **k: seen.append(ticker) or
                        {"run_id": "c", "ticker": ticker, "run_at": "2026-11-27T00:00:00"})
    r = analysis_client.post("/analysis/run", json={"ticker": raw})
    assert r.status_code == 200
    assert seen == [canon]


# ══ 2. cancel ═════════════════════════════════════════════════════════════════

def _patch_run_env(monkeypatch, pipeline):
    async def no_limits(**k):
        return None

    async def no_queue():
        return False

    class _Keys:
        def __init__(self, db):
            pass

        def get_api_keys_dict(self, user_id):
            return {}

    from app.backend.services import rate_limiter
    monkeypatch.setattr(rate_limiter, "check_limits", no_limits)
    monkeypatch.setattr(queue_client, "queue_mode_enabled", no_queue)
    monkeypatch.setattr(A, "ApiKeyService", _Keys)
    monkeypatch.setattr(svc, "get_cached_run", lambda *a, **k: None)
    monkeypatch.setattr(svc, "run_analysis_pipeline", pipeline)
    monkeypatch.setattr(A, "_in_flight", {})
    monkeypatch.setattr(A, "_live_phases", {})
    monkeypatch.setattr(A, "_live_phase_maps", {})


def _patch_cancel_env(monkeypatch, purged):
    cleared = []

    async def fake_clear(t):
        cleared.append(t)

    async def no_runner(key):
        return None

    monkeypatch.setattr(progress_bus, "clear_ticker", fake_clear)
    monkeypatch.setattr(queue_client, "get_runner_run_id", no_runner)
    monkeypatch.setattr(svc, "purge_run_rows",
                        lambda t, run_id=None, user_id=None, all_users=False:
                        purged.append((t, run_id, user_id, all_users)) or 0)
    return cleared


def _req():
    return SimpleNamespace(headers={}, state=SimpleNamespace())


def test_cancel_then_immediate_resubmit_stops_only_the_first_run(monkeypatch):
    """Run A is cancelled, run B is submitted at once: A must stop, B must
    finish, and A's cleanup must not remove B's dedup slot."""
    monkeypatch.setenv("AUTH_ENFORCED", "false")
    release_b = None
    outcomes = {}

    async def fake_pipeline(ticker, model_name, api_keys, on_phase, user_id=None, run_id=None):
        on_phase("dcf_engine", "running", "working", ticker=ticker)
        for _ in range(400):
            if svc.is_cancelled(run_id):
                outcomes[run_id] = "cancelled"
                raise svc.RunCancelled(f"{ticker} cancelled by user")
            if outcomes.get(run_id) == "release" or (release_b is not None and release_b.is_set()
                                                       and run_id == outcomes.get("B")):
                break
            await asyncio.sleep(0.01)
        outcomes[run_id] = "completed"
        return run_id, {}

    _patch_run_env(monkeypatch, fake_pipeline)
    purged = []
    _patch_cancel_env(monkeypatch, purged)

    async def drain(resp):
        return [c async for c in resp.body_iterator]

    async def main():
        nonlocal release_b
        release_b = asyncio.Event()
        resp_a = await A.run_analysis({"ticker": "TSLA"}, request=_req(), db=None)
        slot_a = A._in_flight["TSLA"]
        task_a = asyncio.create_task(drain(resp_a))
        await asyncio.sleep(0.05)

        out = await A.cancel_analysis("TSLA", request=_req(), db=None)
        assert out["run_id"] == slot_a.run_id
        # resubmit IMMEDIATELY, before A has unwound
        resp_b = await A.run_analysis({"ticker": "TSLA"}, request=_req(), db=None)
        slot_b = A._in_flight["TSLA"]
        assert slot_b is not slot_a
        outcomes["B"] = slot_b.run_id
        task_b = asyncio.create_task(drain(resp_b))

        chunks_a = await task_a
        # A's finally ran -- B's slot must survive it
        assert A._in_flight.get("TSLA") is slot_b
        assert not svc.is_cancelled(slot_b.run_id)
        release_b.set()
        chunks_b = await task_b
        return slot_a, slot_b, chunks_a, chunks_b

    slot_a, slot_b, chunks_a, chunks_b = asyncio.run(main())
    assert outcomes[slot_a.run_id] == "cancelled"
    assert outcomes[slot_b.run_id] == "completed"
    assert _parse_sse(chunks_a[-1])[0] == "error"
    name, done = _parse_sse(chunks_b[-1])
    assert name == "complete" and done["run_id"] == slot_b.run_id
    assert "TSLA" not in A._in_flight
    # the purge targeted run A's row specifically
    assert ("TSLA", slot_a.run_id, None, False) in purged


def test_cancel_by_another_user_is_refused(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCED", "true")
    purged = []
    cleared = _patch_cancel_env(monkeypatch, purged)
    slot = A._InFlightSlot(run_id="run-owner-1", user_id=1, model_name="claude-sonnet-4-6")
    monkeypatch.setattr(A, "_in_flight", {"MSFT": slot})
    monkeypatch.setattr(A, "_get_user", lambda request, db: SimpleNamespace(id=2))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.cancel_analysis("MSFT", request=_req(), db=None))
    assert ei.value.status_code == 403
    assert not svc.is_cancelled("run-owner-1")
    assert A._in_flight["MSFT"] is slot
    assert purged == [] and cleared == []


def test_cancel_unauthenticated_is_refused_when_auth_enforced(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCED", "true")
    purged = []
    _patch_cancel_env(monkeypatch, purged)
    monkeypatch.setattr(A, "_in_flight", {})
    monkeypatch.setattr(A, "_get_user", lambda request, db: None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.cancel_analysis("MSFT", request=_req(), db=None))
    assert ei.value.status_code == 401
    assert purged == []


def test_owner_cancel_flags_run_clears_live_state_and_scopes_purge(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCED", "true")
    purged = []
    cleared = _patch_cancel_env(monkeypatch, purged)
    slot = A._InFlightSlot(run_id="run-own-9", user_id=9)
    monkeypatch.setattr(A, "_in_flight", {"02888.HK": slot})
    monkeypatch.setattr(A, "_live_phases", {"02888.HK": {"phase": "x"}})
    monkeypatch.setattr(A, "_live_phase_maps", {"02888.HK": {"x": {}}})
    monkeypatch.setattr(A, "_get_user", lambda request, db: SimpleNamespace(id=9))
    try:
        out = asyncio.run(A.cancel_analysis("2888", request=_req(), db=None))
        assert out["run_id"] == "run-own-9" and out["slot_released"] is True
        assert svc.is_cancelled("run-own-9")
        assert "02888.HK" not in A._in_flight
        assert slot.event.is_set()
        # progress_bus is importable here now (was a NameError on every cancel)
        assert cleared == ["02888.HK"]
        assert "02888.HK" not in A._live_phases
        assert "02888.HK" not in A._live_phase_maps
        assert ("02888.HK", "run-own-9", None, False) in purged
        assert ("02888.HK", None, 9, False) in purged       # own rows only
    finally:
        svc.clear_cancel("run-own-9")


def test_cancel_with_bad_ticker_is_422(monkeypatch):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(A.cancel_analysis("../../ETC", request=_req(), db=None))
    assert ei.value.status_code == 422


def test_purge_run_rows_only_touches_the_callers_checkpoints(tmp_db):
    _insert(tmp_db, "ck-u1", "MSFT", 1, "m", checkpoint=True)
    _insert(tmp_db, "ck-u2", "MSFT", 2, "m", checkpoint=True)
    _insert(tmp_db, "ck-anon", "MSFT", None, "m", checkpoint=True)
    _insert(tmp_db, "final-u1", "MSFT", 1, "m")
    assert svc.purge_run_rows("MSFT", user_id=1) == 2        # own + legacy NULL
    conn = sqlite3.connect(tmp_db)
    left = {r[0] for r in conn.execute("SELECT run_id FROM web_runs")}
    conn.close()
    assert left == {"ck-u2", "final-u1"}
    assert svc.purge_run_rows("MSFT", run_id="ck-u2") == 1
    assert svc.purge_run_rows("MSFT", run_id="final-u1") == 0   # never a final row


# ══ 3. /analysis/company off the loop ═════════════════════════════════════════

def _fake_yf(monkeypatch, delay):
    import yfinance

    class _T:
        def __init__(self, sym):
            self.sym = sym

        @property
        def info(self):
            time.sleep(delay)
            return {"longName": "Zeta Corp", "sector": "Tech", "industry": "Software"}

    monkeypatch.setattr(yfinance, "Ticker", _T)


def test_company_info_does_not_block_the_event_loop(monkeypatch):
    _fake_yf(monkeypatch, delay=0.4)

    async def main():
        ticks = 0

        async def ticker_task():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        tt = asyncio.create_task(ticker_task())
        t0 = time.perf_counter()
        results = await asyncio.gather(*[A.get_company_info("ZETA") for _ in range(8)])
        elapsed = time.perf_counter() - t0
        tt.cancel()
        return results, elapsed, ticks

    results, elapsed, ticks = asyncio.run(main())
    assert all(r["name"] == "Zeta Corp" for r in results)
    assert elapsed < 2.0            # concurrent, not 8 x 0.4 s serialised on the loop
    assert ticks >= 5               # the loop kept running while yfinance blocked


def test_company_info_times_out_to_fallback(monkeypatch):
    _fake_yf(monkeypatch, delay=1.5)
    monkeypatch.setattr(A, "_COMPANY_INFO_TIMEOUT_S", 0.1)
    async def main():
        # Timed inside the loop: asyncio.run() itself waits for the abandoned
        # worker thread when it shuts the default executor down.
        t0 = time.perf_counter()
        out = await A.get_company_info("ZETA")
        return out, time.perf_counter() - t0

    out, elapsed = asyncio.run(main())
    assert elapsed < 1.0
    assert out["name"] == "ZETA" and out["sector"] is None


# ══ 5. portfolio holdings bounds ══════════════════════════════════════════════

@pytest.fixture
def portfolio_client(monkeypatch):
    from app.backend.database.connection import get_db as pf_get_db
    from app.backend.routes import portfolio as P
    from app.backend.services import portfolio_service as ps
    saved = []
    monkeypatch.setattr(ps, "upsert_holding",
                        lambda db, uid, t, q, c, o, n: saved.append((t, q, c)) or
                        SimpleNamespace(id=1, ticker=t, quantity=q, avg_cost=c, opened_at=None,
                                        notes=n, created_at=None))
    app = FastAPI()
    app.include_router(P.router, prefix="/portfolio")
    app.dependency_overrides[pf_get_db] = lambda: None
    c = TestClient(app, raise_server_exceptions=False)
    c.saved = saved
    return c


@pytest.mark.parametrize("body", [
    '{"ticker": "AAPL", "quantity": 1e309, "avg_cost": 10}',
    '{"ticker": "AAPL", "quantity": 10, "avg_cost": 1e309}',
    '{"ticker": "AAPL", "quantity": NaN, "avg_cost": 10}',
    '{"ticker": "AAPL", "quantity": Infinity, "avg_cost": 10}',
    '{"ticker": "AAPL", "quantity": 1e13, "avg_cost": 10}',
    '{"ticker": "AAPL", "quantity": 0, "avg_cost": 10}',
    '{"ticker": "AAPL;DROP", "quantity": 1, "avg_cost": 10}',
    '{"ticker": "' + "A" * 40 + '", "quantity": 1, "avg_cost": 10}',
    '{"ticker": 123, "quantity": 1, "avg_cost": 10}',
])
def test_holding_rejects_bad_values(portfolio_client, body):
    r = portfolio_client.post("/portfolio/holdings", content=body,
                              headers={"Content-Type": "application/json"})
    assert r.status_code == 422, (body, r.status_code, r.text)
    assert portfolio_client.saved == []


def test_holding_update_rejects_inf(portfolio_client):
    r = portfolio_client.put("/portfolio/holdings/1", content='{"quantity": 1e309}',
                             headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_holding_accepts_normal_values(portfolio_client):
    r = portfolio_client.post("/portfolio/holdings",
                              json={"ticker": "0700.HK", "quantity": 100, "avg_cost": 320.5})
    assert r.status_code == 200
    assert portfolio_client.saved == [("0700.HK", 100.0, 320.5)]


def test_dashboard_skips_a_poisoned_legacy_row(monkeypatch):
    from app.backend.services import portfolio_service as ps
    bad = SimpleNamespace(id=1, ticker="AAPL", quantity=float("inf"), avg_cost=10.0,
                          opened_at=None, notes=None, created_at=None)
    monkeypatch.setattr(ps, "list_holdings", lambda db, uid: [bad])
    out = ps.get_dashboard(None, None)
    assert out == ps.build_dashboard([], {}, {})


# ══ 6. no tracebacks in 500s ══════════════════════════════════════════════════

def _assert_generic_500(r):
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "Traceback" not in detail and "secret-path" not in detail
    assert "request id" in detail
    rid = r.headers.get("x-request-id")
    assert rid and rid in detail


def test_screener_500_is_generic(monkeypatch):
    from app.backend.routes import screener as S
    from app.backend.services import screener_service

    def boom(**k):
        raise RuntimeError("db at /secret-path exploded")

    monkeypatch.setattr(screener_service, "get_screener_stocks", boom)
    app = FastAPI()
    app.include_router(S.router, prefix="/screener")
    r = TestClient(app, raise_server_exceptions=False).get("/screener/stocks")
    _assert_generic_500(r)


@pytest.mark.parametrize("path,attr", [("/analysis/runs", "get_history"),
                                       ("/analysis/runs/r1", "get_run_result"),
                                       ("/analysis/summary", "get_archive_summary")])
def test_analysis_500s_are_generic(analysis_client, monkeypatch, path, attr):
    def boom(*a, **k):
        raise RuntimeError("secret-path leaked")
    monkeypatch.setattr(svc, attr, boom)
    _assert_generic_500(analysis_client.get(path))


def test_portfolio_500_is_generic(portfolio_client, monkeypatch):
    from app.backend.services import portfolio_service as ps

    def boom(db, uid):
        raise RuntimeError("secret-path leaked")
    monkeypatch.setattr(ps, "get_dashboard", boom)
    _assert_generic_500(portfolio_client.get("/portfolio"))
