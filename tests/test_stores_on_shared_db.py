"""The IV15 alert, robo-strategy and ETF-metadata stores use the shared dual-mode
`src.data.db` layer (2026-11-27). Their private sqlite3 connections followed
RUN_ARCHIVE_PATH to a Windows path that does not exist in the production
container, so all three silently failed there since August."""
import importlib
import inspect

import pytest

MODULES = [
    "app.backend.services.iv15_alert_store",
    "app.backend.services.robo_strategy_storage",
    "app.backend.services.etf_metadata_service",
]


@pytest.fixture
def tmp_archive(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "archive.db"))
    for name in MODULES:
        importlib.import_module(name)._ensured_for = None
    yield
    for name in MODULES:
        importlib.import_module(name)._ensured_for = None


@pytest.mark.parametrize("name", MODULES)
def test_no_private_sqlite_connection(name):
    src = inspect.getsource(importlib.import_module(name))
    assert "import sqlite3" not in src
    assert "from src.data import db" in src


def test_iv15_state_roundtrip(tmp_archive):
    from app.backend.services import iv15_alert_store as s
    s.upsert_state(cohort="sw46", ticker="d05.si", state="triggered", p_iv15=0.9,
                   price=40.0, iv15=44.0, alerted=True)
    first = s.get_all_states("sw46")["D05.SI"]
    assert first["state"] == "triggered" and first["last_alert_at"]
    # A later non-alert update keeps the original alert time (hysteresis audit).
    s.upsert_state(cohort="sw46", ticker="D05.SI", state="armed", p_iv15=1.2,
                   price=53.0, iv15=44.0)
    again = s.get_all_states("sw46")["D05.SI"]
    assert again["state"] == "armed"
    assert again["last_alert_at"] == first["last_alert_at"]
    assert s.get_all_states("hk50") == {}


def test_iv15_sweep_idempotency(tmp_archive):
    from app.backend.services import iv15_alert_store as s
    assert s.last_sweep_at() is None and not s.swept_within_hours(20)
    s.record_sweep(checked=3, fired=1, rearmed=0, skipped=2, detail={"x": 1})
    assert s.last_sweep_at() and s.swept_within_hours(20)


def test_robo_upsert_and_delete(tmp_archive):
    from app.backend.services import robo_strategy_storage as s
    assert s.get_profile(7) is None
    s.save_profile(7, {"risk": "high", "bad": float("nan")})
    s.save_profile(7, {"risk": "low"})
    assert s.get_profile(7) == {"risk": "low"}
    s.save_portfolio(7, {"w": [1]}, mode="stocks")
    s.save_portfolio(7, {"w": [2]})
    assert s.get_portfolio(7) == {"w": [2]}
    s.delete_profile(7)
    assert s.get_profile(7) is None and s.get_portfolio(7) is None


def test_etf_cache_roundtrip(tmp_archive):
    from app.backend.services import etf_metadata_service as s
    s._ensure_table()
    assert s._get_cached() is None
    s._set_cached([{"ticker": "VT"}])
    s._set_cached([{"ticker": "VTI"}])
    assert s._get_cached() == [{"ticker": "VTI"}]
    s.invalidate_etf_cache()
    assert s._get_cached() is None


# ── IV15 revival: silent re-baseline after an outage ────────────────────────

def _fake_sweep_inputs(monkeypatch, prices):
    from types import SimpleNamespace
    from src.research_ideas.alerts import iv15_monitor as m
    from src.research_ideas.alerts import batch_quote
    cands = [m.AlertCandidate(cohort="hk50", quote_symbol=t, display_ticker=t, name=t, iv15=10.0)
             for t in prices]
    monkeypatch.setattr(m, "load_sw46_candidates", lambda: [])
    monkeypatch.setattr(m, "load_hk50_candidates", lambda: cands)
    monkeypatch.setattr(batch_quote, "fetch_batch_quotes",
                        lambda syms: {t: SimpleNamespace(price=prices[t]) for t in syms})
    posts = []
    import src.research_ideas.alerts.iv15_slack as slack
    monkeypatch.setattr(slack, "post_iv15_alerts", lambda fired, **kw: posts.append(fired) or True)
    return m, posts


