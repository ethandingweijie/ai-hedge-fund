"""Bank valuation profile — regression tests.

Pins the fixes for the D05.SI (DBS Group) run of 2026-08-27, which produced a
12-month price target of S$18.19 against a S$75.65 share price and reported
every ticker-keyed report card as missing.

Ground truth is taken from published Phillip Securities Research Gordon Growth
Model tables:
  * DBS  (4 May 2026,  SG2026_0093): CoE 8.6%, ROE 16.6%, g 3.3% -> P/B 2.51x
  * OCBC (11 May 2026, SG2026_0099): CoE 9.1%, ROE 12.8%, g 3.0% -> P/B 1.60x
  * JPM  (21 Oct 2025):               CoE 10%,  ROE 19.5%, g 3.0% -> P/B 2.37x
"""

from __future__ import annotations

import pytest

from src.agents.analysis.dcf_agent import (
    _analyst_growth_bands,
    _bank_ggm_assumptions,
    _bank_total_income,
    _compute_bank_metrics,
    _compute_ggm_pb,
    _BANK_PROFILE_CALIBRATION,
)
from src.agents.audit.card_qa_agent import _get_path, _split_path
from src.data.sector_profiles import (
    INDUSTRY_VALUATION_PROFILES,
    get_wacc_profile_for_ticker,
)


# DBS FY2025 annual row as it arrived in the failing run (SGD).
DBS_FY2025 = {
    "revenue": 37_892e6,            # GROSS: interest income + non-interest income
    "interest_income": 28_268e6,
    "interest_expense": 13_768e6,
    "operating_expense": 10_344e6,
    "total_assets": 897_488e6,
    "total_equity": 68_916e6,
    "net_income": 10_933e6,
    "shares_outstanding": 2_850e6,
    "book_value_per_share": 24.2833,
}


# ── Card QA: dotted tickers ──────────────────────────────────────────────

@pytest.mark.parametrize("ticker", ["D05.SI", "O39.SI", "00700.HK", "AAPL"])
def test_ticker_keyed_paths_resolve_for_dotted_tickers(ticker):
    """`{ticker}` must stay one path segment even when it contains a dot.

    Splitting after substitution turned `data.dcf_range.D05.SI` into four
    segments, so every ticker-keyed card (dcf_range_summary,
    scenario_analysis_card, value_trap_card, bank_card, decisions_panel,
    power_law_card) was reported missing-mandatory on every SGX and HKEX
    ticker, while non-ticker-keyed cards passed.
    """
    state = {"data": {"dcf_range": {ticker: {"base": {"intrinsic_value": 41.44}}}}}
    assert _split_path("data.dcf_range.{ticker}", ticker)[-1] == ticker
    assert _get_path(state, "data.dcf_range.{ticker}", ticker) is not None


def test_missing_path_still_returns_none():
    state = {"data": {"dcf_range": {}}}
    assert _get_path(state, "data.dcf_range.{ticker}", "D05.SI") is None


# ── Bank total income ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "revenue, interest_income, interest_expense, reported_total_income",
    [
        (34_354e6, 27_862e6, 14_220e6, 20_180e6),   # DBS FY2023
        (38_720e6, 30_927e6, 16_503e6, 22_297e6),   # DBS FY2024
    ],
)
def test_bank_total_income_matches_reported(
    revenue, interest_income, interest_expense, reported_total_income
):
    """Derived total income must land within 2% of the reported figure.

    The gross `revenue` line is ~1.7x total income; feeding it in wherever
    revenue is expected is what produced the -30% growth clamp.
    """
    derived = _bank_total_income({
        "revenue": revenue,
        "interest_income": interest_income,
        "interest_expense": interest_expense,
    })
    assert derived == pytest.approx(reported_total_income, rel=0.02)


def test_bank_total_income_passes_through_net_revenue():
    """A filer already reporting net revenue must not be netted twice."""
    row = {"revenue": 100.0, "interest_income": 120.0, "interest_expense": 30.0}
    assert _bank_total_income(row) == 100.0


def test_bank_total_income_rejects_implausible_netting():
    """An interest-expense line larger than 75% of revenue is mis-scaled."""
    row = {"revenue": 100.0, "interest_income": 90.0, "interest_expense": 95.0}
    assert _bank_total_income(row) == 100.0


# ── Analyst growth bands ─────────────────────────────────────────────────

class _Estimate:
    """Consensus row for DBS FY26e total income (SGD)."""
    revenue_low = 22_800e6
    revenue_avg = 23_558e6
    revenue_high = 24_300e6
    analyst_count_revenue = 15


def test_growth_bands_collapse_on_gross_revenue_base():
    """Documents the failure mode: all three bands pinned to the clamp floor."""
    bands = _analyst_growth_bands([_Estimate()], DBS_FY2025["revenue"])
    assert bands["bear"] == bands["base"] == bands["bull"] == pytest.approx(-0.30)


