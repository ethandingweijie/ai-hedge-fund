"""Consumer Discretionary archetype gates: NKE, ONON, EL, 2020.HK.

This module is the brief's test suite, landed VERBATIM as supplied, plus an
engine-facing section underneath it. The two halves answer different questions
and the split is deliberate — see the banner at Section 0 before reading a pass
count as coverage.

Run:
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
      -m pytest tests/test_consumer_discretionary_gates.py -v
"""
import pytest
import math
import re
import sys

# ----------------------------------------------------------------------
# Gate Invariant 1: Inventory Bullwhip & Days Sales of Inventory (NKE)
# ----------------------------------------------------------------------
def test_gate_inventory_bullwhip_triggers_margin_cap():
    """
    Asserts that an expansion of Days Sales of Inventory (DSI) > 25 days over
    historical median forces a terminal EBIT margin cap (reflecting markdown risk).
    """
    # NKE-like scenario: Inventory swells from $5.2B to $8.4B while sales flatline
    history = [
        {"revenue": 51_000, "cogs": 28_000, "inventory": 8_400, "ebit": 5_800},  # Current DSI: 109.5d
        {"revenue": 50_000, "cogs": 27_000, "inventory": 5_400, "ebit": 6_200},  # Past DSI: 73.0d
        {"revenue": 46_000, "cogs": 25_000, "inventory": 5_100, "ebit": 6_500},  # Past DSI: 74.5d
        {"revenue": 44_000, "cogs": 24_000, "inventory": 4_800, "ebit": 6_100},  # Past DSI: 73.0d
    ]

    current_dsi = (history[0]["inventory"] / history[0]["cogs"]) * 365.0
    past_dsi = [(h["inventory"] / h["cogs"]) * 365.0 for h in history[1:]]
    median_dsi = sorted(past_dsi)[len(past_dsi) // 2]

    dsi_expansion = current_dsi - median_dsi
    assert dsi_expansion > 25.0, f"DSI expanded by {dsi_expansion:.1f} days"

    # Engine logic: Cap terminal margin to the lowest margin of the trailing 3 years
    trailing_3y_margins = [h["ebit"] / h["revenue"] for h in history[:3]]
    capped_terminal_margin = min(trailing_3y_margins)

    # Verify that the engine does not extrapolate the prior peak margin (14.1%)
    assert capped_terminal_margin <= 0.115
    assert capped_terminal_margin < (history[2]["ebit"] / history[2]["revenue"])


# ----------------------------------------------------------------------
# Gate Invariant 2: Hyper-Growth Reinvestment Penalty (ONON)
# ----------------------------------------------------------------------
def test_gate_high_growth_requires_capital_reinvestment():
    """
    Asserts that brands growing > 15% cannot produce FCF without an explicit
    incremental reinvestment charge (Sales-to-Capital = 1.85x).
    """
    # ONON-like scenario: Compounding top line by 28%
    rev_base = 2_000.0
    g1 = 0.28
    projected_delta_rev = rev_base * g1  # $560M incremental revenue
    operating_cash_flow_unadjusted = 380.0  # Reported strong operating cash flow

    # Invariant: If g1 > 0.15, free cash flow must deduct delta_rev / sales_to_capital
    sales_to_capital_ratio = 1.85
    reinvestment_charge = projected_delta_rev / sales_to_capital_ratio  # $302.7M

    adjusted_fcf = operating_cash_flow_unadjusted - reinvestment_charge

    assert g1 > 0.15
    assert reinvestment_charge > 0
    # Adjusted cash flow must reflect the working capital and capex burden of 28% growth
    assert adjusted_fcf == pytest.approx(77.3, rel=1e-2)
    assert adjusted_fcf < (operating_cash_flow_unadjusted * 0.30)


# ----------------------------------------------------------------------
# Gate Invariant 3: Trough/Peak Margin Normalization (EL)
# ----------------------------------------------------------------------
def test_gate_trough_margin_routes_to_normalized_ebit():
    """
    Asserts that when current operating margin deviates > 40% from the 5y median
    (e.g., travel retail channel shock), valuation bypasses unadjusted forward P/E.
    """
    # EL-like scenario: Operating margin collapses to 5.2% vs historical 14.5%
    historical_margins = [0.052, 0.138, 0.152, 0.145, 0.141]  # Median = 0.141
    current_margin = historical_margins[0]
    median_margin = sorted(historical_margins)[len(historical_margins) // 2]

    deviation = abs(current_margin - median_margin) / median_margin
    assert deviation > 0.40, f"Margin deviation is {deviation:.1%}"

    # Engine Decision Gate: If True, do NOT use current year EPS for forward multiples
    route_to_normalized = deviation > 0.40
    assert route_to_normalized is True

    # Assert normalized EBIT baseline replaces depressed trough EBIT
    current_revenue = 15_600.0
    trough_ebit = current_revenue * current_margin      # $811.2M
    normalized_ebit = current_revenue * median_margin  # $2,199.6M

    assert normalized_ebit > (trough_ebit * 2.5)


# ----------------------------------------------------------------------
# Gate Invariant 4: Associate Equity Add-Back & FX Parity (2020.HK)
# ----------------------------------------------------------------------
def test_gate_associate_equity_transparency_and_fx_parity():
    """
    Asserts that unconsolidated JV/associates (Amer Sports / Arc'teryx) > 10% of equity
    are explicitly added to EV and converted using consistent listing FX rates.
    """
    # Anta (2020.HK) Balance Sheet (reported in RMB)
    balance_sheet_rmb = {
        "market_cap_hkd": 230_000.0,
        "total_equity_rmb": 48_000.0,
        "investments_in_associates_rmb": 9_800.0,  # ~20.4% of book equity
        "total_debt_rmb": 12_000.0,
        "cash_and_sti_rmb": 28_000.0,
        "diluted_shares": 2_830.0,
    }
    fx_hkd_rmb = 0.92  # 1 HKD = 0.92 RMB (or 1 RMB = 1.087 HKD)

    # Invariant: Associate book value check
    associate_ratio = (
        balance_sheet_rmb["investments_in_associates_rmb"] /
        balance_sheet_rmb["total_equity_rmb"]
    )
    assert associate_ratio > 0.10, "Associates are material and must be added back"

    # Enterprise Value to Equity conversion with associate equity add-back
    core_operating_ev_rmb = 180_000.0  # From DCF + Multiple legs
    net_cash_rmb = balance_sheet_rmb["cash_and_sti_rmb"] - balance_sheet_rmb["total_debt_rmb"]
    associate_addback_rmb = balance_sheet_rmb["investments_in_associates_rmb"]

    total_equity_value_rmb = core_operating_ev_rmb + net_cash_rmb + associate_addback_rmb

    # Convert to listing currency (HKD) per share
    total_equity_value_hkd = total_equity_value_rmb / fx_hkd_rmb
    implied_iv_per_share_hkd = total_equity_value_hkd / balance_sheet_rmb["diluted_shares"]

    # Verify associate add-back provides an explicit ~HK$3.77/share equity contribution
    associate_contribution_per_share = (
        (associate_addback_rmb / fx_hkd_rmb) / balance_sheet_rmb["diluted_shares"]
    )
    assert pytest.approx(associate_contribution_per_share, rel=1e-2) == 3.76
    assert implied_iv_per_share_hkd > (core_operating_ev_rmb / fx_hkd_rmb / balance_sheet_rmb["diluted_shares"])


# ----------------------------------------------------------------------
# Gate Invariant 5: Gross Margin Qualification for Multiples
# ----------------------------------------------------------------------
def test_terminal_multiple_gross_margin_qualification():
    """
    Asserts that consumer discretionary names cannot capture luxury terminal multiples
    (e.g., 25x+) unless gross margins clear the luxury floor (> 65%).
    """
    profiles = {
        "ONON": {"gross_margin": 0.595, "peer_pe_candidate": 28.0},
        "NKE":  {"gross_margin": 0.445, "peer_pe_candidate": 25.0},
        "EL":   {"gross_margin": 0.720, "peer_pe_candidate": 30.0},
    }

    LUXURY_GROSS_MARGIN_FLOOR = 0.65

    for ticker, data in profiles.items():
        gm = data["gross_margin"]
        target_pe = data["peer_pe_candidate"]

        if gm < LUXURY_GROSS_MARGIN_FLOOR:
            # Scaled down based on pricing power tier
            qualified_pe = min(target_pe, 18.0 + (gm / LUXURY_GROSS_MARGIN_FLOOR) * 4.0)
            assert qualified_pe <= 22.0, f"{ticker} with GM={gm:.1%} must not receive luxury multiple"
        else:
            # Premium luxury multiple allowed (EL with 72% gross margin)
            qualified_pe = target_pe
            assert qualified_pe >= 25.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — read this before reading any pass count above
# ══════════════════════════════════════════════════════════════════════════════
#
# The five tests above are the brief's module, landed verbatim. They import
# `pytest` and `math` and nothing else: no engine module, no fixture, no replay.
# Every number in them is computed from a literal defined three lines earlier and
# asserted against another literal. They pass in 0.08s because nothing runs.
#
# That is not a defect in the arithmetic — the arithmetic is correct and worth
# having as the specification of what each gate should do. It is a defect in what
# a green run means. "All 5 assertions will pass, guaranteeing that inventory
# spikes (NKE), hyper-growth free lunches (ONON), trough margin traps (EL), and
# associate equity omissions (2020.HK) are barred from corrupting intrinsic
# valuations" is not true of the module as supplied, and cannot be: a test that
# never calls the engine cannot constrain it. The five gates it describes —
# `GATE_INVENTORY_STRESS`, a sales-to-capital reinvestment deduction, a
# trough/peak routing rule, an associates-over-10%-of-equity add-back, and a
# luxury gross-margin floor — do not exist in this codebase. Sections A-F below
# measure exactly what the engine does instead, and each brief invariant that the
# engine does not implement is an `xfail(strict=True)`: reported on every run,
# counted, and turning the suite RED the moment someone implements it, so the
# mark cannot outlive the gap.
#
# The brief's integration sketch is also not insertable as written. It says to
# add, inside the `_project_dcf` loop:
#
#     fcf_t = (ebit_t * (1.0 - effective_tax_rate)) + da_t - capex_t - reinvestment_deduction
#
# `_project_dcf` has no `ebit_t`, no `da_t`, no `capex_t`, no
# `effective_tax_rate` and no `overrides` parameter. Its cash flow is
# `fcf_t = rev_t * margin_t` — a margin on revenue, not a built-up cash flow —
# so the sketch is a rewrite of the projection engine, not an insertion into it.
# Section B pins that as a measured fact rather than an opinion.


def test_the_briefs_own_module_never_touches_the_engine():
    """The claim in the banner, checked rather than asserted.

    If a future edit wires one of the five specification tests to a real engine
    call, this fails and the banner has to be updated — which is the point: the
    banner is the thing a reader will otherwise skim past and mis-read as
    coverage.
    """
    import inspect as _inspect

    src = _inspect.getsource(sys.modules[__name__])
    head = src.split("# SECTION 0")[0]
    for forbidden in ("dcf_agent", "sector_profiles", "_project_dcf",
                      "classify_valuation_profile", "golden_replay",
                      "from src", "import src"):
        assert forbidden not in head, (
            f"the verbatim section now references {forbidden!r}; the banner "
            f"claim that it never touches the engine is stale")


# ══════════════════════════════════════════════════════════════════════════════
# Engine-facing sections. Everything below calls or reads the real engine, and
# every number in it was produced by that call — none is transcribed from the
# brief. Where the brief's invariant is not implemented, the test says so with
# `xfail(strict=True)` rather than passing quietly.
# ══════════════════════════════════════════════════════════════════════════════
import inspect                                                # noqa: E402

from src.agents.analysis import dcf_agent                     # noqa: E402
from src.data.sector_profiles import (                        # noqa: E402
    DAMODARAN_SECTOR_MAP,
    INDUSTRY_VALUATION_PROFILES,
    TICKER_SECTOR_LOOKUP,
    classify_valuation_profile,
)

#: The four archetypes, at the (revenue CAGR, FCF margin, debt/equity) the
#: classifier is actually order-sensitive on. Every row is re-derived from the
#: real function in the test below; this dict is the input side only.
ARCHETYPES: dict[str, tuple[float, float, float]] = {
    "NKE":      (0.03,  0.10, 0.6),
    "ONON":     (0.30,  0.12, 0.3),
    "EL":       (-0.02, 0.10, 0.5),
    "02020.HK": (0.12,  0.18, 0.4),
}

_ENGINE_SRC: str | None = None


def _engine_src() -> str:
    global _ENGINE_SRC
    if _ENGINE_SRC is None:
        _ENGINE_SRC = inspect.getsource(dcf_agent)
    return _ENGINE_SRC


def _consumer_profiles() -> dict:
    return INDUSTRY_VALUATION_PROFILES["Consumer"]


def _methods(profile: str) -> dict[str, dict]:
    """`{method name: entry}` for a Consumer profile."""
    return {m["name"]: m for m in _consumer_profiles()[profile]["methods"]}


# ── A. NKE — inventory bullwhip ──────────────────────────────────────────────


def test_the_engine_never_turns_inventory_into_days():
    """`inventory` reaches the engine at exactly three sites, none of them a ratio.

    Measured, not assumed. The three are: two identical line-item request lists
    (the annual-series request and the ROIC input request) and one row-builder
    assignment. After that the value is only ever added to receivables and
    subtracted-from-payables inside ROIC's operating-working-capital term. It is
    never divided by cost of revenue, never compared against its own history, and
    never converted into a margin. So the input the brief's Gate 1 needs is
    present in the data and unconsumed by the logic — the cheapest of the five
    gaps to close, because nothing has to be fetched.
    """
    src = _engine_src()
    for absent in ("days_sales_inventory", "inventory_days", "bullwhip",
                   "markdown", "GATE_INVENTORY_STRESS", "inventory_stress"):
        assert absent not in src, f"{absent!r} exists now; this test is stale"
    lines = [ln for ln in src.splitlines() if '"inventory"' in ln]
    assert len(lines) == 3, [ln.strip() for ln in lines]
    # Classified by shape, not by a character prefix — a prefix short enough to
    # survive a re-indent is also short enough to match the wrong line.
    shapes = sorted(
        "assignment" if ln.strip().startswith('"inventory":') else
        "request" if ln.strip().startswith('"inventory",') else "other"
        for ln in lines)
    assert shapes == ["assignment", "request", "request"], shapes


def test_gate_vocabulary_is_closed_and_has_seven_members():
    """The set of gate ids the engine can emit, extracted from its own source.

    Seven. This is what makes "there is no inventory gate" a fact about the
    engine rather than a fact about the brief's prose — and it means adding one
    is a deliberate act that turns this test red until the list is updated.
    """
    emitted = sorted(set(re.findall(r'"gate_id":\s*"(GATE_[A-Z_]+)"', _engine_src())))
    assert emitted == [
        "GATE_BALANCE_SHEET_FINANCIAL",
        "GATE_CASH_CONVERSION",
        "GATE_CYCLICAL_PEAK_CONSENSUS",
        "GATE_DETERMINISTIC_KPI_PRECEDENCE",
        "GATE_GROWTH_CAGR_DIVERGENCE",
        "GATE_PT_IV_BAND",
        "GATE_REVENUE_SCALE_CAP",
    ], emitted
    assert not any("INVENT" in g for g in emitted)


def test_the_briefs_nike_arithmetic_is_right_about_a_gate_that_is_not_there():
    """The DSI expansion in the brief's own scenario is 36.5 days — and inert.

    Recomputed here so the number is checked rather than quoted: current DSI is
    8400/28000 × 365 = 109.5d against a trailing median of 73.0d, an expansion
    of 36.5d, well past the brief's 25-day trigger. Nothing in the engine reads
    it. The margin that reaches the terminal value is
    `fcf_margin_base + margin_delta`, clamped to `[fcf_floor, _FCF_MARGIN_CAP]`
    — an FCF margin with a Consumer floor of **+0.02**, not an EBIT margin
    capped at a trailing-3y low. For NKE's archetype inputs the floor is above
    the brief's 11.4% cap in the wrong direction entirely: it prevents the
    margin from falling, where the gate is meant to prevent it from staying high.
    """
    from src.data.sector_profiles import FCF_MARGIN_FLOOR

    hist = [(51_000, 28_000, 8_400, 5_800), (50_000, 27_000, 5_400, 6_200),
            (46_000, 25_000, 5_100, 6_500), (44_000, 24_000, 4_800, 6_100)]
    dsi = [(inv / cogs) * 365.0 for _, cogs, inv, _ in hist]
    past = sorted(dsi[1:])
    expansion = dsi[0] - past[len(past) // 2]
    assert expansion == pytest.approx(36.5, abs=0.05), expansion
    assert expansion > 25.0

    cap = min(ebit / rev for rev, _, _, ebit in hist[:3])
    assert cap == pytest.approx(0.11373, abs=1e-5), cap

    # And what the engine actually floors a Consumer margin at:
    assert FCF_MARGIN_FLOOR["Consumer"] == 0.02
    assert FCF_MARGIN_FLOOR["Consumer"] < cap, (
        "if the Consumer floor ever rose above the brief's cap, the two "
        "mechanisms would start to interact and this note would be wrong")


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED. No inventory-days computation exists anywhere in the "
    "engine, so no DSI expansion can cap a terminal margin — see "
    "test_the_engine_never_turns_inventory_into_days. The terminal margin is an "
    "FCF margin clamped to [FCF_MARGIN_FLOOR[sector], 0.60], and for Consumer "
    "the floor is +0.02, which pushes the margin UP where this gate pushes it "
    "DOWN. Closing it needs a DSI series over the recorded rows (inventory and "
    "cost of revenue are both already requested) plus an eighth gate id, which "
    "will turn test_gate_vocabulary_is_closed_and_has_seven_members red until "
    "the list is updated."))
def test_an_inventory_buildup_caps_the_terminal_margin():
    hist = [
        {"revenue": 51_000, "cost_of_revenue": 28_000, "inventory": 8_400, "ebit": 5_800},
        {"revenue": 50_000, "cost_of_revenue": 27_000, "inventory": 5_400, "ebit": 6_200},
        {"revenue": 46_000, "cost_of_revenue": 25_000, "inventory": 5_100, "ebit": 6_500},
        {"revenue": 44_000, "cost_of_revenue": 24_000, "inventory": 4_800, "ebit": 6_100},
    ]
    fn = getattr(dcf_agent, "_inventory_stress_days", None)
    assert fn is not None, "no inventory-stress helper exists"
    assert fn(hist) > 25.0


# ── B. ONON — hyper-growth capital drain ─────────────────────────────────────


def test_project_dcf_charges_no_capital_for_growth():
    """The measured core of the brief's Gate 2, against the real projector.

    A 28%-a-year grower at a flat 19% FCF margin — ONON's archetype. Every
    year's cash flow is exactly `revenue × margin`: year 1 is 2560 × 0.19 =
    486.4, year 3 is 4194.304 × 0.19 = 796.91776. The margin does not move, and
    nothing is deducted for the working capital or the capacity the growth
    requires. Under the brief's rule year 1 would carry a 560/1.85 = 302.7
    charge against a 560 revenue increment — a 62% cut to that year's cash flow
    — and the engine applies none of it.

    This is the largest of the five gaps by valuation impact, because it
    compounds into the terminal value: for these inputs the terminal value is
    86.0% of the total (measured 0.8595), so an un-charged increment is not a
    year-one rounding difference, it is most of the answer.
    """
    equity_ps, pv_fcf, pv_tv, rows = dcf_agent._project_dcf(
        2000.0,      # revenue_base
        0.19,        # fcf_margin_base
        0.28,        # growth_rate — ONON-like
        0.0,         # margin_delta_per_year
        0.09, 0.025, -0.05, 0.0, 100.0,
        years=3,
    )
    assert len(rows) == 3
    assert equity_ps == pytest.approx(112.89488, abs=1e-4)
    prev = 2000.0
    for r in rows:
        assert r["fcf"] == pytest.approx(r["revenue"] * r["fcf_margin"], rel=1e-12)
        assert r["fcf_margin"] == pytest.approx(0.19, rel=1e-12), \
            "the margin moved, so something other than a flat margin is in play"
        assert r["revenue"] == pytest.approx(prev * 1.28, rel=1e-12)
        prev = r["revenue"]
    assert pv_tv / (pv_fcf + pv_tv) == pytest.approx(0.8595, abs=1e-4)


def test_the_projector_has_none_of_the_inputs_the_briefs_patch_needs():
    """The integration sketch cannot be inserted as written.

    It says to compute, inside the loop:

        fcf_t = (ebit_t * (1.0 - effective_tax_rate)) + da_t - capex_t
                - reinvestment_deduction

    gated on `overrides["apply_reinvestment_deduction"]`. The real signature has
    no `overrides`, no tax rate and no per-year EBIT, D&A or capex. It takes a
    revenue base and an FCF MARGIN and never builds a cash flow up from
    components, so the change is a rewrite of the projection engine and of
    everything calibrated against it — not a loop insertion.

    Worth naming what that costs: the projector's margin-only shape is what lets
    a single `fcf_margin_base` plus a scenario multiplier drive all ten years.
    A component build-up needs capex, D&A and working-capital series per year,
    which are requested for the historical rows but never projected forward.
    """
    params = list(inspect.signature(dcf_agent._project_dcf).parameters)
    assert params == [
        "revenue_base", "fcf_margin_base", "growth_rate", "margin_delta_per_year",
        "wacc", "tgr", "fcf_floor", "net_debt", "shares", "years",
        "growth_schedule", "wacc_schedule", "margin_delta_absolute",
        "include_terminal",
    ], params
    for absent in ("overrides", "effective_tax_rate", "capex", "da",
                   "depreciation", "sales_to_capital", "ebit"):
        assert absent not in params, f"{absent!r} is now a parameter"
    src = _engine_src()
    assert "sales_to_capital" not in src
    assert "reinvestment_deduction" not in src


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED, and not implementable as sketched — the projector has no "
    "EBIT, D&A, capex or overrides parameter, so there is nothing to deduct "
    "from. The two existing mechanisms that bound hyper-growth bound the RATE, "
    "not the capital: GATE_REVENUE_SCALE_CAP caps the growth rate at scale, and "
    "GATE_GROWTH_CAGR_DIVERGENCE caps it against history. Neither charges the "
    "cash flow, so a name that clears both still projects a free lunch."))
