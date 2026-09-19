"""Phase 1.3 — KPIs the engine computes beat KPIs the engine reads off a sentence.

The defect this file exists for is not that an LLM got a number wrong. It is
that a number which is *arithmetic on filed statements* was being sampled from a
distribution over research sentences, run to run:

* Alibaba, 2026-09-16, seven minutes apart — ``operating_margin_pct`` came back
  **+10.0%** for 09988.HK and **−0.3%** for BABA. One field moved the composite
  1.266 → 1.0363 and opened an 18.7% gap between the two listings' blended IVs
  on a price that agreed to 0.6%.
* Keppel (BN4.SI) — ``roic_pct`` came back **1.09%**. The SG Conglomerate
  profile marks that KPI mandatory and scores a quality band off it, so 0.0109
  lands "weak" (min 0.0 → 0.9) where the filed accounts land "strong"
  (min 0.12 → 1.1). BN4.SI is single-listed, so ``resolve_company_metrics`` has
  no sibling vector to adopt: every run re-rolled the dice.

The tests below are written in three layers, because the first draft of this
work only had the first and shipped a wrong number anyway (see
``tests/test_cyclical_peak_consensus.py``'s diluted-EPS note for the precedent):

1. **The arithmetic** — pure functions over a hand-built row, asserted against
   values computed by hand in the test, not against the module's own output.
2. **The wiring** — that the three acts a new line item needs (requested from
   the feed, copied into the row, FX-classified) all happened, and that the
   override runs *before* the two consumers that matter.
3. **The blast radius** — that only keys a profile marks ``extractor_only:
   False`` can be overridden, so the 464 genuinely-extracted KPIs are untouched.
"""
from __future__ import annotations

import inspect
import re

import pytest

from src.data.deterministic_kpis import (
    EFFECTIVE_TAX_RATE,
    NEW_LINE_ITEMS,
    apply_overrides,
    compute_from_series,
    eligible_kpi_keys,
)


# ── Rows ────────────────────────────────────────────────────────────────────
# Shaped like `_extract_annual_series` output: oldest first, plain dicts,
# positive revenue guaranteed by that builder. Only the fields this module reads
# are populated; everything else is absent, which is how a real row looks when a
# provider omits a line.

def _row(**kw) -> dict:
    base = {
        "period": "2025-12-31",
        "revenue": 10_000.0,
        # `operating_income` is set EQUAL to `ebit` here on purpose. The two are
        # different quantities in the feed — FMP derives `ebit` bottom-up as
        # pre-tax income plus interest expense, so it carries the non-operating
        # line — and making them equal keeps `_ROIC` below a single hand-computed
        # constant for the whole file. The divergence is what the two
        # operating-margin tests at the end of this file pin; passing None for
        # `operating_income` reproduces production, where the field is
        # unrequested and so absent from every row.
        "ebit": 2_000.0,
        "operating_income": 2_000.0,
        "gross_profit": 4_000.0,
        "free_cash_flow": 1_500.0,
        "capital_expenditure": -800.0,
        "research_and_development": 500.0,
        "total_equity": 6_000.0,
        "total_debt": 3_000.0,
        "cash_and_equivalents": 1_000.0,
        "short_term_investments": 500.0,
        "accounts_receivable": 1_200.0,
        "inventory": 900.0,
        "accounts_payable": 700.0,
        "property_plant_equipment": 4_000.0,
    }
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


#: Hand-computed from `_row()`:
#:   financing side = 6000 + 3000 − 1000 − 500          = 7500
#:   operating floor = (1200 + 900 − 700) + 4000        = 5400
#:   capital         = max(7500, 5400)                  = 7500
#:   NOPAT           = 2000 × (1 − 0.21)                = 1580
#:   ROIC            = 1580 / 7500                      = 0.210667
_ROIC = 1580.0 / 7500.0


# ── 1. The arithmetic ───────────────────────────────────────────────────────


def test_roic_is_nopat_over_the_larger_of_the_two_capitals():
    kpis, basis = compute_from_series([_row(), _row()])
    assert kpis["roic_pct"] == pytest.approx(_ROIC, rel=1e-9)
    r = basis["roic"]
    assert r["capital_financing_side"] == pytest.approx(7500.0)
    assert r["capital_operating_floor"] == pytest.approx(5400.0)
    assert r["capital"] == pytest.approx(7500.0)
    assert r["nopat"] == pytest.approx(1580.0)
    assert r["floor_bound"] is False


