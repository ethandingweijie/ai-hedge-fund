"""Reported free cash flow is only projectable to the extent earnings explain it.

MELI, 2026-08-30: Mercado Pago's float — customer deposits and credit-book
movements running through operating cash flow — produced a reported FCF margin
of 37.3% against a 6.9% net margin. Held flat across ten years of compounding
revenue, `_project_dcf` projected **$59.6bn of annual free cash flow in year
10**, more than Alphabet earns today, from a company that earned $2.0bn last
year. Base IV printed at $18,401 against a $1,966 spot.

The gate is the owner-earnings identity read as a constraint:

    FCF = net income + D&A - capex - change in working capital

The first three terms are structural. The fourth is not — a working-capital
benefit only recurs while the balance sheet keeps growing, and at steady state
it stops contributing. Projecting it flat capitalises a financing flow as if it
were operating profit.

The gate caps DOWNWARD only, and only when it can say why.
"""

from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _CASH_CONVERSION_TOLERANCE,
    _projectable_fcf_margin_cap,
)


def _yr(rev, fcf, ni, capex=0.0, dep=0.0):
    return {"revenue": rev, "free_cash_flow": fcf, "net_income": ni,
            "capital_expenditure": -abs(capex),
            "depreciation_and_amortization": dep}


# ── The case that motivated it ───────────────────────────────────────────

def test_float_driven_cash_conversion_is_capped_to_earnings():
    """MELI's real FY22-25 figures. 4.6x excess over what earnings support."""
    series = [
        _yr(10.78e9, 2.48e9, 0.48e9, capex=0.46e9, dep=0.30e9),
        _yr(15.11e9, 4.63e9, 0.99e9, capex=0.51e9, dep=0.43e9),
        _yr(20.78e9, 7.06e9, 1.91e9, capex=0.86e9, dep=0.61e9),
        _yr(28.89e9, 10.77e9, 2.00e9, capex=1.34e9, dep=0.81e9),
    ]
    cap, trailing, _b = _projectable_fcf_margin_cap(series)
    # Aggregated over FY22-25: FCF 24.94 / revenue 75.56 = 33.0%; earnings
    # 5.38 / 75.56 = 7.1%, with no D&A add-back because capex (3.17) exceeds
    # D&A (2.15) over the window.
    assert trailing == pytest.approx(0.330, abs=0.01), "reported margin"
    assert cap == pytest.approx(0.071, abs=0.01), "earnings-supported margin"
    assert trailing > cap * _CASH_CONVERSION_TOLERANCE, "the gate must bind"


def test_the_gate_binds_hard_enough_to_matter():
    """A 30.6% margin capped to 6.6% is a 4.6x reduction in projected cash —
    the difference between $59.6bn and $12.9bn of year-10 FCF for MELI."""
    series = [_yr(28.89e9, 10.77e9, 2.00e9, capex=1.34e9, dep=0.81e9)]
    cap, trailing, _b = _projectable_fcf_margin_cap(series)
    assert trailing / cap > 4.0


# ── It must not fire on ordinary businesses ──────────────────────────────

@pytest.mark.parametrize("name, series", [
    # FCF tracks earnings — the ordinary case.
    ("clean", [_yr(1000, 100, 95, capex=50, dep=55)]),
    # Capex-heavy: net margin already carries the depreciation of that spend,
    # so the D&A add-back is deliberately floored at zero rather than negative.
    ("capex heavy", [_yr(1000, 20, 130, capex=400, dep=270)]),
    # Reported FCF below earnings — nothing to cap.
    ("fcf below ni", [_yr(1000, 40, 120, capex=90, dep=60)]),
])
def test_ordinary_businesses_are_left_alone(name, series):
    cap, trailing, _b = _projectable_fcf_margin_cap(series)
    assert trailing <= cap * _CASH_CONVERSION_TOLERANCE, f"{name} must not bind"


def test_heavy_depreciation_earns_a_genuinely_higher_cap():
    """The legitimate reason FCF exceeds earnings: past capex still
    depreciating while maintenance capex is low. That add-back is structural
    and must survive the gate."""
    series = [_yr(1000, 250, 80, capex=30, dep=180)]
    cap, trailing, _b = _projectable_fcf_margin_cap(series)
    assert cap == pytest.approx((80 + 150) / 1000, abs=1e-6)
    assert trailing <= cap * _CASH_CONVERSION_TOLERANCE


