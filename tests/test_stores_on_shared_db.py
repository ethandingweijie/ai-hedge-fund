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