def test_the_floor_is_what_stops_a_bought_back_balance_sheet():
    """The reason the floor exists, stated as a number.

    A name that has returned more capital than it raised carries negative
    equity. Financing-side capital of −2000 does not mean a negative return on
    it; it means the denominator stopped measuring capital. The operating side
    — working capital plus net PP&E — still does, and the max() substitutes it.
    """
    bought_back = _row(total_equity=-4_000.0, total_debt=1_000.0,
                       cash_and_equivalents=0.0, short_term_investments=0.0)
    kpis, basis = compute_from_series([bought_back, bought_back])
    r = basis["roic"]
    assert r["capital_financing_side"] == pytest.approx(-3_000.0)
    assert r["capital_operating_floor"] == pytest.approx(5_400.0)
    assert r["floor_bound"] is True
    assert r["capital"] == pytest.approx(5_400.0)
    assert kpis["roic_pct"] == pytest.approx(1580.0 / 5_400.0, rel=1e-9)
    assert kpis["roic_pct"] > 0


def test_roic_is_unavailable_when_both_capitals_are_non_positive():
    """Not invented. The plan's wording: "unavailable (None, band fallback)".

    A negative operating working capital plus trivial PP&E can happen — an
    asset-light platform paid in advance by its customers. There is no honest
    denominator, so the KPI is absent and the band falls back, rather than a
    number appearing on a card that no filing supports.
    """
    shell = _row(total_equity=-1_000.0, total_debt=0.0,
                 cash_and_equivalents=0.0, short_term_investments=0.0,
                 accounts_receivable=100.0, inventory=0.0,
                 accounts_payable=900.0, property_plant_equipment=50.0)
    kpis, basis = compute_from_series([shell, shell])
    assert "roic_pct" not in kpis
    assert basis["roic"]["capital"] == pytest.approx(-750.0)
    assert basis["roic"]["unavailable"] == "capital<=0 after the operating floor"


def test_a_capital_of_exactly_zero_is_unavailable_not_infinite():
    zero = _row(total_equity=0.0, total_debt=0.0, cash_and_equivalents=0.0,
                short_term_investments=0.0, accounts_receivable=0.0,
                inventory=0.0, accounts_payable=0.0,
                property_plant_equipment=0.0)
    kpis, basis = compute_from_series([zero, zero])
    assert "roic_pct" not in kpis
    assert basis["roic"]["capital"] == 0.0


def test_the_result_is_not_clamped_however_large_it_is():
    """No new bounds. The standing constraint on this work is that no clamp is
    added to an estimate unless the owner chose it, and the owner chose a
    denominator FLOOR, not a ratio cap.

    A tiny positive denominator yields an absurd ratio and that is left alone on
    purpose: the framework's quality bands saturate (the SG Conglomerate band
    tops out at min 0.12 → 1.1 "strong"), so a large ROIC cannot run away into
    the composite, and capping it here would be a bound nobody asked for. What
    the module owes instead is that the absurdity is diagnosable — the raw
    denominator is in the basis.
    """
    thin = _row(ebit=1_000.0, total_equity=10.0, total_debt=0.0,
                cash_and_equivalents=0.0, short_term_investments=0.0,
                accounts_receivable=0.0, inventory=0.0, accounts_payable=0.0,
                property_plant_equipment=0.0)
    kpis, basis = compute_from_series([thin, thin])
    assert kpis["roic_pct"] == pytest.approx(79.0, rel=1e-9)   # 7900%
    assert basis["roic"]["capital"] == pytest.approx(10.0)


def test_roic_needs_ebit_and_needs_equity():
    # `ebit` has a fallback (`operating_income`), so removing the numerator
    # means removing both — otherwise the row still computes and the test
    # asserts nothing about the missing-input path.
    for missing, drop in (("ebit", ("ebit", "operating_income")),
                          ("total_equity", ("total_equity",))):
        row = _row(**{k: None for k in drop})
        kpis, basis = compute_from_series([row, row])
        assert "roic_pct" not in kpis
        assert basis["roic"]["unavailable"] == missing


def test_operating_income_stands_in_for_ebit():
    """`ebit` is requested by the engine; `operating_income` is on the row
    builder but not on the request list, so it is None on FMP rows and present
    on others. Either is an acceptable NOPAT base, and preferring ebit keeps the
    result identical to the engine's own NOPAT at dcf_agent's `nopat = ebit *
    (1 - _EFFECTIVE_TAX_RATE)`."""
    kpis, _ = compute_from_series([_row(ebit=None, operating_income=2_000.0)] * 2)
    assert kpis["roic_pct"] == pytest.approx(_ROIC, rel=1e-9)


def test_a_missing_short_term_investment_understates_roic_not_overstates_it():
    """The conservative direction, and the same convention api.py's own
    `invested_capital` derivation uses. The HK and SG fallback providers omit
    the line entirely today; Phase 1.4 maps it, after which this stops being a
    conservative guess."""
    with_sti, _ = compute_from_series([_row()] * 2)
    without, _ = compute_from_series([_row(short_term_investments=None)] * 2)
    assert without["roic_pct"] < with_sti["roic_pct"]
    assert without["roic_pct"] == pytest.approx(1580.0 / 8_000.0, rel=1e-9)


