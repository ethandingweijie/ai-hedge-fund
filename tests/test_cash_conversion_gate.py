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
    cap, trailing = _projectable_fcf_margin_cap(series)
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
    cap, trailing = _projectable_fcf_margin_cap(series)
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
    cap, trailing = _projectable_fcf_margin_cap(series)
    assert trailing <= cap * _CASH_CONVERSION_TOLERANCE, f"{name} must not bind"


def test_heavy_depreciation_earns_a_genuinely_higher_cap():
    """The legitimate reason FCF exceeds earnings: past capex still
    depreciating while maintenance capex is low. That add-back is structural
    and must survive the gate."""
    series = [_yr(1000, 250, 80, capex=30, dep=180)]
    cap, trailing = _projectable_fcf_margin_cap(series)
    assert cap == pytest.approx((80 + 150) / 1000, abs=1e-6)
    assert trailing <= cap * _CASH_CONVERSION_TOLERANCE


def test_the_tolerance_band_is_respected():
    """25% headroom keeps the gate off timing noise."""
    # 1.2x the cap — inside the band.
    inside = [_yr(1000, 120, 100, capex=50, dep=50)]
    cap_i, tr_i = _projectable_fcf_margin_cap(inside)
    assert tr_i <= cap_i * _CASH_CONVERSION_TOLERANCE
    # 1.4x the cap — outside it.
    outside = [_yr(1000, 140, 100, capex=50, dep=50)]
    cap_o, tr_o = _projectable_fcf_margin_cap(outside)
    assert tr_o > cap_o * _CASH_CONVERSION_TOLERANCE


# ── Robustness ───────────────────────────────────────────────────────────

def test_aggregated_not_averaged_so_a_loss_year_does_not_explode_it():
    """A per-year ratio goes to infinity on a zero-earnings year. The identity
    is stated over sums, so the gate aggregates."""
    series = [
        _yr(1000, 100, -50, capex=40, dep=45),
        _yr(1100, 120, 260, capex=44, dep=50),
    ]
    cap, trailing = _projectable_fcf_margin_cap(series)
    assert cap is not None and cap > 0
    assert trailing == pytest.approx(220 / 2100, abs=1e-6)


def test_only_the_last_five_years_count():
    old = [_yr(100, 90, 5) for _ in range(6)]
    recent = [_yr(1000, 100, 95, capex=50, dep=55)]
    cap, trailing = _projectable_fcf_margin_cap(old + recent)
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
    assert _projectable_fcf_margin_cap(series) == (None, None)


def test_a_negative_cap_is_never_applied():
    """A loss-making business yields a negative cap. The caller's `cap > 0`
    guard means the OE<=0 cascade owns that case, not this gate."""
    series = [_yr(1000, 50, -200, capex=100, dep=60)]
    cap, _ = _projectable_fcf_margin_cap(series)
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