def test_first_sweep_after_outage_is_silent_baseline(tmp_archive, monkeypatch):
    m, posts = _fake_sweep_inputs(monkeypatch, {"0700.HK": 8.0, "0005.HK": 12.0})
    r = m.run_iv15_sweep()
    assert r.baseline and [f.display_ticker for f in r.fired] == ["0700.HK"]
    assert posts == []                               # nothing posted
    from app.backend.services import iv15_alert_store as s
    st = s.get_all_states("hk50")
    assert st["0700.HK"]["state"] == "triggered" and st["0700.HK"]["last_alert_at"] is None
    assert st["0005.HK"]["state"] == "armed"

    # Next day: the already-cheap name does not re-fire; a fresh cross does.
    m2, posts2 = _fake_sweep_inputs(monkeypatch, {"0700.HK": 8.0, "0005.HK": 9.0})
    r2 = m2.run_iv15_sweep()
    assert not r2.baseline
    assert [f.display_ticker for f in r2.fired] == ["0005.HK"]
    assert len(posts2) == 1


def test_dry_run_never_persists_baseline(tmp_archive, monkeypatch):
    m, posts = _fake_sweep_inputs(monkeypatch, {"0700.HK": 8.0})
    r = m.run_iv15_sweep(dry_run=True)
    assert r.baseline and posts == []
    from app.backend.services import iv15_alert_store as s
    assert s.get_all_states("hk50") == {} and s.last_sweep_at() is None



# ── HK50 candidate hygiene (2026-11-27 prod dry run) ────────────────────────

def test_hk50_quotes_the_hk_line_it_was_priced_on():
    from src.research_ideas.alerts.iv15_monitor import _hk50_quote_symbol, _quote_currency
    yum = {"ticker": "9987.HK", "hk_ticker": "YUMC", "route_label": "HK/FMP"}
    assert _hk50_quote_symbol(yum) == "9987.HK"
    assert _hk50_quote_symbol({"ticker": "BABA", "hk_ticker": "9988.HK", "route_label": "ADR/FMP"}) == "BABA"
    assert _hk50_quote_symbol({"ticker": "0700.HK", "hk_ticker": "0700.HK", "route_label": "HK/FMP"}) == "0700.HK"
    assert _quote_currency("0700.HK") == "HKD" and _quote_currency("BABA") == "USD"


def test_hk50_loader_cohort_only_and_quote_currency(monkeypatch):
    from src.research_ideas.alerts import iv15_monitor as m
    from app.backend.services import hk50_storage
    rows = [
        {"ticker": "0700.HK", "hk_ticker": "0700.HK", "route_label": "HK/FMP", "currency": "CNY",
         "iv15": 420.0, "price": 431.0, "in_cohort": True},
        {"ticker": "2007.HK", "hk_ticker": "2007.HK", "route_label": "HK/FMP", "currency": "CNY",
         "iv15": 3.7, "price": 0.14, "in_cohort": False},
    ]
    monkeypatch.setattr(hk50_storage, "get_latest_hk50_run", lambda: {"results": rows})
    cands = m.load_hk50_candidates()
    assert [c.quote_symbol for c in cands] == ["0700.HK"]
    assert cands[0].currency == "HKD"          # labels an HKD price, not the CNY reporting ccy


def test_implausible_fair_value_never_alerts(tmp_archive, monkeypatch):
    m, posts = _fake_sweep_inputs(monkeypatch, {"0966.HK": 2.0})
    from src.research_ideas.alerts import iv15_monitor as mm
    cands = [mm.AlertCandidate(cohort="hk50", quote_symbol="0966.HK", display_ticker="0966.HK",
                               name="x", iv15=10.0, stored_price=2.5)]   # stored P/IV15 0.25
    monkeypatch.setattr(mm, "load_hk50_candidates", lambda: cands)
    r = mm.run_iv15_sweep(baseline=False)
    assert r.fired == [] and r.implausible == ["hk50:0966.HK"] and posts == []