def test_the_margins_are_the_filed_ratio_and_nothing_else():
    kpis, _ = compute_from_series([_row(), _row()])
    assert kpis["operating_margin_pct"] == pytest.approx(0.20)
    assert kpis["gross_margin_pct"] == pytest.approx(0.40)
    assert kpis["fcf_margin_pct"] == pytest.approx(0.15)
    assert kpis["rd_intensity_pct"] == pytest.approx(0.05)


def test_capex_intensity_is_a_magnitude_because_the_feed_signs_it_negative():
    """`capital_expenditure` arrives as an outflow. Intensity is how much of a
    revenue dollar goes into the ground; the sign carries no information and
    leaving it in renders a negative capex intensity on the card."""
    kpis, _ = compute_from_series([_row()] * 2)
    assert kpis["capex_intensity_pct"] == pytest.approx(0.08)
    assert kpis["capex_intensity_pct"] > 0
    signed, _ = compute_from_series([_row(capital_expenditure=800.0)] * 2)
    assert signed["capex_intensity_pct"] == pytest.approx(0.08)


def test_a_negative_operating_margin_is_kept():
    """Unlike capex, the sign here IS the information. An operating loss must
    reach the band as a loss — clamping it to zero would score a burning
    business as break-even."""
    kpis, _ = compute_from_series(
        [_row(ebit=-1_500.0, operating_income=-1_500.0)] * 2)
    assert kpis["operating_margin_pct"] == pytest.approx(-0.15)
    assert kpis["roic_pct"] < 0


def test_ebit_is_not_a_substitute_for_operating_income():
    """The two differ by the whole non-operating line, and for a cash-rich
    issuer that line is most of the profit.

    Filed FY2026 statements, measured 2026-09-17:

      Alibaba  operatingIncome  59,665m   ebit 139,180m   revenue 1,023,670m
               → 5.83% filed operating margin, 13.60% if `ebit` is used
      JD       operatingIncome   3,595m   ebit  27,398m   revenue 1,275,204m
               → 0.28% filed, 2.15% if `ebit` is used

    FMP's own `ratios-ttm.operatingProfitMarginTTM` — the reference the
    framework's `operating_margin_pct` band is calibrated against — is computed
    from `operatingIncome`. Writing the `ebit` ratio under that key scored as a
    FALSE_ALARM on both names in the 1.3 backward test, and it would have
    overstated Alibaba's operating margin 2.3x and JD's 7.6x in the live
    composite.

    ROIC's numerator is deliberately NOT changed here: it stays `ebit`, because
    that is the quantity the DCF engine uses for its own NOPAT, and whether the
    non-operating income in it should be there is an open question recorded in
    `_roic` rather than decided by this test.
    """
    kpis, basis = compute_from_series([_row(operating_income=1_800.0)] * 2)
    # operating income / revenue, NOT ebit / revenue (which would be 0.20)
    assert kpis["operating_margin_pct"] == pytest.approx(0.18)
    # ROIC still reads `ebit`, so it is unchanged by the operating-income value
    assert kpis["roic_pct"] == pytest.approx(_ROIC, rel=1e-9)
    assert basis["roic"]["ebit_source"] == "ebit"


def test_operating_margin_is_omitted_when_only_ebit_is_present():
    """Production today. `operating_income` is copied by the row builder and
    classified in `_FX_MONETARY_FIELDS`, but `run_dcf_agent`'s request list never
    asks for it — the KNOWN GAP recorded in
    `tests/test_line_items_requested_are_read.py` — so it is None on every FMP
    row and `ebit` is all there is.

    The right behaviour is to omit the key, not to write the `ebit` ratio under
    an operating-margin name: the extractor keeps supplying the value, drift and
    all, and the omission is recorded in `basis` where a run can see it. Closing
    the gap is not free either — requesting `operating_income` also activates the
    dormant Priority-1 branch of the bank EBIT path, which moves bank valuations
    and needs its own golden diff.
    """
    kpis, basis = compute_from_series([_row(operating_income=None)] * 2)
    assert "operating_margin_pct" not in kpis
    assert "operating_income" not in kpis
    assert "ebit is not a substitute" in basis["operating_margin_unavailable"]
    # ROIC still has a numerator: `ebit` is its documented fallback.
    assert kpis["roic_pct"] == pytest.approx(_ROIC, rel=1e-9)
    assert basis["roic"]["ebit_source"] == "ebit"