def test_a_twenty_eight_percent_grower_pays_delta_rev_over_sales_to_capital():
    _, _, _, rows = dcf_agent._project_dcf(
        2000.0, 0.19, 0.28, 0.0, 0.09, 0.025, -0.05, 0.0, 100.0, years=3)
    y1 = rows[0]
    delta_rev = y1["revenue"] - 2000.0
    assert delta_rev == pytest.approx(560.0, rel=1e-9)
    charge = delta_rev / 1.85
    assert charge == pytest.approx(302.70, abs=0.05)
    assert y1["fcf"] == pytest.approx(
        y1["revenue"] * y1["fcf_margin"] - charge, rel=1e-6)


# ── C. EL — trough/peak margin distortion ────────────────────────────────────


def test_el_changes_profile_with_the_cycle_and_lands_on_trailing_e_at_the_trough():
    """The classifier is the brief's Gate 3, and it points the wrong way.

    Run through the real function at EL's three cycle positions. At a trough —
    the exact state the brief says needs protecting — EL routes to
    `Household / Personal`, whose anchor is plain `P/E` at weight 0.40. At a
    peak it routes to `Luxury Goods`, whose anchor is `P/E (Premium)` at 0.50.
    Both of those method names dispatch to the SAME trailing-12m branch (see
    `test_pe_premium_and_pe_share_the_trailing_branch`), so EL is anchored on
    unadjusted trailing earnings at both ends of the cycle, at 40% and 50% of
    the blend. The only cycle position where it gets a normalized leg at all is
    the healthy middle, `Apparel / Athletic Wear`, which carries `P/E (norm)` at
    0.20 — and that is a sportswear profile applied to a beauty company.

    So the brief's premise ("route forward earnings to normalized median EBIT
    when the margin deviates > 40%") describes a gate; what exists is a
    classifier that responds to the same deviation by moving the name onto a
    MORE trailing-dependent profile.
    """
    c = classify_valuation_profile
    assert c("Consumer", -0.02, 0.10, 0.5) == "Household / Personal"   # trough
    assert c("Consumer", 0.06, 0.16, 0.5) == "Apparel / Athletic Wear"  # healthy
    assert c("Consumer", 0.08, 0.18, 0.5) == "Luxury Goods"             # peak
    assert c("Consumer", 0.08, 0.20, 0.5) == "Luxury Goods"

    m = _methods
    assert m("Household / Personal")["P/E"]["anchor"] is True
    assert m("Household / Personal")["P/E"]["weight"] == 0.40
    assert "P/E (norm)" not in m("Household / Personal")
    assert m("Luxury Goods")["P/E (Premium)"]["anchor"] is True
    assert m("Luxury Goods")["P/E (Premium)"]["weight"] == 0.50
    assert "P/E (norm)" not in m("Luxury Goods")
    assert m("Apparel / Athletic Wear")["P/E (norm)"]["weight"] == 0.20
    assert m("Apparel / Athletic Wear")["P/E (norm)"]["anchor"] is False


