"""HK/SG CFA audit, 2026-11-25: the defects found on the 22-name production sweep, pinned.

1. the comps market-cap sanity check compares caps in one currency (HSBC, AIA reported in USD, quoted in HKD);
2. a deposit-funded intermediary skips the core-earnings strip (OCBC FY25 core NI read 2x reported);
3. a bank's normalised earnings are its own mean ROE on today's book;
4. the S-REIT DDM capitalises D1, not D2;
5. the DDM discounts at the firm-CAPM Ke when the WACC was built on it, not WACC + a spread;
6. the forecast build's tax rate is the filed effective rate;
7. a relative leg priced on a branch literal says so instead of "peer median".
"""
from __future__ import annotations

import pytest

from src.agents.analysis import dcf_agent as d
from src.agents.analysis import guidance_forecast as gf
from src.data import regional_comps as rc


# ── 1. market cap in the listing currency ───────────────────────────────────

def test_a_usd_reporter_in_hong_kong_is_restated_in_hkd(monkeypatch):
    import src.tools.api as api
    monkeypatch.setattr(api, "get_fx_rate", lambda a, b="USD", api_key=None: 7.84 if (a, b) == ("USD", "HKD") else 1.0)
    assert rc.mcap_in_listing_currency("0005.HK", 277.7e9, "USD") == pytest.approx(277.7e9 * 7.84)
    # same currency, US listing without a reported currency: unchanged
    assert rc.mcap_in_listing_currency("D05.SI", 186.9e9, "SGD") == 186.9e9
    assert rc.mcap_in_listing_currency("AAPL", 4e12, None) == 4e12
    # unknown pair (the FX helper's 1.0 sentinel) or an unknown reporting currency off-US: no cap, no check
    assert rc.mcap_in_listing_currency("0700.HK", 2.6e12, "XYZ") is None
    assert rc.mcap_in_listing_currency("0700.HK", 2.6e12, None) is None


def test_the_converted_cap_passes_the_sanity_check():
    hkd_screener, usd_km = 2212e9, 277.7e9
    assert rc.mcap_mismatch(hkd_screener, {"_mcap_multiples": {"value": usd_km}}) is True       # the defect
    assert rc.mcap_mismatch(hkd_screener, {"_mcap_multiples": {"value": usd_km * 7.84}}) is False


# ── 2./3. banks ──────────────────────────────────────────────────────────────

def test_interest_heavy_marks_a_bank_not_a_fee_financial():
    ocbc = [{"interest_expense": 11.2e9, "revenue": 24.7e9}, {"interest_expense": 10.9e9, "revenue": 26.1e9}]
    sgx = [{"interest_expense": 7.3e6, "revenue": 1.19e9}, {"interest_expense": 17.3e6, "revenue": 1.37e9}]
    assert d._interest_heavy(ocbc) is True
    assert d._interest_heavy(sgx) is False
    assert d._interest_heavy([]) is False


def test_bank_pe_norm_prices_own_mean_roe_on_book(monkeypatch):
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: {})
    row = {"total_equity": 69.0e9, "_roe_mean_5y": 0.1492, "_roe_mean_5y_n": 5,
           "normalized_net_income": 12.75e9, "net_income": 10.9e9}
    v, tr = d._traced_method_value(method_name="P/E (norm)", most_recent=row, revenue_base=37.9e9, shares=2.856e9,
                                   net_debt=0.0, market_cap=1.9e11, wacc=0.08, growth_base=0.03, fcf_margin_base=0.2,
                                   tgr=0.02, fcf_floor=0.0, sector="Financials", scenario="base",
                                   profile_name="Money Center Bank (SG)", ticker="D05.SI")
    pe = d._bank_profile_calibration("Money Center Bank (SG)")["pe"]
    assert v == pytest.approx(69.0e9 * 0.1492 / 2.856e9 * pe, rel=1e-6)
    assert "own 5y mean ROE 14.9%" in tr["metric"]