def test_both_r_and_d_spellings_are_emitted_with_the_same_value():
    """The framework carries `rd_intensity_pct` on three profiles and
    `r_and_d_intensity_pct` on one, whose risk band is the "Stagnation Gauge".
    Emitting only one leaves the other profile permanently LLM-sourced — the
    exact failure `tests/test_kpi_canonical_vocabulary.py` exists to prevent,
    where a bespoke name meant the card rendered a dash beside a value the FMP
    stage had already filled under the canonical one."""
    kpis, _ = compute_from_series([_row()] * 2)
    assert kpis["rd_intensity_pct"] == kpis["r_and_d_intensity_pct"] == pytest.approx(0.05)


def test_revenue_growth_needs_a_prior_year():
    kpis, basis = compute_from_series([_row()])
    assert "revenue_growth_pct" not in kpis
    assert basis["revenue_growth_unavailable"] == "only one annual row"

    kpis2, _ = compute_from_series([_row(revenue=8_000.0), _row(revenue=10_000.0)])
    assert kpis2["revenue_growth_pct"] == pytest.approx(0.25)


def test_growth_is_measured_on_the_latest_pair_not_averaged():
    rows = [_row(revenue=5_000.0), _row(revenue=8_000.0), _row(revenue=10_000.0)]
    kpis, basis = compute_from_series(rows)
    assert kpis["revenue_growth_pct"] == pytest.approx(0.25)
    assert basis["observations"] == 3


def test_the_latest_row_is_the_one_used():
    """`_extract_annual_series` returns oldest-first, so `rows[-1]` is the most
    recent filing. Getting this backwards silently scores a company on a
    five-year-old margin — and nothing about the output would look wrong."""
    rows = [_row(revenue=1_000.0, ebit=-500.0, period="2024-12-31"),
            _row(revenue=10_000.0, ebit=2_000.0, period="2025-12-31")]
    kpis, basis = compute_from_series(rows)
    assert basis["period"] == "2025-12-31"
    assert kpis["operating_margin_pct"] == pytest.approx(0.20)


def test_no_rows_means_no_kpis_and_a_reason():
    for rows in ([], None, [None, "x"]):
        kpis, basis = compute_from_series(rows)
        assert kpis == {}
        assert basis.get("unavailable") or basis.get("computed") == []


def test_a_zero_or_negative_revenue_row_computes_nothing():
    """The row builder already filters these, so this is defence against a
    caller that hands it something else. A margin over zero revenue is
    undefined; over negative revenue it is a sign-flipped fiction."""
    for rev in (0.0, -100.0):
        kpis, basis = compute_from_series([_row(revenue=rev)] * 2)
        assert kpis == {}
        assert basis["unavailable"] == "revenue<=0"


def test_missing_inputs_omit_the_key_rather_than_writing_none():
    """A gap must stay a gap. Writing None would let `_augment_metrics_with_fmp_risk`
    see the key as present-and-None (which it treats as a gap, so it would still
    fill) but would let `validate_extractor_output` count a mandatory KPI as
    populated-then-emptied, and would make an override list look contested where
    the two sources never met."""
    bare = {"period": "2025-12-31", "revenue": 10_000.0}
    kpis, basis = compute_from_series([bare])
    assert kpis == {}
    assert basis["computed"] == []
    assert basis["revenue_growth_unavailable"] == "only one annual row"
    # The ROIC basis still explains itself even though nothing was computed —
    # a dash on a card with no reason behind it is the defect, not the dash.
    assert basis["roic"]["unavailable"] == "ebit"


def test_flat_revenue_reads_as_zero_growth_not_as_missing():
    """Identical revenue across two filings is a real answer — no growth — and
    must not be flattened into an absence. A band that scores "revenue growth"
    treats 0% as a middling year and None as unscorable; conflating them would
    silently promote every flat year to unscorable."""
    bare = {"period": "2025-12-31", "revenue": 10_000.0}
    kpis, basis = compute_from_series([bare, bare])
    assert kpis["revenue_growth_pct"] == 0.0
    assert "revenue_growth_unavailable" not in basis


def test_non_finite_inputs_are_rejected_not_propagated():
    # Both numerator fields, because `_roic` falls back from `ebit` to
    # `operating_income`: corrupting one would leave the other to supply a
    # finite numerator and the test would pass without testing anything.
    for bad in (float("nan"), float("inf"), float("-inf")):
        kpis, basis = compute_from_series(
            [_row(ebit=bad, operating_income=bad)] * 2)
        assert "roic_pct" not in kpis
        assert "operating_margin_pct" not in kpis
        assert basis["roic"]["unavailable"] == "ebit"