def test_the_tolerance_band_is_respected():
    """25% headroom keeps the gate off timing noise."""
    # 1.2x the cap — inside the band.
    inside = [_yr(1000, 120, 100, capex=50, dep=50)]
    cap_i, tr_i, _b = _projectable_fcf_margin_cap(inside)
    assert tr_i <= cap_i * _CASH_CONVERSION_TOLERANCE
    # 1.4x the cap — outside it.
    outside = [_yr(1000, 140, 100, capex=50, dep=50)]
    cap_o, tr_o, _b = _projectable_fcf_margin_cap(outside)
    assert tr_o > cap_o * _CASH_CONVERSION_TOLERANCE


# ── Robustness ───────────────────────────────────────────────────────────

def test_aggregated_not_averaged_so_a_loss_year_does_not_explode_it():
    """A per-year ratio goes to infinity on a zero-earnings year. The identity
    is stated over sums, so the gate aggregates."""
    series = [
        _yr(1000, 100, -50, capex=40, dep=45),
        _yr(1100, 120, 260, capex=44, dep=50),
    ]
    cap, trailing, _b = _projectable_fcf_margin_cap(series)
    assert cap is not None and cap > 0
    assert trailing == pytest.approx(220 / 2100, abs=1e-6)


def test_only_the_last_five_years_count():
    old = [_yr(100, 90, 5) for _ in range(6)]
    recent = [_yr(1000, 100, 95, capex=50, dep=55)]
    cap, trailing, _b = _projectable_fcf_margin_cap(old + recent)
    assert trailing < 0.5, "the ancient distorted years must have aged out"


@pytest.mark.parametrize("series", [
    [],
    [{"revenue": 0, "free_cash_flow": 10, "net_income": 5}],
    [{"revenue": 1000, "free_cash_flow": 100}],          # no earnings signal
    [{"revenue": 1000, "net_income": 100}],              # no cash signal
    [{"revenue": None, "free_cash_flow": None, "net_income": None}],
])
def test_missing_inputs_return_none_rather_than_a_wrong_cap(series):
    """The caller leaves the margin untouched on None. Silence is correct here
    — capping on a guess would be worse than not capping."""
    assert _projectable_fcf_margin_cap(series) == (None, None, "")


def test_a_negative_cap_is_never_applied():
    """A loss-making business yields a negative cap. The caller's `cap > 0`
    guard means the OE<=0 cascade owns that case, not this gate."""
    series = [_yr(1000, 50, -200, capex=100, dep=60)]
    cap, _, _b = _projectable_fcf_margin_cap(series)
    assert cap < 0


# ── The revenue-scale cap on the analyst-band path ───────────────────────
#
# The tiered cap (15% / 22% / 30% by revenue) mutates `growth_base`, which the
# analyst-band path never reads — it takes g straight from the band dict. Any
# name with analyst coverage escaped the tier entirely and kept only the flat
# +40% clamp. MELI carried g = 40% on a $28.9bn revenue base against a 15%
# tier, compounding to $194.9bn of year-10 revenue.

from src.agents.analysis.dcf_agent import _scale_analyst_bands_to_cap


def test_a_band_above_the_tier_is_scaled_down_to_it():
    """MELI: $28.9bn revenue puts it in the 15% tier; the band said 40%."""
    bands = {"bear": 0.30, "base": 0.40, "bull": 0.50}
    scaled, scale = _scale_analyst_bands_to_cap(bands, 0.15)
    assert scaled["base"] == pytest.approx(0.15)
    assert scale == pytest.approx(0.375)


def test_the_analyst_dispersion_survives_the_cap():
    """Scaled, not clipped — clipping every scenario to the cap would erase
    the bear/base/bull spread the dispersion actually expresses, and the CAGR
    path does not do that either (its multipliers carry bull above the tier)."""
    bands = {"bear": 0.30, "base": 0.40, "bull": 0.50}
    scaled, _ = _scale_analyst_bands_to_cap(bands, 0.15)
    assert scaled["bear"] < scaled["base"] < scaled["bull"]
    # Ratios preserved exactly.
    assert scaled["bull"] / scaled["base"] == pytest.approx(0.50 / 0.40)
    assert scaled["bear"] / scaled["base"] == pytest.approx(0.30 / 0.40)
    # Bull is still allowed above the tier, mirroring the CAGR path.
    assert scaled["bull"] > 0.15


