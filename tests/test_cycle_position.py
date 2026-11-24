"""Cycle position and mid-cycle margin (owner, 2026-11-12: cyclical and money-losing firms, phase 1)."""
import pytest
from src.agents.analysis import cycle


def _series(margins, rev=None, start=2016):
    rev = rev or [100.0] * len(margins)
    return [{"period": f"{start + i}-12-31", "revenue": r, "ebit": r * m} for i, (m, r) in enumerate(zip(margins, rev))]


def test_a_memory_boom_reads_as_a_peak_and_reverts_to_its_own_mid_cycle():
    c = cycle.measure(_series([0.30, 0.45, 0.10, 0.05, 0.25, 0.30, -0.15, 0.05, 0.20, 0.42]), profile_cyclical=True)
    assert c["cyclical"] and c["data_cyclical"] and c["position"] == "peak"
    assert 0.15 <= c["mid_cycle_margin"] <= 0.30 and c["median_crossings"] >= 2
    assert "PEAK" in cycle.describe(c)


def test_a_trough_year_reads_as_a_trough():
    c = cycle.measure(_series([0.08, 0.12, 0.10, 0.03, 0.11, 0.13, 0.09, 0.12, 0.10, 0.01]), profile_cyclical=True)
    assert c["cyclical"] and c["position"] == "trough" and c["mid_cycle_margin"] > 0.09


def test_a_one_way_slide_is_structural_not_cyclical():
    c = cycle.measure(_series([0.30, 0.29, 0.31, 0.27, 0.24, 0.20, 0.15, 0.08, 0.02, -0.05]))
    assert c["structural_decline"] and not c["cyclical"]
    assert "structural decline" in cycle.describe(c)


def test_a_cyclical_profile_counts_even_when_the_window_is_calm_but_a_decline_still_wins():
    calm = cycle.measure(_series([0.10, 0.11, 0.10, 0.12, 0.11, 0.10, 0.11]), profile_cyclical=True)
    assert calm["cyclical"] and calm["cyclical_basis"] == "profile" and not calm["data_cyclical"]
    slide = cycle.measure(_series([0.30, 0.29, 0.31, 0.27, 0.24, 0.20, 0.15, 0.08, 0.02, -0.05]), profile_cyclical=True)
    assert not slide["cyclical"]


def test_swings_on_a_non_cyclical_profile_are_flagged_for_review_not_reverted():
    c = cycle.measure(_series([0.28, 0.30, 0.25, 0.13, 0.03, -0.19, 0.05, 0.27, 0.10, -0.04]))
    assert c["data_cyclical"] and not c["cyclical"] and c["review_cyclical"]
    assert "not mean-reverted" in cycle.describe(c)


def test_the_mid_cycle_margin_reads_the_last_ten_years():
    old_boom = [0.60] * 5 + [0.10, 0.12, 0.08, 0.11, 0.09, 0.10, 0.12, 0.08, 0.11, 0.10]
    c = cycle.measure(_series(old_boom, start=2011), profile_cyclical=True)
    assert c["n_years"] == 15 and c["mid_cycle_years"] == 10 and 0.08 <= c["mid_cycle_margin"] <= 0.12


def test_a_steady_compounder_is_not_cyclical_and_short_history_is_unscored():
    c = cycle.measure(_series([0.030, 0.031, 0.032, 0.033, 0.034, 0.035, 0.035]))
    assert not c["cyclical"] and not c["structural_decline"] and cycle.describe(c) is None
    assert cycle.measure(_series([0.1, 0.2, 0.1, 0.2])) is None


def test_an_impairment_year_does_not_drag_the_mid_cycle_margin():
    with_one_off = cycle.measure(_series([0.12, 0.10, 0.13, -0.60, 0.11, 0.14, 0.09, 0.12, 0.10, 0.13]))
    assert 0.10 <= with_one_off["mid_cycle_margin"] <= 0.13