def test_the_consumer_ladder_is_a_knife_edge_on_two_thresholds():
    """Below 5% CAGR the Consumer ladder is a band-pass on FCF margin — and a
    BETTER margin demotes the name to a worse profile.

    Measured, because it bounds how much confidence any archetype label
    deserves. `classify_valuation_profile` is a first-match ladder on
    `revenue_cagr` and `fcf_margin`; there is no hysteresis, no weighting and no
    smoothing, so a name sitting near a rung re-classifies on a restatement.

    For any Consumer name with CAGR below 5% — which is NKE at 3%, and most of
    mature apparel — the first rung (`0.0 <= cagr < 0.40 and 0.05 <= fcf < 0.18`)
    and the third (`cagr < 0.05 -> Household / Personal`) between them make the
    profile a pure band-pass on FCF margin:

        NKE, cagr 3%   fcf 0.049 -> Household / Personal
                       fcf 0.050 -> Apparel / Athletic Wear
                       fcf 0.179 -> Apparel / Athletic Wear
                       fcf 0.180 -> Household / Personal

    One tenth of a point either side of 5% or 18% swaps the anchor between
    EV/EBITDA at 0.40 and trailing P/E at 0.40. The upper edge runs the wrong
    way: raising NKE's FCF margin from 17.9% to 18.0% moves it OFF the apparel
    profile built for it and ONTO `Household / Personal`, which halves its DCF
    weight (0.30 -> 0.20), drops its only normalized leg (`P/E (norm)` 0.20) and
    hands 40% of the blend to unadjusted trailing earnings. A stronger business
    gets a lower-quality valuation.

    The other two flips measured here are the ones the brief's archetypes sit
    near: Anta across the 15% FCF rung, and ONON across the 40% CAGR rung.
    """
    c = classify_valuation_profile
    # The band-pass, at its edges.
    for fcf in (0.02, 0.04, 0.049):
        assert c("Consumer", 0.03, fcf, 0.6) == "Household / Personal", fcf
    for fcf in (0.05, 0.10, 0.179):
        assert c("Consumer", 0.03, fcf, 0.6) == "Apparel / Athletic Wear", fcf
    for fcf in (0.18, 0.25, 0.30):
        assert c("Consumer", 0.03, fcf, 0.6) == "Household / Personal", fcf
    # And it holds across the whole sub-5% CAGR range, not just at NKE's 3%.
    for cagr in (0.0, 0.02, 0.049):
        assert c("Consumer", cagr, 0.04, 0.6) == "Household / Personal"
        assert c("Consumer", cagr, 0.30, 0.6) in \
            ("Household / Personal", "Food & Beverage")
    # Crossing 5% CAGR at a strong margin jumps two rungs at once.
    assert c("Consumer", 0.049, 0.30, 0.6) == "Household / Personal"
    assert c("Consumer", 0.05, 0.30, 0.6) == "Luxury Goods"

    # The demotion is a real downgrade in method quality, not a relabel.
    ap, hh = _methods("Apparel / Athletic Wear"), _methods("Household / Personal")
    assert ap["DCF (FCF+)"]["weight"] == 0.30 and ap["EV/EBITDA"]["anchor"] is True
    assert hh["DCF"]["weight"] == 0.20 and hh["P/E"]["anchor"] is True
    assert hh["P/E"]["weight"] == 0.40
    assert "P/E (norm)" in ap and "P/E (norm)" not in hh
    assert set(ap) & set(hh) == {"EV/EBITDA"}, sorted(set(ap) & set(hh))

    # Anta across the 15% FCF rung, and ONON across the 40% CAGR rung.
    assert c("Consumer", 0.12, 0.18, 0.4) == "Luxury Goods"
    assert c("Consumer", 0.12, 0.12, 0.4) == "Apparel / Athletic Wear"
    assert c("Consumer", 0.30, 0.12, 0.3) == "Apparel / Athletic Wear"
    assert c("Consumer", 0.50, 0.12, 0.3) == "Traditional Retail"
    assert _methods("Luxury Goods")["P/E (Premium)"]["anchor"] is True
    assert set(_methods("Luxury Goods")) & set(_methods("Traditional Retail")) == set(), (
        "the two ends of the Anta flip now share a method, so the flip is "
        "less violent than this test documents")