def test_a_non_numeric_string_is_rejected_and_a_numeric_one_coerces_like_safe():
    """Two halves of one decision, and the half that matters is the first.

    `"20%"` and `"n/a"` must not become numbers — on the metrics side that is what
    distinguishes an extractor's formatting artifact from an extractor's claim of
    zero. But `"2000"` IS coerced, because `dcf_agent._safe` already coerces it
    when it builds the row, and being stricter than the engine that produced the
    input would mean a value the DCF projects from is invisible to the
    cross-check of that same DCF.
    """
    for bad in ("20%", "n/a", "2,000", ""):
        kpis, _ = compute_from_series(
            [_row(ebit=bad, operating_income=bad)] * 2)
        assert "roic_pct" not in kpis
        assert "operating_margin_pct" not in kpis

    coerced, _ = compute_from_series([_row(ebit="2000")] * 2)
    assert coerced["roic_pct"] == pytest.approx(_ROIC, rel=1e-9)
    assert coerced["operating_margin_pct"] == pytest.approx(0.20)


def test_booleans_are_not_numbers():
    kpis, _ = compute_from_series(
        [_row(ebit=True, operating_income=True)] * 2)
    assert "roic_pct" not in kpis


def test_the_tax_rate_agrees_with_the_dcf_engine():
    """Pinned by source scan rather than by import: `dcf_agent` imports the
    sector framework, so a framework-side import of `dcf_agent` is a cycle, and
    a lazy import would hide the coupling from both files' readers. If either
    constant moves, the two NOPATs disagree and ROIC stops being comparable to
    the engine's own."""
    import src.agents.analysis.dcf_agent as d
    src_text = inspect.getsource(d)
    found = re.search(r"^_EFFECTIVE_TAX_RATE\s*=\s*([0-9.]+)", src_text, re.M)
    assert found, "_EFFECTIVE_TAX_RATE not found in dcf_agent — the pin is stale"
    assert float(found.group(1)) == EFFECTIVE_TAX_RATE


def test_a_non_default_tax_rate_flows_through():
    kpis, basis = compute_from_series([_row()] * 2, tax_rate=0.30)
    assert kpis["roic_pct"] == pytest.approx(2_000.0 * 0.70 / 7_500.0, rel=1e-9)
    assert basis["roic"]["tax_rate"] == 0.30


# ── 2. The eligibility filter — the whole of the blast radius ───────────────


#: A two-profile mini-framework, so the filter is tested against something whose
#: contents this file controls.
_FRAMEWORK = {
    "Test Profile": {
        "kpis": [
            {"key": "roic_pct", "extractor_only": False},
            {"key": "operating_margin_pct", "extractor_only": False},
            {"key": "cet1_ratio", "extractor_only": True},
            {"key": "occupancy_pct", "extractor_only": True},
            {"key": "no_flag_at_all"},
        ],
    },
}


def test_only_keys_marked_fmp_derivable_are_eligible():
    got = eligible_kpi_keys("Test Profile", _FRAMEWORK)
    assert got == {"roic_pct", "operating_margin_pct"}


def test_an_absent_flag_is_not_a_false_one():
    """`extractor_only` is declared on all 540 KPI dicts today, but a new one
    that forgets the key must not be silently treated as computable — the
    default has to be "leave it to the extractor"."""
    assert "no_flag_at_all" not in eligible_kpi_keys("Test Profile", _FRAMEWORK)


def test_an_unknown_or_empty_profile_is_eligible_for_nothing():
    for name in ("Not Registered", "", None):
        assert eligible_kpi_keys(name, _FRAMEWORK) == frozenset()


def test_the_default_framework_is_the_real_one_and_roic_is_now_in_it():
    """The plan's instruction, verified against the live registration: ROIC was
    `extractor_only: True` in both places it is declared, and marking it False
    is what makes the computed value win."""
    from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
    declaring = [p for p, spec in SECTOR_KPI_FRAMEWORK.items()
                 if any(isinstance(k, dict) and k.get("key") == "roic_pct"
                        for k in (spec.get("kpis") or []))]
    assert declaring, "roic_pct vanished from the framework"
    for profile in declaring:
        assert "roic_pct" in eligible_kpi_keys(profile, SECTOR_KPI_FRAMEWORK), (
            f"{profile} declares roic_pct but it is still extractor_only")


def test_the_keppel_profile_is_the_one_the_defect_was_reported_on():
    from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
    spec = SECTOR_KPI_FRAMEWORK["Conglomerate / Industrial (SG)"]
    roic = next(k for k in spec["kpis"] if k.get("key") == "roic_pct")
    assert roic["mandatory"] is True
    assert roic["extractor_only"] is False