# ── 100-Question screener + complacency peer benchmarks (ported 2026-11-27) ──

HQ = "src.research_ideas.hundred_q.storage"
PB = "src.research_ideas.complacency.peer_benchmarks"


@pytest.fixture
def tmp_archive2(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "archive2.db"))
    for name in (HQ, PB):
        importlib.import_module(name)._ensured_for = None
    yield
    for name in (HQ, PB):
        importlib.import_module(name)._ensured_for = None


@pytest.mark.parametrize("name", [HQ, PB])
def test_research_stores_have_no_private_sqlite(name):
    src = inspect.getsource(importlib.import_module(name))
    assert "import sqlite3" not in src and "from src.data import db" in src


def _cohort(run_id, tier, composite, answers):
    from src.research_ideas.hundred_q.schemas import HundredQCohortResult, HundredQTickerResult, QuestionAnswer
    led = [QuestionAnswer(question_id=q, pillar="P1", label=q, q_type="quant", answer=a,
                          evaluated_at=f"2026-11-27T00:00:0{i}+00:00") for i, (q, a) in enumerate(answers)]
    t = HundredQTickerResult(ticker="MSFT", name="Microsoft", tier=tier, composite_pct=composite, question_ledger=led)
    return HundredQCohortResult(run_id=run_id, created_at="2026-11-27T00:00:00+00:00", run_type="weekly_quant",
                                ticker_count=1, results=[t])


def test_hundred_q_run_watchlist_and_tier_history(tmp_archive2):
    s = importlib.import_module(HQ)
    s.save_run(_cohort("r1", "on_deck", 55.0, [("P1.1", True), ("P1.2", False)]))
    s.save_run(_cohort("r2", "active_pass", 81.0, [("P1.1", False)]))
    wl = s.get_watchlist()
    assert len(wl) == 1 and wl[0]["tier"] == "active_pass" and wl[0]["composite_pct"] == 81.0
    hist = s.get_tier_history("MSFT")
    assert [(h["from_tier"], h["to_tier"]) for h in hist] == [("on_deck", "active_pass")]
    assert len(s.get_question_ledger("r1", "msft")) == 2
    latest = s.get_latest_quant_answers("MSFT")
    assert set(latest) == {"P1.1", "P1.2"}
    assert s.get_latest_run_id() in ("r1", "r2")
    assert s.get_latest_run_by_type("weekly_quant")["run_id"] in ("r1", "r2")
    assert s.get_cohort_summary()["tier_counts"] == {"active_pass": 1}


def test_hundred_q_sweep_event_and_qual_cache(tmp_archive2):
    s = importlib.import_module(HQ)
    from src.research_ideas.hundred_q.schemas import QuestionAnswer
    s.record_sweep_run("sw1", "daily_trigger_sweep", 30)
    assert s.get_latest_run_by_type("daily_trigger_sweep")["ticker_count"] == 30
    s.record_event_triggered_run("ev1", "msft", "earnings",
                                 [QuestionAnswer(question_id="P2.1", pillar="P2", label="x", q_type="quant", answer=True)])
    assert len(s.get_question_ledger("ev1", "MSFT")) == 1
    s.set_qual_answer("msft", "P5.1", "P5", True, 0.8, "ok", "[]", "deepseek-v4-flash", 0.001, "weekly")
    s.set_qual_answer("msft", "P5.1", "P5", False, 0.6, "changed", "[]", "deepseek-v4-flash", 0.001, "event")
    c = s.get_qual_cache("MSFT")
    assert c["P5.1"]["answer"] == 0 and c["P5.1"]["triggered_by"] == "event"
    assert s.get_qual_cache("MSFT", ["P5.1"]).keys() == {"P5.1"}
    assert s.get_stale_qual_question_ids("MSFT", ["P5.1", "P5.2"]) == ["P5.2"]
    assert s.get_stale_qual_question_ids("MSFT", []) == []