def test_pe_premium_and_pe_share_the_trailing_branch():
    """`P/E (Premium)` is not a premium earnings source. It is the same TTM net
    income as plain `P/E`, under a different label.

    Pinned from the engine's own source and its own comment, because the profile
    name and the method name both imply otherwise. The branch set is
    `{"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}` and the comment above
    it says the two spellings "differ only in documentation intent, not earnings
    source". The normalized path is a separate branch keyed on
    `{"P/E (norm)", "P/E norm", "Normalized P/E"}`, which no Consumer profile
    other than `Apparel / Athletic Wear` names.

    Consequence for the brief's Gate 3, which describes only the over-valuation
    direction: the exposure is two-sided. At a peak, trailing NI × a "stable and
    high" multiple over-values; at a trough the same leg under-values, because
    the multiple does not fall with the earnings. `Luxury Goods`' own rationale
    — "Pricing power and brand equity make P/E multiples stable and high" — is
    the assumption that fails at a turning point, in both directions at once.
    """
    src = _engine_src()
    assert 'if method_name in {"P/E", "P/E (ops)", "P/E (Premium)", "P/E (Ops)"}:' in src
    assert 'if method_name in {"P/E (norm)", "P/E norm", "Normalized P/E"}:' in src
    assert "they differ only in documentation intent, not earnings" in src
    assert "For the TRUE cycle-normalized path use" in src
    # The trailing branch multiplies TTM net income; the normalized branch reads
    # the normalized figure. One reader each, so the two cannot be conflated.
    assert 'eps = (net_income / shares) if (net_income is not None and shares > 0) else None' in src
    assert 'norm_ni = most_recent.get("normalized_net_income")' in src
    assert _consumer_profiles()["Luxury Goods"]["rationale"].startswith(
        "Pricing power and brand equity make P/E multiples stable and high.")