def test_ebit_falls_back_to_ebitda_less_da():
    rows = [{"period": f"{2016 + i}", "revenue": 100.0, "ebitda": 100.0 * m + 5.0, "depreciation_and_amortization": -5.0}
            for i, m in enumerate([0.1, 0.2, 0.05, 0.15, 0.25, 0.02, 0.18])]
    assert cycle.measure(rows)["ebit_margins"][1] == 0.2


def test_the_forecast_margin_returns_to_mid_cycle_after_the_guided_years():
    import importlib.util
    from pathlib import Path
    from src.agents.analysis import guidance_forecast as gf
    spec = importlib.util.spec_from_file_location("_gft", Path(__file__).resolve().parent / "test_guidance_forecast.py")
    gft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gft)
    kw = dict(scenario="base", series=gft._SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025,
              shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    plain = gf.build_forecast(gft._BLOCK, **kw)
    blk = {**gft._BLOCK, "_mid_cycle_ebit_margin": 0.01, "_cycle_reversion_years": 3}
    cyc = gf.build_forecast(blk, **kw)
    T = plain["horizon_years"]
    pm = [r["ebit_margin"] for r in plain["rows"]]
    cm = [r["ebit_margin"] for r in cyc["rows"]]
    assert cm[:T] == pytest.approx(pm[:T])                         # the guided years are untouched
    assert cm[T + 2] == pytest.approx(0.01, abs=1e-6) and cm[-1] == pytest.approx(0.01, abs=1e-6)
    assert cyc["mid_cycle_reversion"]["to"] == 0.01 and any("mid-cycle" in f for f in cyc["flags"])
    assert plain["mid_cycle_reversion"] is None


def test_legs_swapped_onto_one_normalised_leg_merge_into_one_row():
    from src.agents.analysis.dcf_agent import _apply_pe_norm_swaps, _merge_named_rows, _CYCLE_PEAK_SWAPS
    rows = [{"name": "P/E (norm)", "weight": 0.45, "anchor": True}, {"name": "EV/EBITDA", "weight": 0.30},
            {"name": "Forward EV/EBITDA", "weight": 0.10}, {"name": "DCF", "weight": 0.15}]
    swaps = [{"from": f, "to": t} for f, t in _CYCLE_PEAK_SWAPS.items() if f in {r["name"] for r in rows}]
    out = _merge_named_rows(_apply_pe_norm_swaps(rows, swaps))
    assert [r["name"] for r in out] == ["P/E (norm)", "EV/EBITDA (norm)", "DCF"]
    assert out[1]["weight"] == pytest.approx(0.40) and out[0]["anchor"] and sum(r["weight"] for r in out) == pytest.approx(1.0)
    assert rows[1]["name"] == "EV/EBITDA"                           # the profile's own rows are untouched


def test_a_loss_making_mid_cycle_is_a_turnaround_not_a_cycle():
    # BA-shaped: a decade with the 2019-2024 crisis in it
    c = cycle.measure(_series([0.06, 0.07, 0.05, -0.02, -0.18, -0.07, -0.04, 0.01, -0.14, 0.05]), profile_cyclical=True)
    assert c["mid_cycle_margin"] <= 0 and c["loss_making_mid_cycle"] and not c["cyclical"]
    assert "turnaround question" in cycle.describe(c)


def test_a_price_inflated_peak_normalises_on_trend_revenue():
    rev = [20, 22, 30, 23, 21, 27, 31, 16, 25, 80]                 # memory-shaped: last year 3x its trend
    c = cycle.measure(_series([0.10, 0.30, 0.45, 0.25, 0.10, 0.20, 0.30, -0.35, 0.05, 0.70], rev=[float(x) for x in rev]),
                      profile_cyclical=True)
    assert c["position"] == "peak" and c["revenue_normalised"] and c["trend_revenue_ratio"] > 2.0
    calm = cycle.measure(_series([0.10, 0.30, 0.45, 0.25, 0.10, 0.20, 0.30, -0.35, 0.05, 0.70]), profile_cyclical=True)
    assert calm["position"] == "peak" and not calm["revenue_normalised"]          # flat revenue: margin only


def test_trend_revenue_is_a_log_linear_fit():
    tr = cycle.trend_revenue([{"revenue": 100.0 * 1.05 ** i} for i in range(10)])
    assert tr["growth"] == pytest.approx(0.05, abs=1e-9) and tr["ratio"] == pytest.approx(1.0)


def test_the_forecast_revenue_converges_to_trend_after_the_guided_years():
    import importlib.util
    from pathlib import Path
    from src.agents.analysis import guidance_forecast as gf
    spec = importlib.util.spec_from_file_location("_gft", Path(__file__).resolve().parent / "test_guidance_forecast.py")
    gft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gft)
    kw = dict(scenario="base", series=gft._SERIES, profile_name="Managed Care", sector="Healthcare", wacc=0.08, tgr=0.025,
              shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)
    plain = gf.build_forecast(gft._BLOCK, **kw)
    rev0 = plain["rows"][0]["revenue"] / (1.0 + plain["growth_schedule"][0])
    blk = {**gft._BLOCK, "_mid_cycle_ebit_margin": 0.02, "_cycle_reversion_years": 3,
           "_trend_revenue0": rev0 / 2.0, "_trend_revenue_growth": 0.03}
    cyc = gf.build_forecast(blk, **kw)
    tc = cyc["trend_revenue_convergence"]
    end = tc["after_year"] + tc["years"]
    assert cyc["rows"][end - 1]["revenue"] == pytest.approx(rev0 / 2.0 * 1.03 ** end, rel=1e-6)
    assert cyc["growth_schedule"][end] == pytest.approx(0.03 + (0.025 - 0.03) / 5.0, abs=1e-9)   # fade from trend growth
    assert plain["trend_revenue_convergence"] is None