def test_non_scenario_keys_are_left_alone():
    """`analyst_count` lives in the same dict and the dispersion guard reads
    it — scaling it once made that guard unfirable."""
    bands = {"bear": 0.30, "base": 0.40, "bull": 0.50, "analyst_count": 26}
    scaled, _ = _scale_analyst_bands_to_cap(bands, 0.15)
    assert scaled["analyst_count"] == 26


@pytest.mark.parametrize("bands, cap", [
    ({"bear": 0.08, "base": 0.12, "bull": 0.16}, 0.15),   # under the tier
    ({"bear": 0.10, "base": 0.15, "bull": 0.20}, 0.15),   # exactly at it
    ({"bear": 0.5, "base": 0.8, "bull": 1.0}, 1.0),       # sub-$1bn, no tier
])
def test_a_band_within_the_tier_is_untouched(bands, cap):
    scaled, scale = _scale_analyst_bands_to_cap(bands, cap)
    assert scale is None
    assert scaled == bands


@pytest.mark.parametrize("bands, cap", [
    (None, 0.15),
    ({}, 0.15),
    ({"bear": 0.3, "bull": 0.5}, 0.15),        # no base to anchor the scale
    ({"base": 0.0}, 0.15),
    ({"base": 0.40}, 0.0),                     # nonsensical cap
    ({"base": 0.40}, -1.0),
])
def test_degenerate_inputs_pass_through_unscaled(bands, cap):
    scaled, scale = _scale_analyst_bands_to_cap(bands, cap)
    assert scale is None
    assert scaled == bands


def test_the_cap_only_ever_reduces_growth():
    for base in (0.16, 0.25, 0.40, 1.00):
        scaled, _ = _scale_analyst_bands_to_cap({"base": base}, 0.15)
        assert scaled["base"] <= base


# ── The cap basis: measured beats inferred ───────────────────────────────
#
# Backtested against realised owner earnings over 8 gate firings, mean
# absolute error was ex-working-capital 5.96pp, raw 7.21pp, earnings cap
# 8.11pp — and on businesses that stayed healthy the earnings cap was off by
# 9.2pp against ex-WC's 2.1pp. It is a haircut, not an estimator. So the
# disclosed working-capital line leads and the earnings cap backstops.

def _wc_yr(rev, fcf, ni, dwc=None, capex=0.0, dep=0.0):
    row = _yr(rev, fcf, ni, capex=capex, dep=dep)
    if dwc is not None:
        row["change_in_working_capital"] = dwc
    return row


def test_the_disclosed_working_capital_line_is_preferred():
    """It IS the non-recurring term, measured rather than inferred."""
    series = [_wc_yr(100.0, 30.0, 5.0, dwc=15.0) for _ in range(4)]
    cap, trailing, basis = _projectable_fcf_margin_cap(series)
    assert basis == "ex-working-capital"
    assert cap == pytest.approx(0.15)
    assert trailing == pytest.approx(0.30)


def test_it_falls_back_when_the_line_is_absent():
    series = [_wc_yr(100.0, 30.0, 5.0) for _ in range(4)]
    cap, _t, basis = _projectable_fcf_margin_cap(series)
    assert basis == "earnings-supported"
    assert cap == pytest.approx(0.05)


def test_one_year_of_the_line_is_a_data_point_not_a_basis():
    """Requires most of the window, or a single disclosed year would swing
    the whole cap."""
    series = [_wc_yr(100.0, 30.0, 5.0) for _ in range(3)]
    series.append(_wc_yr(100.0, 30.0, 5.0, dwc=15.0))
    _c, _t, basis = _projectable_fcf_margin_cap(series)
    assert basis == "earnings-supported"


def test_a_working_capital_outflow_raises_the_cap():
    """Sign matters. A build in working capital depresses reported FCF, so
    removing it must move the projectable margin UP."""
    series = [_wc_yr(100.0, 20.0, 5.0, dwc=-10.0) for _ in range(4)]
    cap, trailing, basis = _projectable_fcf_margin_cap(series)
    assert basis == "ex-working-capital"
    assert cap == pytest.approx(0.30) and cap > trailing


