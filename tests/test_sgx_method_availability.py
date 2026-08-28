"""Every declared SGX method must actually produce a value.

The BN4.SI (Keppel) forward run reported `methods: EV/EBITDA, ROIC vs WACC`
against a profile declaring `SOTP` at a 0.45 anchor. SOTP reads
`segment_breakdown`, which is populated solely from FMP
revenue-product-segmentation and returns nothing for SGX — so the anchor
silently never fired and the blend renormalised onto the secondaries. The
profile's stated valuation method was a fiction, and nothing in the test
suite noticed.

That bug is invisible to a schema test: the method was spelled correctly,
was dispatchable, and had a sane weight. The only way to catch it is to
call the dispatcher and check what comes back.

These tests drive `_compute_method_value` directly with a realistic
Singapore financial row — no network, no LLM — and assert that every
method each SGX profile declares returns a usable number.
"""

from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import _compute_method_value
from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES

SGX_PROFILES = sorted(
    p for p in SECTOR_KPI_FRAMEWORK if "(SG)" in p or p == "S-REIT"
)

# A mid-cap SGX industrial, roughly Keppel-shaped, in SGD. Deliberately
# ordinary: every field a Singapore filer actually reports, nothing exotic,
# so a method that needs something unavailable on SGX fails here the same
# way it fails in production.
BASE_ROW = {
    "revenue": 6_600e6,
    "net_income": 800e6,
    "operating_income": 1_050e6,
    "ebitda": 1_400e6,
    "ebit": 1_050e6,
    "total_equity": 11_500e6,
    "total_assets": 27_000e6,
    "total_debt": 9_400e6,
    "long_term_debt": 7_900e6,
    "net_debt": 7_913e6,
    "total_liabilities": 15_500e6,
    "free_cash_flow": 620e6,
    "operating_cash_flow": 980e6,
    "capital_expenditure": -360e6,
    "shares_outstanding": 1_906e6,
    "book_value_per_share": 6.03,
    "earnings_per_share": 0.42,
    "dividends_per_share": 0.34,
    "dividends_and_distributions": -648e6,
    "interest_expense": 300e6,
    "interest_income": 120e6,
    "depreciation_and_amortization": 350e6,
    "invested_capital": 19_000e6,
    "gross_margin": 0.28,
    "cash_and_equivalents": 1_500e6,
    "goodwill": 0.0,
    "intangible_assets": 0.0,
    "distributable_income": 620e6,
    # Supplied by the engine in a real run: `normalized_net_income` comes
    # from the 5-year series, not a single row.
    "normalized_net_income": 780e6,
}

# Forward consensus by scenario. The engine passes this from FMP estimates;
# the BN4.SI live run produced `Forward P/E: 17.62`, so it IS available for
# SGX and a harness that omitted it would report false failures.
FORWARD_CONSENSUS = {
    "eps":    {"bear": 0.36, "base": 0.45, "bull": 0.54},
    "ebitda": {"bear": 1_200e6, "base": 1_450e6, "bull": 1_700e6},
}

# Representative ticker per profile — the one whose data shape the profile
# was calibrated against.
REPRESENTATIVE = {
    "Money Center Bank (SG)":                 "D05.SI",
    "S-REIT":                                 "AJBU.SI",
    "Market Infrastructure (SG)":             "S68.SI",
    "Real Estate Asset Manager (SG)":         "9CI.SI",
    "Telco / Infrastructure (SG)":            "Z74.SI",
    "Conglomerate / Industrial (SG)":         "BN4.SI",
    "Aerospace & Engineering (SG)":           "S63.SI",
    "Aviation & Marine (SG)":                 "C6L.SI",
    "Property Developer (SG)":                "C09.SI",
    "Agribusiness & Food (SG)":               "F34.SI",
    "Tech Manufacturing / EMS (SG)":          "V03.SI",
    "Real Estate Agency (SG)":                "OYY.SI",
    "Specialised Accommodation (SG)":         "40T.SI",
    "Offshore Marine & Resources (SG)":       "RE4.SI",
    "WealthTech & Specialty Financials (SG)": "IFA.SI",
    "Packaged Consumer & Lifestyle (SG)":     "G13.SI",
}


