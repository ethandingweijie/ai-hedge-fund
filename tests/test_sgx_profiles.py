"""SGX analysis pathway — routing, methods, extraction, cards.

The US pathway works because a ticker resolves deterministically to a
sub-profile, that profile names its own valuation methods, and the same
profile drives what deep research extracts and what the valuation card
renders. SGX never had that, and the failure it caused was concrete:
OCBC fell through to the runtime LLM classifier, landed on
`EM Bank (Premium)` — an India-private-bank row carrying a 13% cost of
equity — and produced a S$9.23 target on a S$30.90 share.

These tests pin all four links of that chain for every SGX ticker.
"""

from __future__ import annotations

import pytest

from src.data.sector_kpi_framework import (
    SECTOR_KPI_FRAMEWORK,
    build_extractor_schema,
    render_card_payload,
)
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    SGX_TICKER_SECTOR_LOOKUP,
    get_wacc_profile_for_ticker,
)

SGX_PROFILES = sorted(
    p for p in SECTOR_KPI_FRAMEWORK if "(SG)" in p or p == "S-REIT"
)


def _profile_sector_key(sector: str) -> str:
    return "RealEstate" if sector == "REIT" else sector


# ── Link 1: routing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", sorted(SGX_TICKER_SECTOR_LOOKUP))
def test_every_sgx_ticker_routes_deterministically(ticker):
    """No SGX ticker may depend on the runtime LLM classifier.

    An empty profile hint is what sent OCBC to the India-private-bank row.
    """
    sector, profile = get_wacc_profile_for_ticker(ticker)
    assert profile, f"{ticker}: no profile — would fall through to the LLM"
    key = _profile_sector_key(sector)
    assert INDUSTRY_VALUATION_PROFILES.get(key, {}).get(profile), (
        f"{ticker}: hint {profile!r} is not registered under sector {key!r}"
    )


@pytest.mark.parametrize(
    "ticker, expected",
    [
        ("D05.SI", "Money Center Bank (SG)"),
        ("J69U.SI", "S-REIT"),
        ("S68.SI", "Market Infrastructure (SG)"),
        ("9CI.SI", "Real Estate Asset Manager (SG)"),
        ("Z74.SI", "Telco / Infrastructure (SG)"),
        ("BN4.SI", "Conglomerate / Industrial (SG)"),
        ("S63.SI", "Aerospace & Engineering (SG)"),
        ("C6L.SI", "Aviation & Marine (SG)"),
        ("C09.SI", "Property Developer (SG)"),
        ("F34.SI", "Agribusiness & Food (SG)"),
        ("V03.SI", "Tech Manufacturing / EMS (SG)"),
        ("OYY.SI", "Real Estate Agency (SG)"),
        ("40T.SI", "Specialised Accommodation (SG)"),
        ("RE4.SI", "Offshore Marine & Resources (SG)"),
    ],
)
def test_representative_tickers_land_on_the_right_profile(ticker, expected):
    _sector, profile = get_wacc_profile_for_ticker(ticker)
    assert profile == expected


def test_aviation_and_aerospace_are_separate_profiles():
    """PLF and CASK ex-fuel are meaningless for a defence engineer, and MRO
    turnaround is meaningless for an airline — so they cannot share a spec."""
    _s, sia = get_wacc_profile_for_ticker("C6L.SI")
    _s, steng = get_wacc_profile_for_ticker("S63.SI")
    assert sia != steng
    aviation = {k["key"] for k in SECTOR_KPI_FRAMEWORK[sia]["kpis"]}
    aero = {k["key"] for k in SECTOR_KPI_FRAMEWORK[steng]["kpis"]}
    assert "passenger_load_factor" in aviation and "passenger_load_factor" not in aero
    assert "mro_turnaround_days" in aero and "mro_turnaround_days" not in aviation