def test_normalized_ebit_is_computed_for_every_name_and_read_by_nothing():
    """The quantity Gate 3 wants already exists, on every run, unused.

    `run_dcf_agent` computes all three normalized figures for every ticker and
    stores them on the most-recent row. `normalized_net_income` has three
    readers; `normalized_ebitda` has one; **`normalized_ebit` has none** — the
    string appears exactly once in the engine, and that occurrence is the write.

    This changes what implementing Gate 3 costs. It is not a modelling gap, it
    is a dispatch gap: the figure the brief asks to route forward earnings to is
    already in `most_recent`, and the only consumer-side work is a branch that
    reads it. Compare `normalized_ebitda`, which is read at exactly one place —
    inside the `EV/EBITDA (norm)` method branch — and is therefore reachable
    only from the four profiles that name that method, none of them Consumer.
    """
    src = _engine_src()
    assert src.count('"normalized_ebit"') == 1, "normalized_ebit gained a reader"
    assert src.count('"normalized_ebitda"') == 2
    assert src.count('"normalized_net_income"') == 5
    assert src.count("_normalized_earnings(") == 4   # def + three call sites
    assert '_norm_ebit   = _normalized_earnings(series, "ebit",       window=5)' in src
    assert 'most_recent["normalized_ebit"]       = _norm_ebit' in src


def test_no_consumer_profile_can_reach_the_normalized_ebitda_branch():
    """The one normalized-earnings branch with a reader is unreachable from
    Consumer. Measured across all 85 method names in the taxonomy.

    `EV/EBITDA (norm)` / `EV/EBITDA (Norm)` are named by exactly four profiles:
    Local Services & Instant Retail (Tech), Digital Asset Mining (Crypto),
    Steel / Metals (Materials) and Mining (Major) (Resources) — the four most
    cyclical profiles in the book, which is the right instinct. Consumer's
    13 profiles name none of those spellings, so `normalized_ebitda` is dead for
    every archetype in this brief even though it is computed for all of them.
    """
    norm_ev = {"EV/EBITDA (norm)", "EV/EBITDA (Norm)", "EV/EBITDA norm",
               "Normalized EV/EBITDA"}
    users = []
    for sector, profiles in INDUSTRY_VALUATION_PROFILES.items():
        for pname, d in profiles.items():
            for m in d.get("methods", []):
                if m.get("name") in norm_ev:
                    users.append((sector, pname, m["name"]))
    assert sorted(users) == [
        ("Crypto", "Digital Asset Mining", "EV/EBITDA (norm)"),
        ("Materials", "Steel / Metals", "EV/EBITDA (Norm)"),
        ("Resources", "Mining (Major)", "EV/EBITDA (norm)"),
        ("Tech", "Local Services & Instant Retail", "EV/EBITDA (norm)"),
    ], users
    consumer_vocab = {m["name"] for d in _consumer_profiles().values()
                      for m in d.get("methods", [])}
    assert consumer_vocab & norm_ev == set()
    assert len(_consumer_profiles()) == 13


def test_the_normalizers_outlier_floor_is_absolute_where_the_briefs_is_relative():
    """They agree on EL by coincidence, and diverge everywhere else.

    `_normalized_earnings` drops a year when `|margin − median| > max(2·IQR,
    0.05)` — an ABSOLUTE floor of five percentage points of margin. The brief's
    Gate 3 triggers on a RELATIVE deviation of 40%. For EL's own series
    (median 14.1%) the two nearly coincide: 40% relative is 5.64pp against a
    5pp absolute floor, so the trough year is excluded either way and the engine
    returns 2246.4 against the brief's 2199.6 — a 2.1% difference, and the
    engine's figure is the higher one because it averages the four survivors
    rather than taking their median.

    Run the same probe at other margin levels and the coincidence disappears.
    At a 5% median, a 60% relative collapse (5.0% → 2.0%, a 3pp absolute move)
    is INSIDE the 5pp floor, so the trough year is averaged straight in and the
    "normalized" margin comes out at 4.4%. At a 90% relative collapse it comes
    out at 4.1%. Meanwhile at a 30% median a 43% relative drop is a 13pp
    absolute move and IS filtered.

    The asymmetry matters more than the numbers: the engine's filter is loosest
    exactly where consumer margins are thinnest, and thin-margin is what a trough
    looks like. So the one mechanism that could have served as Gate 3 is
    weakest on the names Gate 3 is for.
    """
    import statistics

    rev = 10_000.0

    def norm(margins):
        s = [{"revenue": rev, "ebit": rev * m} for m in margins]
        return dcf_agent._normalized_earnings(s, "ebit", window=5) / rev

    el = [0.052, 0.138, 0.152, 0.145, 0.141]
    assert norm(el) == pytest.approx(0.144, abs=1e-9)
    # The brief's own EL revenue, so the dollar figures are comparable to its
    # 2199.6 rather than to this helper's 10bn probe revenue.
    el_rev = 15_600.0
    assert el_rev * norm(el) == pytest.approx(2246.4, abs=0.05)
    brief_median = el_rev * statistics.median(el)
    assert brief_median == pytest.approx(2199.6, abs=0.05)
    assert norm(el) / statistics.median(el) == pytest.approx(1.02128, abs=1e-4)
    # 40% of EL's median is 5.64pp — just past the 5pp absolute floor.
    assert 0.40 * statistics.median(el) == pytest.approx(0.0564, abs=1e-9)

    thin = [0.020, 0.050, 0.050, 0.050, 0.050]      # 60% relative collapse
    assert norm(thin) == pytest.approx(0.044, abs=1e-9), "the trough was filtered"
    assert norm(thin) == pytest.approx(statistics.mean(thin), abs=1e-9)
    deeper = [0.005, 0.050, 0.050, 0.050, 0.050]    # 90% relative collapse
    assert norm(deeper) == pytest.approx(0.041, abs=1e-9)
    rich = [0.170, 0.300, 0.300, 0.300, 0.300]      # 43% relative collapse
    assert norm(rich) == pytest.approx(0.300, abs=1e-9), "the trough survived"


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED as a routing rule, though 90% of the machinery is present. "
    "`normalized_ebit` is computed for every name on every run and has zero "
    "readers; the deviation test would need a reader, not a new computation. "
    "Two real obstacles: (1) the deviation must be measured against a median "
    "while `_normalized_earnings` averages, and its outlier filter is absolute "
    "(5pp) where the rule is relative (40%) — see "
    "test_the_normalizers_outlier_floor_is_absolute_where_the_briefs_is_relative; "
    "(2) routing means swapping a method branch, and `Luxury Goods` names no "
    "normalized method at all, so there is nothing to route TO without adding "
    "one to the profile. The brief also describes only the over-valuation "
    "direction; the exposure is two-sided."))
def test_a_trough_margin_routes_the_forward_leg_to_normalized_ebit():
    series = [{"revenue": 15_600.0, "ebit": 15_600.0 * m}
              for m in (0.052, 0.138, 0.152, 0.145, 0.141)]
    fn = getattr(dcf_agent, "_margin_deviation_routes_to_normalized", None)
    assert fn is not None, "no deviation-routing helper exists"
    assert fn(series) is True


# ── D. 2020.HK — associate equity and FX parity ──────────────────────────────


def _sotp_body() -> str:
    """The body of `_sotp_analyst_style`, from its `def` to the NAV line.

    Scoped to the function rather than to a character budget behind the add-back,
    because a fixed budget wide enough to reach the `def` (4065 chars, measured)
    also reaches into the previous function and starts matching strings that
    have nothing to do with associates.
    """
    src = _engine_src()
    start = src.index("def _sotp_analyst_style(")
    end = src.index("nav = total_seg_value + associates + net_cash")
    assert start < end
    return src[start:end]