def test_watchlist_sorts_null_composite_last(tmp_archive2):
    s = importlib.import_module(HQ)
    from src.data import db
    s.save_run(_cohort("r1", "on_deck", 40.0, []))
    s._ensure_tables()
    db.execute("INSERT INTO hq_watchlist (ticker, tier, composite_pct) VALUES ('NUL', 'on_deck', NULL)")
    assert [r["ticker"] for r in s.get_watchlist()] == ["MSFT", "NUL"]


def test_peer_benchmarks_roundtrip(tmp_archive2):
    pb = importlib.import_module(PB)
    assert pb._load_latest("complacency") == {}
    pb._save_batch("complacency", {"dso_days": {"median": 70, "p25": 50, "p75": 90}}, 48)
    got = pb._load_latest("complacency")
    assert got["dso_days"]["median"] == 70 and got["dso_days"]["sample_size"] == 48
    assert pb._latest_refresh("complacency")


def test_ensure_serial_id_is_noop_on_sqlite(tmp_archive2):
    from src.data import db
    assert db.ensure_serial_id("hq_question_ledger") is False


def test_schema_guard_runs_id_sequence_check():
    from app.backend.database import schema_guard as g
    src = inspect.getsource(g.ensure_all)
    assert "ensure_id_sequences" in src
    import sqlalchemy as sa
    g.ensure_id_sequences(sa.create_engine("sqlite://"))     # no-op off Postgres, never raises


def test_hundred_q_trigger_state_survives_in_archive(tmp_archive2):
    s = importlib.import_module(HQ)
    assert s.load_trigger_state() == {}
    s.save_trigger_state({"MSFT": {"new_10q": "0000950170-26-1", "earnings_reported": "2026-10-28"}})
    s.save_trigger_state({"MSFT": {"new_10q": "0000950170-26-2"}})
    assert s.load_trigger_state() == {"MSFT": {"new_10q": "0000950170-26-2", "earnings_reported": "2026-10-28"}}


def test_daily_sweep_uses_archive_trigger_state():
    src = inspect.getsource(importlib.import_module("src.research_ideas.hundred_q.runner").run_daily_trigger_sweep)
    assert "storage.load_trigger_state()" in src and "storage.save_trigger_state(state)" in src


def test_hk50_price_is_the_hk_quote_not_eps_times_pe(monkeypatch):
    """A/H listing: provider P/E is the A-share multiple (ICBC 9.81 rebuilt vs HK$7.94)."""
    import scripts.hk50_iv15 as m
    from types import SimpleNamespace
    monkeypatch.setattr(m, "get_financial_metrics", lambda *a, **k: [SimpleNamespace(
        return_on_equity=0.1, payout_ratio=0.3, price_to_earnings_ratio=8.1, currency="CNY",
        earnings_per_share=1.21, revenue_growth=0.02)])
    monkeypatch.setattr(m, "_yf_info", lambda t: {"currentPrice": 7.94})
    monkeypatch.setattr(m, "_owner_earnings_retention", lambda t: None, raising=False)
    inp = m.build_inputs("1398.HK")
    assert inp["price"] == 7.94 and abs(inp["pe"] - 7.94 / 1.21) < 1e-9
    monkeypatch.setattr(m, "_yf_info", lambda t: {})
    import src.research_ideas.alerts.batch_quote as bq
    monkeypatch.setattr(bq, "fetch_batch_quotes", lambda syms: {"1398.HK": SimpleNamespace(price=7.62)})
    assert m.build_inputs("1398.HK")["price"] == 7.62      # FMP quote when yfinance is down