def _row_for(profile: str) -> dict:
    """BASE_ROW plus whatever a profile's own methods legitimately need."""
    row = dict(BASE_ROW)
    if profile == "Money Center Bank (SG)":
        row.update({
            "revenue": 37_892e6, "interest_income": 28_268e6,
            "interest_expense": 13_768e6, "operating_expense": 10_344e6,
            "total_assets": 897_488e6, "total_equity": 68_916e6,
            "net_income": 10_933e6, "shares_outstanding": 2_850e6,
            "book_value_per_share": 24.28, "_bank_target_roe_research": 0.166,
        })
        # The bank branch derives through-cycle normalised earnings itself
        # (equity x target ROE), which is immune to credit-cycle provision
        # swings. Leaving an industrial-scale figure here would feed P/E
        # (norm) the wrong base entirely.
        row.pop("normalized_net_income", None)
    elif profile == "S-REIT":
        row.update({
            "_sreit_subtype": "data_centre",
            "_sreit_dpu_cents_research": 11.54,
            "cap_rate_market": 0.05,
        })
    return row


def _evaluate(profile: str, method: str):
    """Call the dispatcher exactly as the engine does for a base scenario."""
    spec = SECTOR_KPI_FRAMEWORK[profile]
    sector = spec["sector"]
    row = _row_for(profile)
    return _compute_method_value(
        method_name=method,
        most_recent=row,
        revenue_base=row["revenue"],
        shares=row["shares_outstanding"],
        net_debt=row["net_debt"],
        market_cap=row["shares_outstanding"] * 7.0,
        wacc=0.075,
        growth_base=0.03,
        fcf_margin_base=0.09,
        tgr=0.02,
        fcf_floor=0.01,
        sector=sector,
        scenario="base",
        reported_currency="SGD",
        profile_name=profile,
        forward_consensus=FORWARD_CONSENSUS,
        ticker=REPRESENTATIVE.get(profile, ""),
    )


def _declared_methods(profile: str) -> list[dict]:
    spec = SECTOR_KPI_FRAMEWORK[profile]
    key = "RealEstate" if spec["sector"] == "REIT" else spec["sector"]
    return INDUSTRY_VALUATION_PROFILES[key][profile]["methods"]


@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_anchor_method_produces_a_value(profile):
    """The anchor carries the most weight — if it returns None the profile's
    stated methodology is not what actually ran."""
    anchor = next(m["name"] for m in _declared_methods(profile) if m.get("anchor"))
    value = _evaluate(profile, anchor)
    assert value is not None, (
        f"{profile}: anchor {anchor!r} returned None on an ordinary SGX row — "
        f"the weights would renormalise onto the secondary methods and the "
        f"profile would report a methodology it never used"
    )
    assert value > 0, f"{profile}: anchor {anchor!r} returned {value}"


@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_every_declared_method_produces_a_value(profile):
    """A declared method that never contributes is dead weight in the blend.

    `SOTP (published)` is exempt: it is data-gated by design and returns
    None unless a published table exists for that specific ticker, which is
    the correct behaviour rather than a silent substitution.
    """
    DATA_GATED = {"SOTP (published)", "Published SOTP"}
    dead = []
    for m in _declared_methods(profile):
        name = m["name"]
        if name in DATA_GATED:
            continue
        if _evaluate(profile, name) is None:
            dead.append(f"{name} (weight {m['weight']})")
    assert not dead, f"{profile}: declared methods returned None: {dead}"


def test_published_sotp_is_gated_not_broken():
    """The one data-gated method must fire where a table exists."""
    assert _evaluate("Conglomerate / Industrial (SG)", "SOTP (published)") is not None
    # ...and stay silent where none does, rather than substituting.
    spec = SECTOR_KPI_FRAMEWORK["Telco / Infrastructure (SG)"]
    row = _row_for("Telco / Infrastructure (SG)")
    assert _compute_method_value(
        method_name="SOTP (published)", most_recent=row,
        revenue_base=row["revenue"], shares=row["shares_outstanding"],
        net_debt=row["net_debt"], market_cap=1e10, wacc=0.075,
        growth_base=0.03, fcf_margin_base=0.09, tgr=0.02, fcf_floor=0.01,
        sector=spec["sector"], scenario="base", reported_currency="SGD",
        profile_name="Telco / Infrastructure (SG)", ticker="Z74.SI",
    ) is None


@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_anchor_value_is_in_a_plausible_band(profile):
    """A method returning a value orders of magnitude off its peers drags the
    blend toward a number no method supports.

    `ROIC vs WACC` returned S$1.90 on the Keppel run against EV/EBITDA's
    S$10.91 and a published SOTP of S$10.70, and at 25% weight that was
    enough to distort the result.
    """
    values = []
    for m in _declared_methods(profile):
        v = _evaluate(profile, m["name"])
        if v is not None and v > 0:
            values.append((m["name"], v))
    if len(values) < 2:
        pytest.skip(f"{profile}: fewer than two methods produced a value")
    lo = min(v for _n, v in values)
    hi = max(v for _n, v in values)
    assert hi / lo < 12.0, (
        f"{profile}: declared methods disagree by {hi / lo:.1f}x — {values}"
    )
