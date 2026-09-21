"""Wave 2 (power & transition): the engine work that lands ahead of the
owner-confirmed method tables.

P/Rate Base used to sit in the P/BV set and price book value x peer P/B under
a rate-base label (Stage 0, 2026-09-21: NEE, DUK and SO each carried half their
surviving weight on it). It is now the regulator's own arithmetic, and it is
inert for every ticker until two things exist: an owner-ACCEPTED rate base with
its allowed ROE, and an owner-SET cost of equity for the profile. Until then
the profile's declared P/BV proxy prices the weight, exactly as before.
"""
import pytest

from src.agents.analysis import dcf_agent
from src.data import valuation_constants as vc

PROFILE = "Regulated Utility"


def _value(row, monkeypatch, coe=0.085, scenario="base", tgr=0.02, method="P/Rate Base"):
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pb": 2.0})
    monkeypatch.setattr(vc, "cost_of_equity", lambda profile, doc=None: coe)
    return dcf_agent._compute_method_value(
        method_name=method, most_recent=row, revenue_base=2.5e10, shares=2e9, net_debt=9e10,
        market_cap=1.5e11, wacc=0.045, growth_base=0.04, fcf_margin_base=0.05, tgr=tgr,
        fcf_floor=0.0, sector="Energy", scenario=scenario, profile_name=PROFILE)


def _row(**over):
    row = {"book_value_per_share": 25.0, "total_equity": 5e10, "total_debt": 9e10,
           "rate_base_accepted": 7.1e10,
           "_rate_base_detail": {"value": 7.1e10, "allowed_roe": 0.108, "equity_ratio": 0.596}}
    row.update(over)
    return row


def test_the_leg_is_the_justified_multiple_on_the_equity_layer(monkeypatch):
    v = _value(_row(), monkeypatch)
    equity_rate_base_ps = 7.1e10 * 0.596 / 2e9
    assert v == pytest.approx(equity_rate_base_ps * (0.108 - 0.02) / (0.085 - 0.02))


def test_net_debt_is_not_subtracted(monkeypatch):
    """The equity layer is already the equity-funded slice of the rate base."""
    a = _value(_row(), monkeypatch)
    monkeypatch.undo()
    monkeypatch.setattr(dcf_agent, "get_sector_peer_multiples", lambda *a, **k: {"pb": 2.0})
    monkeypatch.setattr(vc, "cost_of_equity", lambda profile, doc=None: 0.085)
    b = dcf_agent._compute_method_value(
        method_name="P/Rate Base", most_recent=_row(), revenue_base=2.5e10, shares=2e9, net_debt=0.0,
        market_cap=1.5e11, wacc=0.045, growth_base=0.04, fcf_margin_base=0.05, tgr=0.02,
        fcf_floor=0.0, sector="Energy", scenario="base", profile_name=PROFILE)
    assert a == b


@pytest.mark.parametrize("row,coe", [
    (_row(rate_base_accepted=None, _rate_base_detail={}), 0.085),          # nothing accepted
    (_row(_rate_base_detail={"value": 7.1e10, "allowed_roe": None, "equity_ratio": 0.5}), 0.085),  # SoC: no ROE
    (_row(), None),                                                        # no cost of equity authored
])
def test_a_missing_input_declines_so_the_declared_proxy_prices_the_weight(row, coe, monkeypatch):
    assert _value(row, monkeypatch, coe=coe) is None


def test_it_is_no_longer_book_value_under_another_name(monkeypatch):
    """The pre-Wave-2 behaviour: bvps 25 x peer pb 2.0 = 50, identical to P/BV."""
    assert _value(_row(), monkeypatch, method="P/BV") == pytest.approx(50.0)
    assert _value(_row(), monkeypatch) != pytest.approx(50.0)
    assert _value(_row(rate_base_accepted=None, _rate_base_detail={}), monkeypatch) is None


def test_no_authorised_equity_ratio_falls_to_the_book_capital_structure(monkeypatch):
    row = _row(_rate_base_detail={"value": 7.1e10, "allowed_roe": 0.108, "equity_ratio": None})
    v = _value(row, monkeypatch)
    assert v == pytest.approx(7.1e10 * (5e10 / (5e10 + 9e10)) / 2e9 * (0.108 - 0.02) / (0.085 - 0.02))


def test_an_allowed_roe_at_or_below_growth_declines(monkeypatch):
    row = _row(_rate_base_detail={"value": 7.1e10, "allowed_roe": 0.02, "equity_ratio": 0.5})
    assert _value(row, monkeypatch) is None


def test_the_scenarios_stay_ordered(monkeypatch):
    bear, base, bull = (_value(_row(), monkeypatch, scenario=s) for s in ("bear", "base", "bull"))
    assert bear <= base <= bull


def test_the_real_method_is_requested_beside_its_proxy_and_no_cost_of_equity_is_authored_yet():
    assert "P/Rate Base" in dcf_agent._PER_TICKER_METHODS
    assert dcf_agent._LOOKTHROUGH_METHODS <= dcf_agent._PER_TICKER_METHODS
    # Owner-set, and not set: the leg is inert in production until it is.
    assert vc.cost_of_equity(PROFILE) is None
    assert vc.cost_of_equity(None) is None


def test_the_profile_still_declares_the_proxy_so_no_weight_is_lost():
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES as P
    leg = next(m for m in P["Energy"][PROFILE]["methods"] if m["name"] == "P/Rate Base")
    assert leg["implementable"] is False and leg["proxy"] == "P/BV"