def test_associates_are_added_back_unconditionally_and_only_inside_the_segment_path():
    """The add-back exists. It has no materiality test and no non-SOTP path.

    `_sotp_analyst_style` does `nav = total_seg_value + associates + net_cash`
    with `associates = _safe(assumptions.get("associates_investments")) or 0.0`.
    So the brief's Gate 4 is half-implemented already, and the half that is
    missing is the half its own rationale depends on: there is no comparison
    against total equity, so the add-back is all-or-nothing on whether the LLM
    extractor returned a number, not on whether the stake is material.

    It is also unreachable for a name with no segments — the function returns
    None when `rows` is empty, and rows come from `assumptions["segments"]`.
    Anta qualifies (Amer Sports is a segment story), but a single-brand apparel
    name with a minority JV would not, and would silently drop the stake.
    """
    src = _engine_src()
    assert 'associates = _safe(assumptions.get("associates_investments")) or 0.0' in src
    assert "nav = total_seg_value + associates + net_cash" in src
    body = _sotp_body()
    assert "if not rows:" in body and "return None" in body
    # The value arrives from the LLM extractor, FX-converted to USD there.
    import src.agents.analysis.sotp_extractor as sx
    sxs = inspect.getsource(sx)
    assert 'skeleton["associates"] = float(assoc) * _ccy_fx' in sxs
    assert 'assoc = getattr(row, "equity_method_investments", None) \\\n            or getattr(row, "long_term_investments", None)' in sxs
    assert '_ccy_fx = get_fx_rate(_ccy, "USD", api_key) if _ccy != "USD" else 1.0' in sxs


def test_there_is_no_associates_to_equity_materiality_gate():
    """The brief's 10%-of-equity trigger has no counterpart anywhere.

    Measured by absence across the whole of `_sotp_analyst_style` up to the
    add-back: nothing reads `shareholders_equity` or `total_equity`, and there is
    no ratio. The consequence is symmetric and worth stating both ways — an
    immaterial 0.1% stake in a holdco template is added at full book value just
    as a 20% one is, so the gate's absence over-values thin stakes rather than
    only under-valuing thick ones.
    """
    body = _sotp_body()
    for absent in ("shareholders_equity", "total_equity", "0.10",
                   "associate_ratio", "materiality"):
        assert absent not in body, f"{absent!r} appears in _sotp_analyst_style now"


def test_the_hk_reporting_currency_table_covers_a_quarter_of_the_names_it_serves():
    """A coverage gap that turned out NOT to be a defect — recorded so it is not
    re-investigated.

    `src/tools/hk/currency.py` maps 5-digit codes to a reporting currency over
    40 entries; the sector lookup carries 162 `.HK` names, so 121 fall through
    to `_DEFAULT_CURRENCY`. Anta (`02020`) is one of the 121.

    The default is **CNY**, which is right for Anta and for most HKEX-listed
    mainland issuers, so the fall-through is benign for this brief's archetype:
    `statement_to_hkd(100.0, "02020")` and `statement_to_hkd(100.0, "00700")`
    both return 116.89. The interesting counter-example is `02888.HK`, Standard
    Chartered, which reports in USD, is also absent from the table, and is a
    golden fixture — so if the label were operative its statements would carry a
    CNY→HKD factor where they need USD→HKD. They do not: the fixture records
    `source_currency: "USD"`, `reported_currency: "HKD"` and
    `fx_rate: 7.84469`. The currency in the payload comes from the feed, not
    from this table, which is why a 75% coverage gap has no measured valuation
    consequence on the one fixture that could have exposed it.

    A HKD-reporting name among the 121 would still be mislabelled, and that is
    a real (unmeasured) exposure — but it is not this brief's, and it is not the
    RMB-filing-to-HKD-listing parity the brief asks for, which already works.
    """
    import json
    from src.tools.hk.currency import (
        _DEFAULT_CURRENCY, _REPORTING_CURRENCY, get_reporting_currency,
        statement_to_hkd,
    )

    assert _DEFAULT_CURRENCY == "CNY"
    assert len(_REPORTING_CURRENCY) == 40
    assert get_reporting_currency("02020") == "CNY"
    assert "02020" not in _REPORTING_CURRENCY
    assert "00700" in _REPORTING_CURRENCY and _REPORTING_CURRENCY["00700"] == "CNY"

    hk = [k for k in TICKER_SECTOR_LOOKUP if k.endswith(".HK")]
    missing = [k for k in hk
               if k.split(".")[0].zfill(5) not in _REPORTING_CURRENCY]
    assert len(hk) == 162 and len(missing) == 121, (len(hk), len(missing))
    assert "02020.HK" in missing and "02888.HK" in missing

    assert statement_to_hkd(100.0, "02020") == statement_to_hkd(100.0, "00700")
    assert statement_to_hkd(100.0, "02020") == pytest.approx(116.89, abs=0.6)

    snap = json.load(open("tests/golden/snapshots.json", encoding="utf-8"))
    proj = snap["02888_HK"]["projection"]
    assert proj["source_currency"] == "USD"
    assert proj["reported_currency"] == "HKD"
    assert proj["fx_rate"] == pytest.approx(7.84469, abs=1e-5)


def test_the_four_archetypes_are_the_unpinned_ones():
    """NKE, ONON and Anta are classified from metrics; LULU and BIRK are not.

    `TICKER_SECTOR_LOOKUP`'s second slot is an optional profile override. Of
    83 Consumer rows, 39 fill it and 44 leave it empty — and the three of the
    brief's four archetypes that are present at all are in the empty set, while
    their two closest listed competitors are pinned:

        LULU -> ('Consumer', 'Apparel / Athletic Wear', 'Athletic Apparel', ...)
        BIRK -> ('Consumer', 'Apparel / Athletic Wear', 'Footwear', ...)
        NKE  -> ('Consumer', '',                        'Apparel',     'Nike')
        ONON -> ('Consumer', '',                        'Apparel',     'On Holding AG ADR')
        02020.HK -> ('Consumer', '', 'Sportswear', 'Anta Sports — ... P/E ~25x ...')

    So two names in the same industry as LULU are routed by whatever their
    latest FCF margin and revenue CAGR happen to be, which is the knife-edge in
    `test_the_consumer_ladder_is_a_knife_edge_on_two_thresholds`. Anta's note
    even records the intended answer — "P/E ~25x near US level" — in a field
    that is documentation, not routing.

    EL is absent from the lookup entirely: no key, and no row mentioning Estée
    Lauder. Every one of the four is therefore classified from metrics.
    """
    lk = TICKER_SECTOR_LOOKUP
    assert lk["NKE"] == ("Consumer", "", "Apparel", "Nike")
    assert lk["ONON"][1] == ""
    assert lk["02020.HK"][1] == "" and lk["02020.HK"][2] == "Sportswear"
    assert "EL" not in lk
    # Searched by name, not by an "Est" prefix — that matches every Real Estate
    # note in the table and proves nothing.
    lauder = [k for k, v in lk.items()
              if "lauder" in str(v).lower() or "estée" in str(v).lower()
              or "estee" in str(v).lower()]
    assert lauder == [], lauder
    assert lk["LULU"][1] == "Apparel / Athletic Wear"
    assert lk["BIRK"][1] == "Apparel / Athletic Wear"

    consumer = [v for v in lk.values() if v[0] == "Consumer"]
    assert len(consumer) == 83
    assert sum(1 for v in consumer if v[1]) == 39
    assert sum(1 for v in consumer if not v[1]) == 44


