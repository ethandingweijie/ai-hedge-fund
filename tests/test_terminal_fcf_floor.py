"""D3 (owner, 2026-11-27): the sector FCF-margin floor binds the TERMINAL year only.

A positive floor (Telco / REIT / Professional Services +5%, Consumer / Industrials +2%, Materials +1%) used to lift
every projected year, so a -20% cash burner was valued on +5% margins with no flag. Earlier years below the floor are
now projected as modelled and recorded; the terminal year (and Gate B's terminal-margin estimate) still floors.
"""
from __future__ import annotations

import pytest

from src.agents.analysis import dcf_agent as d


def _run(margin: float, floor: float, **kw):
    return d._project_dcf(revenue_base=1_000.0, fcf_margin_base=margin, growth_rate=0.0, margin_delta_per_year=0.0,
                          wacc=0.08, tgr=0.02, fcf_floor=floor, net_debt=0.0, shares=1.0, **kw)


def test_positive_floor_lifts_only_the_terminal_year():
    iv, _, _, rows = _run(-0.20, 0.05)
    margins = [r["fcf_margin"] for r in rows]
    assert margins[:-1] == [pytest.approx(-0.20)] * (len(rows) - 1)
    assert margins[-1] == pytest.approx(0.05)
    assert [i + 1 for i, r in enumerate(rows) if r["below_fcf_floor"]] == list(range(1, len(rows)))
    # the old every-year lift valued the burner at +5% throughout: strictly more than now
    iv_all_years = sum(1_000.0 * 0.05 / 1.08 ** t for t in range(1, len(rows) + 1))
    assert iv < iv_all_years


def test_a_loss_clamp_floor_still_binds_every_year():
    _, _, _, rows = _run(-0.40, -0.05)
    assert all(r["fcf_margin"] == pytest.approx(-0.05) for r in rows)
    assert not any(r["below_fcf_floor"] for r in rows)


def test_a_margin_above_the_floor_is_untouched():
    _, _, _, rows = _run(0.12, 0.05)
    assert all(r["fcf_margin"] == pytest.approx(0.12) for r in rows)
    assert not any(r["below_fcf_floor"] for r in rows)


def test_schedule_below_the_floor_is_flagged_year_by_year():
    sched = [0.01, 0.02, 0.03, 0.04, 0.06, 0.06, 0.06, 0.06, 0.06, 0.03]
    _, _, _, rows = _run(0.03, 0.05, margin_schedule=sched)
    assert [r["fcf_margin"] for r in rows][:4] == [pytest.approx(x) for x in sched[:4]]
    assert rows[-1]["fcf_margin"] == pytest.approx(0.05)
    assert [i + 1 for i, r in enumerate(rows) if r["below_fcf_floor"]] == [1, 2, 3, 4]


def test_gate_b_terminal_estimate_uses_the_same_rule():
    assert d._floored_fcf_margin(-0.20, 0.05, terminal=True) == pytest.approx(0.05)
    assert d._floored_fcf_margin(-0.20, 0.05, terminal=False) == pytest.approx(-0.20)
    assert d._floored_fcf_margin(-0.20, -0.05, terminal=False) == pytest.approx(-0.05)
    import inspect
    src = inspect.getsource(d.run_dcf_agent)
    assert "_floored_fcf_margin(fcf_margin_base + md_abs, fcf_floor, terminal=True)" in src


def test_the_breach_is_a_data_limitation():
    from src.utils.report_disclosures import data_limitations
    data = {"dcf_range": {"Z": {"base": {"leg_inputs": {"DCF": {"fcf_floor": 0.05, "fcf_floor_breach_years": [1, 2]}}}}}}
    lines = data_limitations(data, "Z")
    assert any("year(s) 1, 2" in x and "terminal year only" in x for x in lines)


def test_floor_values_unchanged():
    from src.data.sector_profiles import FCF_MARGIN_FLOOR
    assert FCF_MARGIN_FLOOR["Telco"] == 0.05 and FCF_MARGIN_FLOOR["Consumer"] == 0.02
    assert FCF_MARGIN_FLOOR["Materials"] == 0.01 and FCF_MARGIN_FLOOR["Tech"] == -0.05