def test_growth_bands_are_sane_on_total_income_base():
    """On the correct base the bands separate and stay off the floor."""
    bands = _analyst_growth_bands([_Estimate()], _bank_total_income(DBS_FY2025))
    assert bands["bear"] < bands["base"] < bands["bull"]
    assert bands["bear"] > -0.15
    assert -0.10 < bands["base"] < 0.10


# ── Bank metrics ─────────────────────────────────────────────────────────

def test_nim_uses_interest_earning_assets():
    """NII / total assets understates NIM; DBS reported 1.89%, not 1.62%."""
    nim = _compute_bank_metrics(DBS_FY2025, "Money Center Bank (SG)")["nim"]
    assert nim == pytest.approx(0.019, abs=0.002)


def test_efficiency_ratio_uses_total_income():
    """Cost-income against gross revenue flattered DBS to 27.3%."""
    eff = _compute_bank_metrics(DBS_FY2025, "Money Center Bank (SG)")["efficiency_ratio"]
    assert 0.35 < eff < 0.50


# ── Gordon Growth Model ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ticker, profile, expected_pb",
    [
        ("D05.SI", "Money Center Bank (SG)", 2.51),
        ("O39.SI", "Money Center Bank (SG)", 1.60),
        ("JPM",    "Money Center Bank",      2.37),
    ],
)
def test_ggm_reproduces_published_target_pb(ticker, profile, expected_pb):
    row = {"book_value_per_share": 20.0, "net_income": 1e9, "total_equity": 1e10}
    _value, target_pb, _a = _compute_ggm_pb(ticker, profile, row, 1e9)
    assert target_pb == pytest.approx(expected_pb, abs=0.02)


def test_ggm_reproduces_dbs_target_price():
    """target P/B x BVPS must land on the published S$61.00 target."""
    value, _pb, assumptions = _compute_ggm_pb(
        "D05.SI", "Money Center Bank (SG)", DBS_FY2025, DBS_FY2025["shares_outstanding"]
    )
    assert value == pytest.approx(61.00, rel=0.02)
    assert any("broker" in p for p in assumptions["provenance"])


def test_ggm_returns_none_when_coe_approaches_growth():
    """The formula diverges as CoE approaches g — must decline, not explode.

    Both inputs are inside their own accepted bands (CoE 6.5% is within
    250bps of the 8.8% profile; g 6.3% is under the 7% ceiling) — it is
    only their proximity to each other that makes the multiple unstable.
    """
    row = {"book_value_per_share": 20.0, "_bank_coe_research": 0.065,
           "_bank_ggm_g_research": 0.063, "_bank_target_roe_research": 0.15}
    a = _bank_ggm_assumptions("XXX", "Money Center Bank (SG)", row)
    assert (a["coe"], a["g"]) == (0.065, 0.063)     # both accepted
    assert _compute_ggm_pb("XXX", "Money Center Bank (SG)", row, 1e9) is None


def test_out_of_band_research_coe_falls_back_to_profile():
    """A 3.1% cost of equity for a bank is a bad extraction, not a datum."""
    row = {"book_value_per_share": 20.0, "_bank_coe_research": 0.031,
           "_bank_target_roe_research": 0.15}
    a = _bank_ggm_assumptions("XXX", "Money Center Bank (SG)", row)
    assert a["coe"] == _BANK_PROFILE_CALIBRATION["Money Center Bank (SG)"]["coe"]
    assert "CoE 8.8% (profile)" in a["provenance"]


def test_research_roe_outranks_broker_but_coe_does_not():
    """ROE is management-disclosed; CoE and g are analyst constructs.

    A published broker valuation table states CoE and g deliberately; an
    extractor scraping prose for "cost of equity" is guessing. On the
    2026-08-27 run an extracted 10.5% CoE displaced Phillip's 8.6% and cut
    the target P/B from 2.51x to 1.62x. ROE still takes the live value.
    """
    row = {"book_value_per_share": 20.0, "_bank_coe_research": 0.105,
           "_bank_ggm_g_research": 0.025, "_bank_target_roe_research": 0.14}
    a = _bank_ggm_assumptions("D05.SI", "Money Center Bank (SG)", row)
    assert a["roe"] == 0.14            # research wins
    assert a["coe"] == 0.086           # broker table wins
    assert a["g"] == 0.033             # broker table wins
    assert any("not used" in p for p in a["provenance"])


def test_research_coe_used_when_no_broker_row_and_plausible():
    row = {"book_value_per_share": 28.0, "_bank_coe_research": 0.095}
    a = _bank_ggm_assumptions("U11.SI", "Money Center Bank (SG)", row)
    assert a["coe"] == 0.095
    assert "CoE 9.5% (research)" in a["provenance"]