def test_the_terminal_value_is_value_driver_and_growth_at_wacc_adds_nothing():
    """Owner, 2026-11-25: TV = NOPAT(T+1) x (1 - g/RONIC) / (WACC - g) with RONIC = WACC -> growth is value-neutral."""
    from src.agents.analysis import dcf_agent as d
    kw = dict(revenue_base=1000.0, fcf_margin_base=0.10, growth_rate=0.0, margin_delta_per_year=0.0, wacc=0.08,
              fcf_floor=-1.0, net_debt=0.0, shares=1.0, years=5)
    _, _, tv0, _ = d._project_dcf(tgr=0.0, **kw)
    _, _, tv2, _ = d._project_dcf(tgr=0.02, **kw)
    # growth at RONIC = WACC adds no value beyond next year's NOPAT: TV = NOPAT(T+1) / WACC
    assert tv2 == pytest.approx(tv0 * 1.02, rel=1e-9)
    _, _, tvm, _ = d._project_dcf(tgr=0.02, terminal_ronic_spread=0.04, **kw)
    assert tvm > tv0                                                # an owner-set moat spread makes growth valuable
    _, _, tvn, _ = d._project_dcf(tgr=0.0, terminal_nopat_ratio=1.25, **kw)
    assert tvn == pytest.approx(tv0 * 1.25, rel=1e-9)               # the guidance path's terminal year on NOPAT


def test_owner_earnings_charge_gross_sbc_whatever_the_buybacks():
    """Owner, 2026-11-25: SBC is an expense, buybacks a distribution -- no netting, no double-charged withholding."""
    from types import SimpleNamespace
    from src.agents.analysis.dcf_agent import _extract_annual_series
    li = [SimpleNamespace(report_period="2025-12-31", period="2025-12-31", revenue=1000.0, free_cash_flow=200.0,
                          stock_based_compensation=50.0, common_stock_repurchased=-300.0, currency="USD")]
    rows, _ = _extract_annual_series(li)
    assert rows[-1]["fcf_owner_earnings"] == pytest.approx(150.0)