def test_sportswear_is_not_a_damodaran_key_and_the_fall_through_is_harmless():
    """Another gap that is not a defect, measured rather than assumed.

    `DAMODARAN_SECTOR_MAP` is keyed on (primary sector, industry group) from
    indname.xls and has `("Consumer Discretionary", "Apparel")` and
    `("Consumer Discretionary", "Shoe")` but no `Sportswear`. `map_damodaran`
    then falls back to a local `_PRIMARY_FALLBACK` dict whose
    `"Consumer Discretionary"` entry is `("Consumer", "")` — byte-identical to
    what the explicit Apparel and Shoe keys return. So Anta's unrouted industry
    lands on the same sector and the same empty WACC profile as Nike's routed
    one, and the missing key changes nothing.

    Pinned because it looks like a bug on grep and is not, and because the
    fallback is a local dict inside the function rather than a module constant,
    so it is easy to break without noticing.
    """
    from src.data.sector_profiles import map_damodaran

    assert ("Consumer Discretionary", "Apparel") in DAMODARAN_SECTOR_MAP
    assert ("Consumer Discretionary", "Shoe") in DAMODARAN_SECTOR_MAP
    assert ("Consumer Discretionary", "Sportswear") not in DAMODARAN_SECTOR_MAP
    assert map_damodaran("Consumer Discretionary", "Sportswear") == ("Consumer", "")
    assert map_damodaran("Consumer Discretionary", "Sportswear") == \
        map_damodaran("Consumer Discretionary", "Apparel")


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED. The add-back itself exists (`nav = total_seg_value + "
    "associates + net_cash`) and is FX-converted, so the missing piece is "
    "narrow: nothing compares associates to total equity, and nothing adds them "
    "outside the segment path. A 10% trigger needs `shareholders_equity` on the "
    "row — it is requested for HK/SG but not read anywhere near the add-back. "
    "Note the gate as written is one-sided; without a floor the same code "
    "over-values immaterial stakes at full book."))
def test_a_stake_above_ten_percent_of_equity_forces_an_add_back():
    body = _sotp_body()
    assert "shareholders_equity" in body
    assert "0.10" in body


# ── E. Gross-margin qualification for terminal multiples ─────────────────────


def test_the_65_percent_floor_is_live_for_tech_and_inert_for_consumer():
    """The exact floor the brief asks for exists, and stops at the sector line.

    `_SAAS_GROSS_MARGIN_FLOOR = 0.65` — the same number the brief names
    `LUXURY_GROSS_MARGIN_FLOOR`, arrived at independently. It is applied inside
    `_qualified_ev_revenue_multiple`, but behind
    `if not _is_tech_subtype(sector, profile_name): return peer, "peer"` on the
    first line, and `_is_tech_subtype` requires `is_tech_sector(sector)`.

    Called directly, with a peer EV/Revenue of 6.0 and a properly shaped row:

        Technology / Hyper-Growth Platform   gm 0.445 ->  6.0  "peer median — gross
                                              margin 45% is below the 65% SaaS floor"
        Technology / Hyper-Growth Platform   gm 0.595 ->  6.0  (same)
        Technology / Hyper-Growth Platform   gm 0.720 -> 10.0  "SaaS terminal"
        Consumer / Luxury Goods              gm 0.445 ->  6.0  "peer"
        Consumer / Luxury Goods              gm 0.595 ->  6.0  "peer"
        Consumer / Luxury Goods              gm 0.720 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.445 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.595 ->  6.0  "peer"
        Consumer / Apparel / Athletic Wear   gm 0.720 ->  6.0  "peer"

    The tech row is margin-sensitive and the two consumer rows are flat: the
    same 6.0 at 44.5% and at 72%. The mechanism is not missing, it is scoped —
    and the scoping is the whole of the brief's Gate 5.

    Two notes on what this does and does not prove. The floor qualifies
    EV/REVENUE, not P/E, so even wired to Consumer it would not gate the
    "25x+ luxury terminal multiple" the brief describes; that multiple arrives
    through `peer.get("pe", 18.0)` on the trailing branch, which has no margin
    qualification at all. And the earlier MELI incident that produced this floor
    was an over-valuation at 10x on 2.65x peer revenue — the same failure shape
    the brief predicts for consumer names, already observed once in production.
    """
    from src.agents.analysis.dcf_agent import (
        _SAAS_GROSS_MARGIN_FLOOR, _SAAS_GROSS_MARGIN_REFERENCE,
        _is_tech_subtype, _qualified_ev_revenue_multiple,
    )

    assert _SAAS_GROSS_MARGIN_FLOOR == 0.65
    assert _SAAS_GROSS_MARGIN_REFERENCE == 0.80
    peer = {"ev_revenue": 6.0}

    def row(gm):
        return {"revenue": 1000.0, "cost_of_revenue": 1000.0 * (1 - gm)}

    assert _is_tech_subtype("Technology", "Hyper-Growth Platform") is True
    for sector, profile in (("Consumer", "Luxury Goods"),
                            ("Consumer", "Apparel / Athletic Wear")):
        assert _is_tech_subtype(sector, profile) is False
        for gm in (0.445, 0.595, 0.720):
            mult, basis = _qualified_ev_revenue_multiple(
                sector, profile, "base", peer, row(gm))
            assert (mult, basis) == (6.0, "peer"), (sector, profile, gm, mult, basis)

    assert _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.445))[0] == 6.0
    assert _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.595))[0] == 6.0
    tm, tb = _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", peer, row(0.720))
    assert tm == 10.0 and tb == "SaaS terminal"

    # With no peer multiple to fall back on, the scaling leg is what fires.
    scaled, basis = _qualified_ev_revenue_multiple(
        "Technology", "Hyper-Growth Platform", "base", {}, row(0.445))
    assert scaled == pytest.approx(10.0 * (0.445 / 0.80) ** 2, rel=1e-9)
    assert "scaled by" in basis