# ── Link 2: methods ──────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_every_sgx_profile_declares_a_dispatchable_anchor(profile):
    """A declared method the engine cannot dispatch yields an empty value."""
    from src.agents.analysis import dcf_agent

    spec = SECTOR_KPI_FRAMEWORK[profile]
    key = _profile_sector_key(spec["sector"])
    vprofile = INDUSTRY_VALUATION_PROFILES[key][profile]
    anchors = [m for m in vprofile["methods"] if m.get("anchor")]
    assert len(anchors) == 1, f"{profile}: expected exactly one anchor"

    source = dcf_agent.__doc__ or ""
    with open(dcf_agent.__file__, encoding="utf-8") as fh:
        source = fh.read()
    for m in vprofile["methods"]:
        assert f'"{m["name"]}"' in source, (
            f"{profile}: method {m['name']!r} is declared but never dispatched"
        )


@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_method_weights_sum_to_one(profile):
    spec = SECTOR_KPI_FRAMEWORK[profile]
    vprofile = INDUSTRY_VALUATION_PROFILES[_profile_sector_key(spec["sector"])][profile]
    total = sum(m["weight"] for m in vprofile["methods"])
    assert total == pytest.approx(1.0, abs=0.001), f"{profile}: weights sum to {total}"


# ── Link 3: extraction ───────────────────────────────────────────────────

@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_every_sgx_profile_has_extractor_fields(profile):
    """A profile with no framework entry asks the extractor for nothing.

    `S-REIT` silently returned zero fields until its spec was registered —
    every SGX REIT routed to a profile whose extractor was empty.
    """
    schema = build_extractor_schema(profile)
    assert schema["clamps"], f"{profile}: extractor schema is empty"


@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_critical_and_good_to_have_are_both_present(profile):
    """Critical = valuation blocker (mandatory). Good-to-have = catalyst.

    A profile with no mandatory KPI makes `_completeness_score` meaningless;
    one with no optional KPI has no catalyst layer at all.
    """
    kpis = SECTOR_KPI_FRAMEWORK[profile]["kpis"]
    assert [k for k in kpis if k.get("mandatory")], f"{profile}: no critical KPI"
    assert [k for k in kpis if not k.get("mandatory")], f"{profile}: no good-to-have KPI"


def test_no_kpi_key_collides_with_a_statement_line_item():
    """The framework bridge writes extracted KPIs onto the financial row BY
    KEY, so a collision silently replaces real data — that is how an
    extracted 32.5 displaced DBS's real book value per share of 24.28."""
    LINE_ITEMS = {
        "revenue", "net_income", "total_equity", "total_assets", "total_debt",
        "total_liabilities", "book_value_per_share", "earnings_per_share",
        "free_cash_flow", "operating_cash_flow", "operating_income",
        "interest_income", "interest_expense", "capital_expenditure",
        "shares_outstanding", "net_debt", "gross_margin", "long_term_debt",
        "dividends_per_share", "dividends_and_distributions",
        "accounts_receivable", "depreciation_and_amortization",
    }
    for profile in SGX_PROFILES:
        for kpi in SECTOR_KPI_FRAMEWORK[profile]["kpis"]:
            assert kpi["key"] not in LINE_ITEMS, (
                f"{profile}.{kpi['key']} shadows a statement line item — "
                f"namespace it instead"
            )


# ── Link 4: the sector valuation card ────────────────────────────────────

@pytest.mark.parametrize("profile", SGX_PROFILES)
def test_sector_card_renders_with_groups(profile):
    """The card is generic — a complete spec produces one automatically.

    KPIs with no `group` never render, so an ungrouped spec yields a card
    with a header and nothing under it.
    """
    spec = SECTOR_KPI_FRAMEWORK[profile]
    state = {"data": {"framework_metrics_all": {"TEST": {}}}}
    payload = render_card_payload(profile, state, "TEST")
    if payload is None:
        pytest.skip(f"{profile} is a legacy bespoke-card profile")
    assert payload["anchor_methods"], f"{profile}: card has no anchor methods"
    assert payload["groups"], f"{profile}: card rendered zero groups"
    grouped = {k["key"] for g in payload["groups"] for k in g["kpis"]}
    declared = {k["key"] for k in spec["kpis"]}
    assert declared - grouped == set(), (
        f"{profile}: KPIs missing from the card (no `group`): "
        f"{sorted(declared - grouped)}"
    )