def test_implausible_research_coe_is_rejected():
    """A CoE far off the profile's risk-free + ERP structure is a bad grab."""
    row = {"book_value_per_share": 28.0, "_bank_coe_research": 0.16}
    a = _bank_ggm_assumptions("U11.SI", "Money Center Bank (SG)", row)
    assert a["coe"] == _BANK_PROFILE_CALIBRATION["Money Center Bank (SG)"]["coe"]
    assert any("rejected" in p for p in a["provenance"])


def test_sustainable_roe_falls_back_to_midpoint():
    """Without a published ROE, the GGM must not use the RI fade target alone."""
    profile = "Money Center Bank"
    cfg = _BANK_PROFILE_CALIBRATION[profile]
    row = {"book_value_per_share": 20.0, "net_income": 19e9, "total_equity": 100e9}
    a = _bank_ggm_assumptions("NOTCOVERED", profile, row)
    assert cfg["target_roe"] < a["roe"] < 0.19
    assert "midpoint" in a["provenance"][0]


# ── Profile routing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", ["D05.SI", "O39.SI", "U11.SI"])
def test_sg_banks_route_to_sg_profile(ticker):
    sector, profile = get_wacc_profile_for_ticker(ticker)
    assert (sector, profile) == ("Financials", "Money Center Bank (SG)")


def test_sg_profile_has_lower_coe_and_higher_roe_than_us():
    sg = _BANK_PROFILE_CALIBRATION["Money Center Bank (SG)"]
    us = _BANK_PROFILE_CALIBRATION["Money Center Bank"]
    assert sg["coe"] < us["coe"]
    assert sg["target_roe"] > us["target_roe"]


@pytest.mark.parametrize(
    "ticker, name, expected_profile",
    [
        ("D05.SI",   "DBS",     "Money Center Bank (SG)"),
        ("O39.SI",   "OCBC",    "Money Center Bank (SG)"),
        ("U11.SI",   "UOB",     "Money Center Bank (SG)"),
        ("01398.HK", "ICBC",    "EM Bank"),
        ("00939.HK", "CCB",     "EM Bank"),
        ("01288.HK", "AgBank",  "EM Bank"),
        ("03988.HK", "Bank of China",   "EM Bank"),
        ("03968.HK", "China Merchants", "EM Bank"),
        ("00998.HK", "China CITIC",     "EM Bank"),
        ("03328.HK", "BoCom",           "EM Bank"),
        ("01658.HK", "Postal Savings",  "EM Bank"),
        # HK-domiciled, HKMA-supervised, USD-pegged funding — not the
        # mainland SOE calibration.
        ("02388.HK", "BOC Hong Kong",   "Regional Bank"),
        ("00011.HK", "Hang Seng",       "Regional Bank"),
        ("00005.HK", "HSBC",            "Money Center Bank (EU)"),
    ],
)
def test_major_sg_hk_banks_route_deterministically_with_ggm(
    ticker, name, expected_profile
):
    """Every major SG/HK bank must resolve to a profile WITHOUT the LLM.

    An empty profile hint falls through to the runtime classifier, which is
    how OCBC landed on "EM Bank (Premium)" — the India-private row, CoE 13%
    — on the 2026-08-27 production run. Agricultural Bank of China, CITIC
    and Bank of Communications all carried empty hints for the same reason.
    """
    sector, profile = get_wacc_profile_for_ticker(ticker)
    assert (sector, profile) == ("Financials", expected_profile), name

    methods = [
        m["name"]
        for m in INDUSTRY_VALUATION_PROFILES["Financials"][profile]["methods"]
    ]
    assert "GGM (P/B)" in methods, f"{name} has no GGM method"

    # And the GGM must actually produce a multiple on ordinary bank inputs.
    row = {"book_value_per_share": 10.0, "net_income": 1e9, "total_equity": 1e10}
    result = _compute_ggm_pb(ticker, profile, row, 1e9)
    assert result is not None, name
    assert 0.3 <= result[1] <= 4.0, (name, result[1])


def test_only_real_profiles_are_returned():
    """A hint that is not a registered profile must never be returned.

    Was `test_non_bank_sgx_tickers_still_fall_through`, which asserted
    C6L.SI / Z74.SI / S68.SI resolve to "". They now route to real SGX
    profiles, so the assertion is inverted: the invariant was never
    "these fall through", it was "whatever comes back is resolvable".
    """
    from src.data.sector_profiles import (
        SGX_TICKER_SECTOR_LOOKUP, INDUSTRY_VALUATION_PROFILES,
    )
    for ticker in SGX_TICKER_SECTOR_LOOKUP:
        sector, profile = get_wacc_profile_for_ticker(ticker)
        if not profile:
            continue                      # unrouted is allowed; bogus is not
        key = "RealEstate" if sector == "REIT" else sector
        assert INDUSTRY_VALUATION_PROFILES.get(key, {}).get(profile), (
            f"{ticker}: hint {profile!r} is not a registered profile "
            f"under sector {key!r}"
        )