def test_the_floors_own_rationale_names_inventory_revenue():
    """The comment above the constant makes the sector-agnostic argument itself.

    Verbatim: "A software terminal EV/Revenue multiple prices revenue that
    converts at software gross margins. Below this line the top line is
    transaction or inventory revenue and the multiple is not the right one."

    Inventory revenue is the consumer case — it is the same sentence with
    "software" struck out. Recorded because it means extending the floor is not
    a new policy position; the policy is already written down and its dispatch
    is narrower than its reasoning.
    """
    src = _engine_src()
    i = src.index("_SAAS_GROSS_MARGIN_FLOOR = 0.65")
    # The block is `#:`-prefixed and wrapped, so the sentences are not
    # contiguous substrings. Unwrap before matching, rather than matching a
    # fragment short enough to survive the wrap.
    lines = [ln.strip() for ln in src[:i].splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    block = []
    for s in reversed(lines):
        if s.startswith("#"):
            block.append(s.lstrip("#:").strip())
        else:
            break
    comment = " ".join(w for w in reversed(block) if w)
    assert comment.startswith("A software terminal EV/Revenue multiple"), comment
    assert "converts at software gross margins" in comment
    assert "transaction or inventory revenue" in comment
    assert "the multiple is not the right one" in comment
    assert "software" in comment, (
        "the rationale no longer names software, so the argument that it is "
        "sector-agnostic in everything but its dispatch no longer holds")


@pytest.mark.xfail(strict=True, reason=(
    "NOT IMPLEMENTED for Consumer, and not reachable by widening the existing "
    "floor alone: `_qualified_ev_revenue_multiple` gates an EV/REVENUE multiple, "
    "while the luxury P/E the brief wants to qualify arrives through "
    "`peer.get('pe', 18.0)` on the trailing branch, which has no margin test. "
    "Extending `_is_tech_subtype` would change the EV/Revenue leg for every "
    "consumer name and needs a golden re-baseline; adding a P/E qualification "
    "is new code on a branch that 24 profiles dispatch through."))
def test_a_low_gross_margin_consumer_name_cannot_take_a_luxury_multiple():
    from src.agents.analysis.dcf_agent import _qualified_ev_revenue_multiple
    peer = {"ev_revenue": 25.0}
    row = {"revenue": 1000.0, "cost_of_revenue": 555.0}     # gm 0.445
    mult, basis = _qualified_ev_revenue_multiple(
        "Consumer", "Luxury Goods", "base", peer, row)
    assert mult < 22.0, (mult, basis)
    assert "gross margin" in basis


# ── F. Not in the brief: the Consumer default profile contradicts itself ─────


def test_consumer_growth_declares_a_leg_it_always_strips():
    """The Consumer sector DEFAULT profile carries a weight that never applies.

    Found while mapping the archetypes, not in the brief, and it matters more
    than a bookkeeping nit because `_SECTOR_PROFILE_DEFAULT["Consumer"]` is
    `Consumer Growth` — this is the profile a Consumer name lands on when
    classification is skipped or fails, and it is where ONON routes the moment
    its FCF margin clears 15% at a CAGR above 15%.

    Its `methods` declare DCF 0.40 / EV/Revenue 0.25 / **P/E 0.20** /
    EV/EBITDA 0.15 — summing to exactly 1.00 including a leg that its own
    `excluded: ["P/E"]` strips. The strip is real and happens at two places
    (`method_values_t1.pop(ex, None)` on the Tier-1 path and
    `method_values.pop(ex, None)` on the scenario path), and `_blend_methods`
    renormalises whatever survives. Measured through the real function:

        P/E present : weight_dcf 0.40, effective DCF 0.40 / EV-Rev 0.25 /
                      P/E 0.20 / EV-EBITDA 0.15
        P/E stripped: weight_dcf 0.50, effective DCF 0.50 / EV-Rev 0.3125 /
                      EV-EBITDA 0.1875

    So the rationale text — "a blend of DCF intrinsic value (50%)" — describes
    the EFFECTIVE blend and contradicts the declared 0.40. Both readings are
    defensible in isolation; anyone computing an expected blend from `methods`
    gets 40% DCF and anyone computing it from the rationale gets 50%, and the
    engine does the second.

    The 0.20 is not harmless dead text. It is the weight the profile would carry
    if `excluded` were ever dropped, renamed or mis-spelled — and nothing
    validates that the two fields agree. The exclusion is also the reason the
    brief's Gate 3 has no normalized leg to route to on this profile: the
    earnings-based method was removed rather than replaced with `P/E (norm)`,
    which is exactly the substitution Gate 3 asks for.
    """
    from src.agents.analysis.dcf_agent import _blend_methods
    from src.data.sector_profiles import _SECTOR_PROFILE_DEFAULT

    assert _SECTOR_PROFILE_DEFAULT["Consumer"] == "Consumer Growth"
    prof = _consumer_profiles()["Consumer Growth"]
    ms = prof["methods"]
    assert {m["name"]: m["weight"] for m in ms} == {
        "DCF": 0.40, "EV/Revenue": 0.25, "P/E": 0.20, "EV/EBITDA": 0.15}
    assert sum(m["weight"] for m in ms) == pytest.approx(1.0, abs=1e-9)
    assert prof["excluded"] == ["P/E"]
    assert "DCF intrinsic value (50%)" in prof["rationale"]
    assert "P/E excluded" in prof["rationale"]

    flat = {"DCF": 100.0, "EV/Revenue": 100.0, "P/E": 100.0, "EV/EBITDA": 100.0}
    _, with_pe = _blend_methods(ms, dict(flat), 1.0, [], 0.5, 1.0)
    assert with_pe["weight_dcf"] == pytest.approx(0.40)
    assert {e["method"]: e["weight"] for e in with_pe["effective_weights"]} == \
        {"DCF": 0.40, "EV/Revenue": 0.25, "P/E": 0.20, "EV/EBITDA": 0.15}

    stripped = {k: v for k, v in flat.items() if k != "P/E"}
    iv, no_pe = _blend_methods(ms, dict(stripped), 1.0, [], 0.5, 1.0)
    assert no_pe["weight_dcf"] == pytest.approx(0.50)
    assert {e["method"]: e["weight"] for e in no_pe["effective_weights"]} == \
        {"DCF": 0.50, "EV/Revenue": 0.3125, "EV/EBITDA": 0.1875}

    # The renormalisation is exact, so the effective weights are checkable by
    # hand rather than merely observed.
    v = {"DCF": 200.0, "EV/Revenue": 100.0, "EV/EBITDA": 60.0}
    iv2, bd = _blend_methods(ms, dict(v), 1.0, [], 0.5, 1.0)
    assert iv2 == pytest.approx(
        0.50 * 200.0 + 0.50 * (0.625 * 100.0 + 0.375 * 60.0), rel=1e-12)
    assert iv2 == pytest.approx(142.5, rel=1e-12)
    assert bd["iv_multi"] == pytest.approx(85.0, rel=1e-12)


def test_the_two_strip_sites_are_both_live():
    """`excluded` is honoured on the Tier-1 path and the scenario path alike.

    Pinned because the profile contradiction above is only a documentation
    problem while both strips fire. If either stopped reading `excluded`,
    `Consumer Growth` would silently regain a trailing P/E leg at 0.20 and lose
    10 points of DCF weight — a real valuation change with no test failing
    unless this one does.
    """
    src = _engine_src()
    assert src.count('for ex in profile_data.get("excluded", []):') == 1
    assert src.count("excluded = profile_data.get(\"excluded\", [])") == 1
    assert "method_values_t1.pop(ex, None)" in src
    assert "method_values.pop(ex, None)" in src


# ── G. The brief's three integration steps, as a status record ───────────────


def test_none_of_the_briefs_three_integration_steps_is_present():
    """Each of the three edits the brief asks for, checked for by name.

    Recorded as one test rather than three because they are one deliverable, and
    because the useful fact is that NONE of them landed — a partial application
    would be worse than none, since a pre-flight hook with no bound
    reinvestment, or a bound reinvestment with no hook, would each look like the
    gate was live.

    1. a pre-flight hook calling `evaluate_consumer_discretionary_guards` before
       `_project_dcf` — the function does not exist in any module;
    2. a reinvestment deduction bound inside the projection loop via
       `overrides["apply_reinvestment_deduction"]` — neither name exists, and
       the projector has no `overrides` parameter (Section B);
    3. the resulting cash-flow identity
       `fcf_t = (ebit_t * (1 - effective_tax_rate)) + da_t - capex_t
                - reinvestment_deduction` — the real identity is
       `fcf_t = rev_t * margin_t` (Section B, measured through the function).

    This test is deliberately NOT an xfail. The five xfails above mark the
    invariants the engine does not enforce; this one marks the brief's
    implementation plan as not-started, which is a different claim and should
    stay green until someone begins it — at which point it will fail on the
    first name that appears, and the xfail above it can be retired.
    """
    src = _engine_src()
    for name in ("evaluate_consumer_discretionary_guards",
                 "apply_reinvestment_deduction",
                 "GATE_INVENTORY_STRESS",
                 "LUXURY_GROSS_MARGIN_FLOOR",
                 "_inventory_stress_days",
                 "_margin_deviation_routes_to_normalized"):
        assert name not in src, f"{name!r} has appeared; update this record"
    # Whitespace-insensitive: the real line is column-aligned as
    # `fcf_t    = rev_t * margin_t`, and pinning the padding would make this
    # fail on a reformat that changes nothing.
    assert re.search(r"fcf_t\s*=\s*rev_t \* margin_t", src), \
        "the projector's cash-flow identity is no longer revenue x margin"
    assert "ebit_t" not in src and "da_t" not in src and "capex_t" not in src
    # The hook site the brief names, so the "before _project_dcf" claim has a
    # concrete anchor: the projection call is inside run_dcf_agent's scenario
    # loop, downstream of every margin decision, not upstream of them.
    assert src.count("_project_dcf(") >= 2
