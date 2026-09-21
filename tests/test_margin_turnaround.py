"""The base FCF margin of a structural turnaround, and inventory built against
contracted work.

Owner, 2026-09-21, on GE Vernova's $87 DCF: "an artifact of an erroneously low
2% FCF margin assumption ... anchoring the model to distressed historical
carve-out margins." The trace agreed, and found two defects:

  1. the base margin was the five-year MEAN of -6.8%, -2.1%, +1.3%, +4.9%,
     +9.75% = 1.4%, three of those years being pre-spin carve-out financials;
  2. an unscoped inventory-stress gate then took 15% off it, because inventory
     days rose 30 -- while the company built turbines for a $176bn order book.
"""
import inspect

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _turnaround_margin as turn


def _series(margins, field="free_cash_flow", revenue=1e10):
    return [{"revenue": revenue, field: m * revenue, "period": f"{2021 + i}-12-31"} for i, m in enumerate(margins)]


GEV = [-0.0678, -0.0211, 0.0133, 0.0487, 0.0975]


def test_ge_vernova_is_a_turnaround_and_its_base_is_the_last_two_audited_years():
    t = turn(_series(GEV))
    assert t["mean_window"] == pytest.approx(sum(GEV) / 5)             # 1.4%: where the company was
    assert t["recent_two_year"] == pytest.approx((0.0487 + 0.0975) / 2)  # 7.3%: audited, recent, not the best year
    assert t["recent_two_year"] < GEV[-1]


@pytest.mark.parametrize("margins,why", [
    ([0.20, 0.22, 0.25, 0.28, 0.33], "never cash-burning: a grower's mean is still its own history"),
    ([-0.07, -0.02, 0.05, 0.03, 0.10], "one step back: a wandering margin, not a turnaround"),
    ([-0.07, -0.05, -0.03, -0.02, -0.01], "still cash-burning at the close"),
    ([-0.010, -0.005, 0.000, 0.005, 0.010], "latest clears the mean by under 3 points: nothing to correct"),
    ([-0.07, 0.02, 0.10], "fewer than four years: not enough to call a direction"),
    ([0.10, 0.05, 0.01, -0.02, -0.07], "a deterioration is not this guard's business"),
])
def test_it_is_narrow_on_purpose(margins, why):
    assert turn(_series(margins)) is None, why


def test_it_runs_on_whichever_basis_the_live_margin_chose():
    rows = _series(GEV, field="fcf_owner_earnings")
    assert turn(rows, field="fcf_owner_earnings")["recent_two_year"] == pytest.approx(0.0731)
    assert turn(rows) is None                                           # that field is absent: no verdict


def test_missing_years_are_skipped_not_read_as_zero():
    rows = _series(GEV)
    rows[2]["free_cash_flow"] = None
    t = turn(rows)
    assert t["margins"] == [pytest.approx(m, abs=1e-6) for m in (GEV[0], GEV[1], GEV[3], GEV[4])]


def test_the_backtest_takes_the_same_guard_or_it_tests_a_different_method():
    src = inspect.getsource(dcf_agent)
    live = src.index("_turn = _turnaround_margin(series, field=_oe_basis_field)")
    t1 = src.index("_turn_t1 = _turnaround_margin(_hist, field=_t1_field)")
    assert t1 < live                                                    # both exist
    assert '"gate_id": "GATE_MARGIN_TURNAROUND"' in src and "never the base" in src


def test_guidance_is_never_the_base():
    """A guided 25% margin is an overlay with its own acceptance (owner,
    2026-09-20). The guard reads audited years only and never the best one alone."""
    assert "guidance" not in inspect.signature(turn).parameters
    assert turn(_series(GEV))["recent_two_year"] < 0.10


def test_inventory_built_against_an_accepted_backlog_is_not_marked_down():
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    exempt = src.index("_inv_contracted = _inv_backlog_cov is not None and _inv_backlog_cov >= 1.0")
    haircut = src.index("_inv_haircut = fcf_margin_base * _INVENTORY_MARKDOWN_HAIRCUT")
    assert exempt < haircut
    # A precondition of the ONE gate site: it clears the reading, so the gate
    # below is never entered, and there is still exactly one gate record.
    between = src[exempt:haircut]
    assert "NOT marked down" in between and "_inv_days = None" in between
    assert between.count("_inventory_stress_days(series[::-1])") == 1
    # Review-gated like every filing figure: only an ACCEPTED backlog exempts.
    assert '_ii_inv.accepted_detail(\n                ticker, "backlog"' in src
    # Not a profile exemption: Capital Goods also holds dealer-channel names.
    assert "Caterpillar, Deere" in src