def test_no_balance_sheet_financial_profile_is_eligible_for_roic():
    """ROIC on a bank has no interpretation, for the reason Phase 1.2A exists —
    the deposits ARE the product, so "invested capital" counts money the bank is
    holding for someone else. No carve-out is needed in the module because no
    tier-1 profile declares the KPI at all; this pins that, so adding one is a
    decision rather than a drift."""
    from src.agents.analysis.dcf_agent import BALANCE_SHEET_FINANCIAL_PROFILES
    from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
    offenders = sorted(
        p for p in BALANCE_SHEET_FINANCIAL_PROFILES
        if "roic_pct" in eligible_kpi_keys(p, SECTOR_KPI_FRAMEWORK))
    assert offenders == []


def test_the_blast_radius_is_the_profiles_that_declare_a_computable_kpi():
    """How much of the framework this change can touch at all. Recorded rather
    than asserted against a hard number, because the count moves as profiles are
    added — but if it ever reaches all 92, the eligibility filter has stopped
    filtering."""
    from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
    from src.data.deterministic_kpis import compute_from_series as _c
    computable = set(_c([_row(), _row()])[0])
    touched = {p for p in SECTOR_KPI_FRAMEWORK
               if eligible_kpi_keys(p, SECTOR_KPI_FRAMEWORK) & computable}
    assert 0 < len(touched) < len(SECTOR_KPI_FRAMEWORK)


# ── 3. The override ─────────────────────────────────────────────────────────

#: Hand-built rather than taken from `compute_from_series`, so each case below
#: exercises one key at a time. `compute_from_series` on `_row()` emits both of
#: the mini-framework's eligible keys, which made every "one override" assertion
#: here count two — and the first draft of these tests passed the full output in
#: and asserted a length, which is how a precedence rule gets pinned to whatever
#: the fixture happened to compute rather than to the rule.
_DET = {"roic_pct": _ROIC}


def test_the_computed_value_wins_over_the_extracted_one():
    metrics = {"operating_margin_pct": 0.10, "roic_pct": 0.0109}
    kpis, basis = compute_from_series([_row()] * 2)
    out, overrides = apply_overrides(metrics, kpis, "Test Profile",
                                     framework=_FRAMEWORK, basis=basis)
    assert out["operating_margin_pct"] == pytest.approx(0.20)
    assert out["roic_pct"] == pytest.approx(_ROIC)
    assert {o["kpi"] for o in overrides} == {"operating_margin_pct", "roic_pct"}
    by_key = {o["kpi"]: o for o in overrides}
    assert by_key["roic_pct"]["llm"] == 0.0109
    assert by_key["roic_pct"]["deterministic"] == pytest.approx(_ROIC)


def test_keppels_reported_number_moves_band_not_just_decimal_place():
    """The defect, end to end. 1.09% sits in the profile's "weak" tier
    (min 0.0 → 0.9); the filed accounts put it in "strong" (min 0.12 → 1.1).
    This asserts the crossing, not merely that the number changed."""
    metrics = {"roic_pct": 0.0109}
    kpis, _ = compute_from_series([_row()] * 2)
    out, _ = apply_overrides(metrics, kpis, "Test Profile",
                             framework=_FRAMEWORK)
    assert metrics["roic_pct"] < 0.12 <= out["roic_pct"]


def test_the_input_dict_is_never_mutated():
    metrics = {"roic_pct": 0.0109}
    kpis, _ = compute_from_series([_row()] * 2)
    apply_overrides(metrics, kpis, "Test Profile", framework=_FRAMEWORK)
    assert metrics == {"roic_pct": 0.0109}


