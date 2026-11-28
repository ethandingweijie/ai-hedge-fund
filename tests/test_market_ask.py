"""Market-development questions (owner, 2026-11-28): answer, verify by search, update + supersede an analysis."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.backend.services import market_ask as ma
from src.memory import user_development as ud

NOW = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "ask.db"))
    from src.data import db
    from app.backend.services import billing
    monkeypatch.setattr(db, "get_db_path", lambda: str(tmp_path / "ask.db"))
    db.close_all_connections()
    monkeypatch.setitem(billing._READY, "v", False)
    monkeypatch.setenv("MARKET_ASK_UPDATES", "on")
    ma._READY["v"] = False
    yield
    db.close_all_connections()
    ma._READY["v"] = False


def _answer(verified=True, date="2026-11-26", sources=2, tickers=("AMAT", "NVDA")):
    srcs = [{"title": f"t{i}", "url": f"https://www.outlet{i}.com/a", "date": date, "fact": "f"}
            for i in range(sources)]
    return json.dumps({
        "answer": "The US widened chip export curbs.",
        "development": {"headline": "US widens chip-equipment export curbs to China", "date": date,
                        "summary": "New rule covers etch and deposition tools."},
        "corroborated": verified, "sources": srcs,
        "tickers": [{"symbol": t, "name": t + " Inc", "why": "China revenue"} for t in tickers]})


def _ask(user=None, raw=None, **kw):
    user = user or SimpleNamespace(id=7, role="member")
    return ma.ask(user, "What do the new US chip export curbs mean for AMAT?", kw.pop("hint", None),
                  search=lambda q, today, hint: raw if raw is not None else _answer(),
                  recap_for=kw.pop("recap_for", lambda s: None), run_type_for=lambda s: "new", now=NOW, **kw)


def test_verified_development_offers_updates_with_credit_cost():
    out = _ask(recap_for=lambda s: {"run_id": "r1", "run_at": "2026-11-20", "final_action": "BUY",
                                    "recap_json": {"price_target": 210}} if s == "AMAT" else None)
    assert out["verified"] and out["development"]["date"] == "2026-11-26"
    rows = {r["ticker"]: r for r in out["tickers"]}
    assert rows["AMAT"]["latest"]["run_id"] == "r1" and rows["AMAT"]["can_update"]
    assert rows["NVDA"]["latest"] is None and rows["NVDA"]["credits"] == 2


@pytest.mark.parametrize("raw,why", [
    (_answer(sources=1), "fewer than two"),
    (_answer(verified=False), "fewer than two"),
    (_answer(date="2026-08-01"), "more than 60 days"),
    (_answer(date="2026-12-25"), "future"),
    (json.dumps({"answer": "a", "development": None, "sources": [], "tickers": []}), "specific development"),
])
def test_unverified_claims_can_never_update_an_analysis(raw, why):
    out = _ask(raw=raw)
    assert not out["verified"] and why in out["verification"]
    assert all(not r["can_update"] for r in out["tickers"])
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], SimpleNamespace(id=7), "AMAT", now=NOW)
    assert e.value.status in (422,)


def test_same_outlet_twice_is_one_source():
    raw = json.loads(_answer())
    raw["sources"] = [{"url": "https://www.reuters.com/a"}, {"url": "https://reuters.com/b"}]
    assert not _ask(raw=json.dumps(raw))["verified"]


def test_binding_is_owner_only_single_use_and_ticker_scoped():
    out = _ask()
    me = SimpleNamespace(id=7)
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], SimpleNamespace(id=8), "AMAT", now=NOW)
    assert e.value.status == 404
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], me, "MSFT", now=NOW)
    assert e.value.status == 422
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], me, "AMAT", now=NOW + timedelta(hours=25))
    assert e.value.status == 410
    ma.check_bindable(out["ask_id"], me, "AMAT", now=NOW)
    assert ma.bind_run(out["ask_id"], "AMAT", "run-1") is True
    assert ma.bind_run(out["ask_id"], "AMAT", "run-2") is False
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], me, "AMAT", now=NOW)
    assert e.value.status == 409
    dev = ma.development_for_run("run-1")
    assert list(dev) == ["AMAT"] and dev["AMAT"]["headline"].startswith("US widens")
    assert dev["AMAT"]["question"].startswith("What do") and len(dev["AMAT"]["sources"]) == 2
    assert ma.development_for_run("run-2") == {}


def test_daily_cap_per_plan_and_admin_exempt(monkeypatch):
    monkeypatch.setattr(ma, "ASKS_PER_DAY", {"free": 2, "starter": 2, "professional": 2})
    u = SimpleNamespace(id=7, role="member")
    _ask(u), _ask(u)
    with pytest.raises(ma.AskError) as e:
        _ask(u)
    assert e.value.status == 429 and e.value.detail["code"] == "ask_limit"
    admin = SimpleNamespace(id=1, role="admin")
    for _ in range(3):
        _ask(admin)


def test_garbage_answer_is_503_and_short_question_422():
    with pytest.raises(ma.AskError) as e:
        _ask(raw="not json at all")
    assert e.value.status == 503
    with pytest.raises(ma.AskError) as e:
        ma.clean_question("hi")
    assert e.value.status == 422


def test_unsupported_listings_are_dropped_and_hint_comes_first():
    out = _ask(raw=_answer(tickers=("ASML.AS", "9988.HK", "D05.SI", "NVDA")), hint="amat")
    assert [r["ticker"] for r in out["tickers"]] == ["AMAT", "09988.HK", "D05.SI", "NVDA"]


def test_question_detection():
    assert ma.looks_like_question("what happened to nvda?")
    assert ma.looks_like_question("chip export curbs impact on AMAT")
    assert not ma.looks_like_question("Applied Materials")
    assert not ma.looks_like_question("NVDA")


# ── pipeline side ──────────────────────────────────────────────────────────

DEV = {"headline": "US widens curbs", "date": "2026-11-26", "summary": "s", "question": "q?", "ask_id": "a1",
       "relevance": "China is 30% of revenue",
       "sources": [{"title": "Reuters", "url": "https://reuters.com/x", "date": "2026-11-26", "fact": "f"}]}


def test_development_forces_the_material_amend_path_on_a_covered_ticker():
    from src.agents.industry import deep_research as dr
    prior = {"material": False, "events": [], "verdict": "no material change", "snippets": ""}
    assert dr._delta_route(5, prior) == "replay"
    d = ud.merge_all({"AMAT": prior}, {"AMAT": DEV})["AMAT"]
    assert d["material"] is True and d["events"][0]["source"] == "user_question"
    assert dr._is_material(d) and dr._delta_route(5, d) == "one_search"
    assert "https://reuters.com/x" in d["snippets"] and d["user_development"]["ask_id"] == "a1"
    rec = dr._material_update_record("amended", d)
    assert rec["events"][0]["headline"] == "US widens curbs"


def test_new_ticker_gets_a_delta_and_a_research_brief():
    d = ud.merge_all({}, {"NVDA": DEV})
    assert d["NVDA"]["material"] is True
    assert "USER-RAISED DEVELOPMENT" in ud.research_brief(d["NVDA"])
    assert ud.research_brief({"material": True}) == ""
    assert ud.merge_all({"X": {"material": False}}, None) == {"X": {"material": False}}


# ── routes ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.backend.database import get_db
    from app.backend.routes import analysis as A
    from app.backend.services import billing, queue_client, rate_limiter
    me = SimpleNamespace(id=7, role="member", daily_pipeline_limit=None, concurrent_pipeline_limit=None)
    monkeypatch.setattr(A, "_get_user", lambda request, db: me)
    real_ask = ma.ask
    monkeypatch.setattr(ma, "ask", lambda user, q, hint=None: real_ask(
        user, q, hint, search=lambda *_: _answer(), recap_for=lambda s: None, run_type_for=lambda s: "covered",
        now=datetime.now(timezone.utc)))
    calls = {"reserve": 0, "enqueue": [], "cache": 0, "claim": True}
    monkeypatch.setattr(billing, "reserve_run", lambda *a, **k: calls.__setitem__("reserve", calls["reserve"] + 1))

    async def no_limits(**kw):
        return None

    async def yes():
        return True

    async def claim(key, run_id):
        return calls["claim"]

    async def runner(key):
        return "someone-elses-run"

    async def enqueue(**kw):
        calls["enqueue"].append(kw)

    async def release(key):
        calls["released"] = True

    async def cached(*a, **k):
        calls["cache"] += 1
        return None

    async def stream(run_id, ticker, model_name, waiter, user_id=None):
        yield f"event: start\ndata: {json.dumps({'run_id': run_id})}\n\n"

    class _Keys:
        def __init__(self, db):
            pass

        def get_api_keys_dict(self, user_id):
            return {}

    monkeypatch.setattr(rate_limiter, "check_limits", no_limits)
    monkeypatch.setattr(queue_client, "queue_mode_enabled", yes)
    monkeypatch.setattr(queue_client, "claim_run", claim)
    monkeypatch.setattr(queue_client, "get_runner_run_id", runner)
    monkeypatch.setattr(queue_client, "enqueue_analysis", enqueue)
    monkeypatch.setattr(queue_client, "release_run", release)
    monkeypatch.setattr(A, "_scoped_cached_run", cached)
    monkeypatch.setattr(A, "_queue_stream_generator", stream)
    monkeypatch.setattr(A, "ApiKeyService", _Keys)
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app, raise_server_exceptions=False), calls


def test_ask_route_answers_and_update_run_binds_the_ask(client):
    c, calls = client
    r = c.post("/analysis/ask", json={"question": "What do the new chip export curbs mean for AMAT?"})
    assert r.status_code == 200 and r.json()["verified"]
    ask_id = r.json()["ask_id"]
    r = c.post("/analysis/run", json={"ticker": "AMAT", "model": "qwen3.8-flash", "ask_id": ask_id})
    assert r.status_code == 200, r.text
    assert calls["reserve"] == 1 and calls["cache"] == 0            # fresh run: no 30-min cached replay
    run_id = calls["enqueue"][0]["run_id"]
    assert list(ma.development_for_run(run_id)) == ["AMAT"]
    r = c.post("/analysis/run", json={"ticker": "AMAT", "ask_id": ask_id})
    assert r.status_code == 409                                     # single use


def test_unverified_or_foreign_ticker_ask_is_refused_before_credits(client, monkeypatch):
    c, calls = client
    ask_id = c.post("/analysis/ask", json={"question": "What do the new chip export curbs mean for AMAT?"}).json()["ask_id"]
    r = c.post("/analysis/run", json={"ticker": "MSFT", "ask_id": ask_id})
    assert r.status_code == 422 and calls["reserve"] == 0
    r = c.post("/analysis/run", json={"ticker": "AMAT", "ask_id": "not-a-uuid"})
    assert r.status_code == 422 and calls["reserve"] == 0


def test_update_never_joins_someone_elses_in_flight_run(client):
    c, calls = client
    ask_id = c.post("/analysis/ask", json={"question": "What do the new chip export curbs mean for AMAT?"}).json()["ask_id"]
    calls["claim"] = False
    r = c.post("/analysis/run", json={"ticker": "AMAT", "ask_id": ask_id})
    assert r.status_code == 409 and "running right now" in r.json()["detail"]
    assert not calls["enqueue"]
    calls["claim"] = True                                           # the ask is still usable afterwards
    assert c.post("/analysis/run", json={"ticker": "AMAT", "ask_id": ask_id}).status_code == 200


def test_updates_are_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("MARKET_ASK_UPDATES", raising=False)
    out = _ask()
    assert out["verified"] and all(not r["can_update"] for r in out["tickers"])
    with pytest.raises(ma.AskError) as e:
        ma.check_bindable(out["ask_id"], SimpleNamespace(id=7), "AMAT", now=NOW)
    assert e.value.status == 503