# ── 4. S-REIT DDM ────────────────────────────────────────────────────────────

def test_sreit_ddm_capitalises_d1():
    row = {"_sreit_dpu_cents_research": 10.88}
    value, dpu_fwd, a = d._compute_sreit_ddm("C38U.SI", "commercial", row, 1e9, dpu_growth=0.02)
    assert dpu_fwd == pytest.approx(0.1088 * 1.02, rel=1e-6)
    assert value == pytest.approx(dpu_fwd / (a["coe"] - a["g"]), rel=1e-4)


# ── 5. DDM cost of equity ────────────────────────────────────────────────────

def test_ddm_discounts_at_the_firm_ke_when_no_owner_row():
    ke, src = d._ddm_cost_of_equity("Telecom Carrier", "00941.HK", 0.066, firm_ke=0.0660)
    assert ke == pytest.approx(0.066) and "firm CAPM" in src
    ke2, src2 = d._ddm_cost_of_equity("Telecom Carrier", "00941.HK", 0.066)
    assert ke2 > 0.066 and "spread" in src2


# ── 6. forecast tax rate ─────────────────────────────────────────────────────

def test_forecast_tax_rate_is_the_filed_effective_rate():
    cfg = gf.DEFAULTS if isinstance(gf.DEFAULTS, dict) else {}
    # China Mobile-like: FMP EBIT below pretax (associates, investment income) -- the identity read ~5%
    rows = [{"revenue": 1000.0, "ebit": 150.0, "net_income": 134.0, "interest_expense": 3.6,
             "income_tax_expense": 37.4, "pretax_income": 171.0} for _ in range(4)]
    out = gf.history_ratios(rows, {**cfg})
    assert out["tax_rate"] == pytest.approx(37.4 / 171.0, rel=1e-6)


# ── 7. static defaults are said to be defaults ───────────────────────────────

def test_a_leg_on_a_branch_literal_is_labelled_a_static_default(monkeypatch):
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: {"_comp_market": "SES"})
    row = {"ebitda": 4.8e9, "net_income": 1.0e9}
    v, tr = d._traced_method_value(method_name="EV/EBITDA", most_recent=row, revenue_base=19e9, shares=3.16e9,
                                   net_debt=1.3e9, market_cap=2.2e10, wacc=0.08, growth_base=0.03,
                                   fcf_margin_base=0.1, tgr=0.02, fcf_floor=0.0, sector="Industrials",
                                   scenario="base", profile_name="Airlines", ticker="C6L.SI")
    assert v is not None and tr["peer_static_default"]
    assert tr["multiple_parts"]["peer_source"].startswith("static default")


def test_peer_fields_required_exempts_bank_calibrated_legs():
    assert d._peer_fields_required("P/E (norm)", "Money Center Bank (SG)", "Financials") == ()
    assert d._peer_fields_required("P/E (norm)", "Airlines", "Industrials") == ("pe", "pe_ntm")
    assert d._peer_fields_required("DCF", "Airlines", "Industrials") == ()


# ── 8. netted holdings: their income is not capitalised twice ─────────────────

def test_netted_holdings_put_the_ev_legs_on_operating_earnings():
    row = {"operating_income": 145.2e9, "depreciation_and_amortization": 188.0e9, "ebitda": 362.8e9, "ebit": 174.6e9}
    msg = d._operating_earnings_for_netted_holdings(row)
    assert row["ebitda_core"] == pytest.approx(333.2e9) and row["ebit_core"] == pytest.approx(145.2e9)
    assert msg and "operating earnings" in msg
    # never raises a figure that is already below the operating line
    low = {"operating_income": 10.0, "depreciation_and_amortization": 2.0, "ebitda": 11.0, "ebit": 9.0}
    assert d._operating_earnings_for_netted_holdings(low) is None and "ebitda_core" not in low
