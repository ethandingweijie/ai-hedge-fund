"""D3 (owner, 2026-11-28): methods that disagree publish a range, a failed currency check withholds the valuation,
and either returns the run's credit."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.decisions.currency_check import currency_verdict


def test_currency_label_mismatch_is_withheld():
    v = currency_verdict("CNY", "USD", "CNY", 120.0, 100.0)
    assert v["code"] == "currency_check" and v["label"] == "Valuation withheld"


@pytest.mark.parametrize("iv,spot,withheld", [(1180.0, 100.0, True), (15.0, 100.0, True),
                                              (450.0, 100.0, False), (25.0, 100.0, False)])
def test_converted_value_far_from_price_is_withheld(iv, spot, withheld):
    assert bool(currency_verdict("USD", "USD", "CNY", iv, spot)) is withheld


def test_same_currency_far_from_price_is_not_a_currency_problem():
    assert currency_verdict("USD", "USD", "USD", 1180.0, 100.0) is None      # dispersion rules handle that
    assert currency_verdict("USD", "USD", "CNY", None, 100.0) is None


def test_methods_that_disagree_publish_the_bear_to_bull_range():
    from src.agents import portfolio_manager as pm
    sc = {"12m_targets_by_scenario": {"bear": 80.0, "base": 100.0, "bull": 140.0}}
    disp = {"ratio": 4.4, "hi": 220.0, "hi_leg": "DCF", "lo": 50.0, "lo_leg": "EV/EBITDA"}
    p = pm._publication(sc, disp)
    assert p["mode"] == "range" and (p["low"], p["high"]) == (80.0, 140.0) and "4.4x" in p["reason"]
    # 2026-11-28 bridge: 2.5x-4x keeps the single target (the rating cap at Neutral is unchanged)
    assert pm._publication(sc, {**disp, "ratio": 3.4}) == {"mode": "point"}
    assert pm._publication(sc, None) == {"mode": "point"}
    assert pm._publication({"12m_targets_by_scenario": {"bear": None, "bull": 140.0}}, disp) == {"mode": "point"}


def test_not_sellable_reasons():
    from app.backend.services import billing as b
    unrated = {"data": {"dcf_range": {"X": {"rating_state": {"state": "unrated", "code": "currency_check"}}}}}
    assert b.not_sellable_reason(unrated, "x") == "unrated:currency_check"
    rng = {"data": {}, "decisions": {"X": {"research_view": {"publication": {"mode": "range"}}}}}
    assert b.not_sellable_reason(rng, "X") == "range_only"
    ok = {"data": {}, "decisions": {"X": {"research_view": {"publication": {"mode": "point"}}}}}
    assert b.not_sellable_reason(ok, "X") is None


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "l.db"))
    from src.data import db
    from app.backend.services import billing as b
    monkeypatch.setattr(db, "get_db_path", lambda: str(tmp_path / "l.db"))
    db.close_all_connections()
    monkeypatch.setitem(b._READY, "v", False)
    monkeypatch.setattr(b, "_is_covered", lambda t: False)            # new ticker: 2 credits
    db.ensure_table("CREATE TABLE IF NOT EXISTS web_runs (run_id TEXT PRIMARY KEY, run_at TEXT, ticker TEXT, "
                    "user_id INTEGER, is_checkpoint INTEGER DEFAULT 0)")
    yield b
    db.close_all_connections()


def test_refund_returns_the_runs_credits(ledger):
    b = ledger
    u = SimpleNamespace(id=9, role="member")
    start = datetime.now(timezone.utc)
    b.reserve_run(u, "MU", now=start)
    assert b.usage(u)["used"] == 2
    assert b.refund_run(9, "MU", start - timedelta(minutes=1), "range_only") == 1
    assert b.usage(u)["used"] == 0
    assert b.refund_run(9, "MU", start - timedelta(minutes=1), "range_only") == 0      # idempotent


def test_moat_roic_excludes_goodwill():
    """D4 (b), owner 2026-11-28: acquirers' organic returns are measured without acquisition goodwill."""
    from src.data import capm
    inc = [{"date": f"{y}-12-31", "operatingIncome": 100.0, "incomeBeforeTax": 100.0, "incomeTaxExpense": 20.0}
           for y in range(2016, 2026)]
    bal = [{"date": f"{y}-12-31", "totalDebt": 200.0, "totalStockholdersEquity": 500.0, "cashAndShortTermInvestments": 100.0,
            "goodwill": 300.0, "intangibleAssets": 100.0} for y in range(2016, 2026)]
    ex = capm.roic_ex_goodwill(inc, bal)
    assert len(ex) == 10 and ex[0] == pytest.approx(80.0 / 200.0)        # vs 80 / 600 = 13% with goodwill
    m = capm.moat_spread(ex, 0.10)
    assert m["tagged"] and m["spread"] == pytest.approx(0.10)              # capped at 10 points
    bal_neg = [{**b, "totalStockholdersEquity": -500.0} for b in bal]      # negative invested capital -> skipped
    assert capm.roic_ex_goodwill(inc, bal_neg) == []
