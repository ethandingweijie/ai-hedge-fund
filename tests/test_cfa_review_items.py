"""CFA review items 2 and 5 (owner, 2026-11-25).

2. Weighted legs further apart than 2.5x cap the rating at Neutral (the blended value is still published);
   an owner review in valuation_constants.dispersion_reviewed lifts the cap for that ticker.
5. Every run reconciles its key inputs against the identities the statements must satisfy.
"""
from __future__ import annotations

import pytest

from src.data import input_reconciliation as irc
from src.decisions import ratings as rt

_DISP = {"ratio": 4.68, "hi": 191.6, "hi_leg": "DCF (2-stage)", "lo": 40.9, "lo_leg": "FCF Yield"}


def _view(**kw):
    return rt.build_research_view(ticker="00941.HK", sector="Telecom", price=78.0, price_as_of="2026-11-25",
                                  target_12m=106.0, intrinsic_value=133.8, dps=5.0, **kw)


def test_unreconciled_legs_cap_a_buy_at_neutral():
    v0 = _view()
    assert v0["research_rating"] == "OVERWEIGHT"
    v = _view(unreconciled=_DISP)
    assert v["research_rating"] == "NEUTRAL" and v["trade_action"] == "HOLD" and v["tactical_rating"] == "OVERWEIGHT"
    assert v["valuation_unreconciled"]["ratio"] == pytest.approx(4.68)
    assert v["compliance"]["status"] == "valuation_unreconciled"
    assert any("valuation unreconciled" in n for n in v["compliance"]["notes"])


def test_the_cap_needs_dispersion_above_the_threshold_and_no_owner_review(monkeypatch):
    assert rt.unreconciled_legs({**_DISP, "ratio": 2.4}) is None
    assert rt.unreconciled_legs(_DISP, "00941.HK") == _DISP
    from src.data import valuation_constants as vc
    monkeypatch.setattr(vc, "load", lambda *a, **k: {"dispersion_reviewed": {"tickers": {"00941.HK": {"reason": "x"}}}})
    assert rt.unreconciled_legs(_DISP, "00941.HK") is None


def test_bvps_identity_catches_book_value_on_total_equity():
    # Ping An before the fix: FMP BVPS on total equity (incl. CNY 415.7bn minorities)
    row = {"book_value_per_share": 78.23, "total_equity": 1000.8e9}
    assert irc.check_bvps(row, 18.1e9)["status"] == "fail"
    assert irc.check_bvps({**row, "book_value_per_share": 55.27}, 18.1e9)["status"] == "pass"


def test_ebitda_bridge_fails_a_bank_and_notes_an_associate_heavy_name():
    ocbc = {"ebitda": 22.19e9, "operating_income": 8.5e9, "depreciation_and_amortization": 0.6e9}
    assert irc.check_ebitda_bridge(ocbc, interest_is_cost=True)["status"] == "fail"
    assert irc.check_ebitda_bridge(ocbc, interest_is_cost=False)["status"] == "warn"
    assert irc.check_ebitda_bridge({"ebitda": 10.0, "operating_income": 8.0, "depreciation_and_amortization": 2.0})["status"] == "pass"


def test_freshness_net_debt_dividends_and_inputs():
    assert irc.check_freshness({"period": "2024-06-30"}, "2026-11-25")["status"] == "fail"
    assert irc.check_freshness({"period": "2025-12-31"}, "2026-11-25")["status"] == "pass"
    assert irc.check_net_debt({"total_debt": 100.0, "cash_and_equivalents": 30.0, "net_debt": 50.0})["status"] == "fail"
    assert irc.check_dividends({"dividends_per_share": 1.0, "dividends_and_distributions": -0.5e9}, 1e9)["status"] == "fail"
    bad = irc.check_accepted_inputs([{"kind": "embedded_value", "period": "FY2023", "source_url": ""}], "2026-11-25")
    assert bad[0]["status"] == "fail" and "no source URL" in bad[0]["detail"] and "stale" in bad[0]["detail"]
    out = irc.reconcile({"book_value_per_share": 78.23, "total_equity": 1000.8e9}, shares=18.1e9)
    assert out["failed"] == 1 and out["flags"][0].startswith("Input check failed")