def test_a_key_the_profile_does_not_mark_computable_is_left_alone():
    """The filter is what keeps 464 genuinely-extracted KPIs out of reach. CET1,
    NIM, occupancy, same-store sales and wafer starts are quoted in research and
    are not arithmetic on a filed statement; a computed substitute for any of
    them would be a worse number with a more confident name."""
    metrics = {"cet1_ratio": 0.169, "occupancy_pct": 0.92}
    kpis = {"cet1_ratio": 0.5, "occupancy_pct": 0.5, "roic_pct": _ROIC}
    out, overrides = apply_overrides(metrics, kpis, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["cet1_ratio"] == 0.169
    assert out["occupancy_pct"] == 0.92
    assert "cet1_ratio" not in out or out["cet1_ratio"] == 0.169
    assert [o["kpi"] for o in overrides] == ["roic_pct"]


def test_an_unknown_profile_overrides_nothing():
    metrics = {"roic_pct": 0.0109}
    out, overrides = apply_overrides(metrics, _DET, "Not Registered",
                                     framework=_FRAMEWORK)
    assert out == metrics
    assert overrides == []


def test_agreement_is_not_recorded_as_an_override():
    """An override list full of no-ops makes a run look contested when the two
    sources already agreed — which is the common case, and the point of having a
    deterministic cross-check. It would also inflate the firing count the
    shipping rule is scored on."""
    out, overrides = apply_overrides({"roic_pct": _ROIC}, _DET, "Test Profile",
                                     framework=_FRAMEWORK)
    assert overrides == []
    assert out["roic_pct"] == pytest.approx(_ROIC)


def test_agreement_is_tolerant_of_float_noise_but_not_of_a_real_difference():
    """The tolerance exists because `_compute_derived_kpis` rounds to 6dp and a
    re-derivation can land one ULP off; it must not be wide enough to swallow the
    Keppel case, where the two differ by a factor of twenty."""
    noisy = {"roic_pct": _ROIC + 1e-13}
    _out, overrides = apply_overrides(noisy, _DET, "Test Profile",
                                      framework=_FRAMEWORK)
    assert overrides == []
    for delta in (1e-6, 0.01, _ROIC - 0.0109):
        _out, overrides = apply_overrides({"roic_pct": _ROIC - delta}, _DET,
                                          "Test Profile", framework=_FRAMEWORK)
        assert len(overrides) == 1, f"a {delta} difference was treated as agreement"


def test_a_gap_the_extractor_left_is_filled_and_says_it_was_a_gap():
    """`llm: None` reads differently in an audit trail from `llm: 0.10`. One is
    a reversal of a claim the model made; the other is the model having nothing
    to say, and only the first is evidence about extractor reliability."""
    out, overrides = apply_overrides({}, _DET, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["roic_pct"] == pytest.approx(_ROIC)
    assert overrides == [{"kpi": "roic_pct", "llm": None,
                          "deterministic": pytest.approx(_ROIC)}]


def test_an_explicit_none_from_the_extractor_counts_as_a_gap():
    """Same rule `_augment_metrics_with_fmp_risk` uses (`out[k] is None`) and
    that `test_explicit_none_counts_as_no_filed_value` pins for the collision
    guard: a key present with value None is a gap, not a claim of zero."""
    _out, overrides = apply_overrides({"roic_pct": None}, _DET, "Test Profile",
                                      framework=_FRAMEWORK)
    assert overrides[0]["llm"] is None


def test_a_zero_from_the_extractor_is_a_claim_and_gets_overridden():
    """The mirror image. `_augment_metrics_with_fmp_risk` lets an extracted 0
    win over an FMP value because it only fills gaps; this pass exists precisely
    to reverse that precedence for computable KPIs, so 0.0 must lose."""
    out, overrides = apply_overrides({"roic_pct": 0.0}, _DET, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["roic_pct"] == pytest.approx(_ROIC)
    assert overrides[0]["llm"] == 0.0


def test_a_non_numeric_extracted_value_is_replaced_and_recorded_as_it_was():
    """An LLM can return "20%" for a decimal KPI. Today that string wins, because
    the gap-fill only tests for None. It is not a number, so it cannot agree with
    one, and the override records the string verbatim so the audit trail shows
    what was actually there."""
    out, overrides = apply_overrides({"roic_pct": "20%"}, _DET, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["roic_pct"] == pytest.approx(_ROIC)
    assert overrides[0]["llm"] == "20%"


def test_a_none_inside_the_deterministic_dict_is_skipped_not_written():
    """Defence against a future compute pass that fills every key with None.
    `compute_from_series` omits keys instead, so this cannot happen today — but
    writing None here would turn a computable KPI into a gap on a profile where
    the extractor had actually produced a number, which is strictly worse than
    not running at all."""
    out, overrides = apply_overrides({"roic_pct": 0.0109},
                                     {"roic_pct": None}, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["roic_pct"] == 0.0109
    assert overrides == []


def test_no_deterministic_values_means_no_change_at_all():
    metrics = {"roic_pct": 0.0109}
    out, overrides = apply_overrides(metrics, {}, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out == metrics
    assert overrides == []
    assert "_deterministic_kpis" not in out


def test_the_audit_key_records_what_was_computed_and_on_which_period():
    kpis, basis = compute_from_series([_row(), _row(period="2025-12-31")])
    out, _ = apply_overrides({}, kpis, "Test Profile",
                             framework=_FRAMEWORK, basis=basis)
    audit = out["_deterministic_kpis"]
    assert audit["period"] == "2025-12-31"
    assert sorted(audit["overridden"]) == ["operating_margin_pct", "roic_pct"]
    assert "capex_intensity_pct" in audit["computed"]


def test_the_audit_key_is_private_so_the_entity_cache_strips_it():
    """`resolve_company_metrics` drops every `_`-prefixed key on adoption, and
    `test_per_run_private_keys_are_not_inherited` pins that. A record of what
    THIS run computed belongs to this run — the sibling that adopts the vector
    recomputes its own."""
    out, _ = apply_overrides({}, _DET, "Test Profile",
                             framework=_FRAMEWORK, basis={"period": "2025-12-31"})
    assert "_deterministic_kpis" in out
    assert all(k.startswith("_") for k in out if k not in _DET)


def test_metrics_of_none_is_treated_as_an_empty_vector():
    out, overrides = apply_overrides(None, _DET, "Test Profile",
                                     framework=_FRAMEWORK)
    assert out["roic_pct"] == pytest.approx(_ROIC)
    assert len(overrides) == 1


# ── 4. The wiring ───────────────────────────────────────────────────────────


def test_both_new_line_items_were_declared():
    assert set(NEW_LINE_ITEMS) == {"inventory", "property_plant_equipment"}


def test_both_new_line_items_reach_the_row_builder():
    """The template is `test_rd_field_copied_into_series`. Requesting a field and
    copying it into the row are separate acts, and doing only one has silently
    produced None on every row four times now."""
    import src.agents.analysis.dcf_agent as d
    src_text = inspect.getsource(d._extract_annual_series)
    for field in NEW_LINE_ITEMS:
        assert re.search(rf'"{field}":\s*_safe\(getattr\(li,\s*"{field}"', src_text), (
            f"{field} is not copied into the annual row")


def test_both_new_line_items_are_requested_from_the_feed():
    """`test_line_items_requested_are_read.py` asserts the general invariant by
    regex over the call shape; this asserts the two specific fields by name, so
    a refactor that satisfies the regex while dropping them fails here."""
    import src.agents.analysis.dcf_agent as d
    src_text = inspect.getsource(d.run_dcf_agent)
    m = re.search(r"search_line_items\(\s*ticker,\s*\[(.*?)\],\s*end_date",
                  src_text, re.S)
    assert m, "search_line_items request list not found in run_dcf_agent"
    requested = set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    for field in NEW_LINE_ITEMS:
        assert field in requested, f"{field} is read but never requested"


def test_both_new_line_items_are_fx_classified_as_monetary():
    """`test_fx_field_coverage.py` asserts every row field is classified; this
    asserts these two are on the MONETARY side specifically. They are compared
    against a financing-side capital that is converted, so leaving them in the
    filing currency decides the max() by exchange rate rather than by balance
    sheet — the 0388.HK failure mode the existing comment on the customer-balance
    proxy lines describes."""
    import src.agents.analysis.dcf_agent as d
    for field in NEW_LINE_ITEMS:
        assert field in d._FX_MONETARY_FIELDS
        assert field not in d._FX_NON_MONETARY_FIELDS


def test_the_kpis_are_computed_before_the_quarterly_balance_sheet_refresh():
    """The refresh moves cash, short-term investments, total debt and net debt to
    the latest quarter but leaves equity, receivables, inventory, payables and
    PP&E at the year end. ROIC's denominator is assembled from both halves, so
    computing afterwards would stitch two filing periods into one capital base."""
    import src.agents.analysis.dcf_agent as d
    src_text = inspect.getsource(d.run_dcf_agent)
    compute_at = src_text.index("_compute_det_kpis(series)")
    refresh_at = src_text.index("_bs_flag = _refresh_balance_sheet_from_latest_quarter(")
    assert compute_at >= 0 and refresh_at >= 0
    assert compute_at < refresh_at, (
        "deterministic KPIs must be computed from the annual row as filed, "
        "before the quarterly refresh mutates it")


def test_a_failure_in_the_override_never_breaks_the_valuation():
    """Both new blocks are fail-safe, matching every other framework touchpoint
    in this engine. A KPI cross-check that can take down a valuation is worse
    than no cross-check.

    Located by nearest-preceding ``try:`` / nearest-following ``except`` rather
    than by a fixed character window: the apply block's try opens well before its
    call site, with a snapshot of the pre-override vector and the bucket
    de-duplication loop in between, so any window wide enough to reach the try
    would also reach one belonging to a neighbouring block.
    """
    import src.agents.analysis.dcf_agent as d
    src_text = inspect.getsource(d.run_dcf_agent)
    for anchor in ("_compute_det_kpis(series)", "_bucket[ticker], _ovr = _apply_det_kpis("):
        at = src_text.index(anchor)
        try_at = src_text.rindex("try:", 0, at)
        except_at = src_text.index("except Exception", at)
        assert try_at < at < except_at, f"{anchor} is not inside a try block"
        # And the handler must be the fail-safe kind, not a re-raise.
        handler = src_text[except_at:except_at + 400]
        assert "raise" not in handler, f"{anchor}'s handler re-raises"