def test_the_basis_is_reported_so_the_flag_can_name_it():
    """The gate writes the basis into both the flag and gate_evaluations; an
    unexplained cap is not auditable."""
    import inspect

    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent)
    assert '"basis": _cc_basis,' in src
    assert "_cc_basis" in src


# ── The deep-cut observation (a reverted suppression rule) ───────────────
#
# Retaining <40% of the trailing margin shipped as a SUPPRESSION rule and was
# reverted the same day. On the 141 dates that chose it, it declined 9 false
# alarms and 0 correct firings. On 188 held-out dates it declined 0 false
# alarms and 8 correct ones — a perfect inversion, because every held-out
# firing it blocked was a financial (MUFG raw 167%, CCB 86%, Ping An 43%),
# where reported FCF is meaningless and a deep cut is the whole point.
# Combined it dropped 8 wins and 9 losses: it discriminates nothing.
#
# These tests pin the revert so the rule cannot creep back as a gate.

from src.agents.analysis.dcf_agent import _DEEP_CUT_OBSERVATION_FRACTION


def test_a_deep_cut_is_recorded_but_never_suppresses():
    """The regression these tests exist for: this must not gate the firing."""
    import inspect

    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent)
    assert "_cc_deep_cut" in src, "the observation should still be computed"
    assert '"deep_cut": bool(_cc_deep_cut),' in src, "and recorded"
    assert "not _cc_too_deep" not in src, "but must NOT gate the firing"
    assert "_MIN_RETAINED_MARGIN_FRACTION" not in src, "suppression is reverted"


def test_financials_are_why_the_suppression_was_wrong():
    """A bank's reported FCF margin is dominated by deposit and lending flows,
    so the cap SHOULD cut it hard. MUFG's trailing margin was 167%; a rule
    that refused cuts deeper than 60% blocked exactly the firings that were
    right."""
    bank = [_wc_yr(100.0, 167.0, 8.0, dwc=150.0) for _ in range(4)]
    cap, trailing, basis = _projectable_fcf_margin_cap(bank)
    assert trailing > 1.0, "reported FCF margin above 100% of revenue"
    assert cap < _DEEP_CUT_OBSERVATION_FRACTION * trailing, "a deep cut"
    assert cap < trailing, "and it must still cap downward"


# ── Observed, not applied ────────────────────────────────────────────────
#
# Backtested over 329 ticker-dates in two independent samples, the cap is
# validated on financials (10H/1F, Beta 0.85) and reliably wrong on
# marketplaces (7H/19F, Beta 0.29) — both replicated. In production those
# populations are inverted against where it can act: every financial resolves
# to a multiples-only blend (GGM, Residual Income, P/TBV, P/E norm, Excess
# Capital) with weight_dcf = 0.0, while marketplaces carry 0.45-0.80. The
# gate's effect sits precisely where it is wrong.

def test_the_cap_is_recorded_but_does_not_move_the_margin():
    """The regression this file now exists to prevent. Re-applying the cap
    needs new evidence, not a refactor that quietly restores it."""
    import inspect

    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent)
    assert '"applied": False,' in src, "the telemetry must say it did not act"
    assert "fcf_margin_base = _cc_cap" not in src, (
        "the cap must not be assigned back onto the projection margin"
    )
    assert "OBSERVED, NOT APPLIED." in src, "and the reason must be recorded"


def test_the_observation_still_carries_both_paths():
    """Turning it off must not cost the evidence — the learning loop needs the
    (A, B) pair to keep scoring the cap it is no longer applying."""
    import inspect

    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent)
    assert '"raw_input_path_a"' in src and '"gated_output_path_b"' in src
    assert '"basis": _cc_basis,' in src


def test_the_helper_still_computes_so_the_backtest_keeps_working():
    """`_projectable_fcf_margin_cap` is now consumed by the harness rather
    than by the engine. It must keep returning a real cap."""
    series = [_wc_yr(100.0, 30.0, 5.0, dwc=15.0) for _ in range(4)]
    cap, trailing, basis = _projectable_fcf_margin_cap(series)
    assert cap == pytest.approx(0.15) and trailing == pytest.approx(0.30)
    assert basis == "ex-working-capital"
