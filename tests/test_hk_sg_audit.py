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


# ── owner decisions, 2026-11-25 ──────────────────────────────────────────────

def test_sg_cash_flows_discount_on_the_sgs_10y():
    from src.data import capm
    rf, src = capm.risk_free("SG")
    assert rf == pytest.approx(0.0229) and "Singapore Government" in src
    assert capm.risk_free("HK")[0] != pytest.approx(0.0229)          # HKD peg: US rates
    w = capm.firm_wacc(profile="Telecom Carrier", net_debt=0.0, market_cap=7e10, cost_of_debt=None, market="SG")
    assert w and w["rf"] == pytest.approx(0.0229)


def test_bank_ri_terminal_is_the_persistence_fade():
    assert d._RI_PERSISTENCE == pytest.approx(0.80)
    import inspect
    src = inspect.getsource(d._compute_residual_income_2stage)
    assert "(1.0 + coe - _RI_PERSISTENCE)" in src and "terminal_spread * bvps_t" not in src


def test_thin_sgx_profiles_have_cross_market_baskets():
    for prof in ("Airlines", "Market Infrastructure (SG)", "Agribusiness & Food (SG)"):
        spec = rc.CROSS_MARKET_BASKETS[prof]
        assert "SES" in spec["markets"] and len(spec["symbols"]) >= 6
    assert not {"SPGI", "MCO", "MSCI"} & set(rc.CROSS_MARKET_BASKETS["Market Infrastructure (SG)"]["symbols"])


# ── life insurers on cohort P/EV (owner, 2026-11-25) ─────────────────────────

def test_cohort_pev_is_the_median_without_the_subject():
    from src.data import insurer_pev as ip
    px = {"02628.HK": 20.0, "02601.HK": 30.0, "01336.HK": 40.0, "01339.HK": 5.0, "02318.HK": 50.0}
    ev = {"02628.HK": 50.0, "02601.HK": 60.0, "01336.HK": 80.0}            # PICC has no accepted EV
    out = ip.cohort_pev("02318.HK", quote=px.get, ev_ps=lambda s, c: ev.get(s), listing_ccy=lambda s: "HKD")
    assert out["cohort"] == "china_domestic" and out["value"] == pytest.approx(0.5)   # 0.4, 0.5, 0.5
    assert "02318.HK" not in [m["symbol"] for m in out["members_used"]] and "01339.HK" in out["members_missing"]
    thin = ip.cohort_pev("02318.HK", quote=px.get, ev_ps=lambda s, c: ev.get(s) if s == "02628.HK" else None,
                         listing_ccy=lambda s: "HKD")
    assert thin["value"] is None
    assert ip.cohort_pev("D05.SI") is None


def test_embedded_value_leg_prices_at_the_cohort_pev(monkeypatch):
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: {"pb": 1.2})
    row = {"embedded_value_per_share": 101.66, "_insurer_pev": {"value": 0.5, "cohort": "china_domestic",
                                                                "members_used": [{"symbol": "02628.HK"}, {"symbol": "02601.HK"}]}}
    kw = dict(method_name="Embedded Value", revenue_base=1e12, shares=1.8e10, net_debt=0.0, market_cap=9e11,
              wacc=0.08, growth_base=0.03, fcf_margin_base=0.1, tgr=0.02, fcf_floor=0.0, sector="Financials",
              scenario="base", profile_name="Insurance", ticker="02318.HK")
    v, tr = d._traced_method_value(most_recent=row, **kw)
    assert v == pytest.approx(101.66 * 0.5) and "median P/EV" in tr["multiple_parts"]["peer_source"]
    v1, tr1 = d._traced_method_value(most_recent={"embedded_value_per_share": 101.66}, **kw)
    assert v1 == pytest.approx(101.66) and "1.0x" in tr1["multiple_parts"]["peer_source"]


# ── A/H dual listings priced at the H-share quote ────────────────────────────

def test_an_h_share_is_restated_from_the_a_share_price():
    # China Telecom (0728.HK): key metrics on the A-share price, HK$757.7bn, against an H-share cap of HK$498bn
    mm = {"mcap_multiples": 757.7e9, "ev_multiples": 800.0e9}
    vals = {"ev_ebitda": {"value": 7.41, "in_band": True}, "pe": {"value": 22.7, "in_band": True},
            "fcf_yield": {"value": 0.05, "in_band": True}, "roic": {"value": 0.06, "in_band": True}}
    rc._restate_at_listing_price("0728.HK", 498.0e9, mm, vals)
    r = 498.0 / 757.7
    assert vals["pe"]["value"] == pytest.approx(22.7 * r)
    assert vals["ev_ebitda"]["value"] == pytest.approx(7.41 * (800.0 - 757.7 + 498.0) / 800.0)
    assert vals["fcf_yield"]["value"] == pytest.approx(0.05 / r) and vals["roic"]["value"] == 0.06
    assert mm["mcap_multiples"] == 498.0e9


def test_restatement_leaves_aligned_and_non_hk_lines_alone():
    for sym, cap in (("0005.HK", 2200e9), ("AAPL", 2.0e12)):
        mm, vals = {"mcap_multiples": 2178e9, "ev_multiples": 2300e9}, {"pe": {"value": 10.0, "in_band": True}}
        rc._restate_at_listing_price(sym, cap, mm, vals)
        assert vals["pe"]["value"] == 10.0


# ── non-cyclical normalisation leaves out the pre-profit ramp (owner, 2026-11-25) ──

def test_ex_ramp_drops_only_the_leading_loss_years():
    rows = [{"revenue": 100.0, "ebitda": m * 100.0} for m in (-0.076, 0.014, 0.085, 0.123, -0.057)]   # Meituan
    xr = d._normalized_ex_ramp(rows, "ebitda")
    assert xr is not None and xr == pytest.approx(d._normalized_earnings(rows[1:], "ebitda", window=4))
    assert d._normalized_ex_ramp([{"revenue": 1.0, "ebitda": 0.1}] * 5, "ebitda") is None        # nothing to drop
    assert d._normalized_ex_ramp([{"revenue": 1.0, "ebitda": v} for v in (-1, -1, -1, 0.1, 0.1)], "ebitda") is None


# ── HKSE utility CoE row by currency; H-share withholding tax (owner, 2026-11-25) ──

def test_the_cgb_utility_row_prices_rmb_cash_flows_only():
    from src.data import valuation_constants as vc
    assert vc.cost_of_equity("Regulated Utility", "HKSE", cash_flow_ccy="CNY") == pytest.approx(0.06)
    assert vc.cost_of_equity("Regulated Utility", "HKSE", cash_flow_ccy="HKD") is None
    assert vc.cost_of_equity("Regulated Utility", "HKSE") == pytest.approx(0.06)          # no currency given: unchanged
    ke, src = d._ddm_cost_of_equity("Regulated Utility", "00002.HK", 0.0614, firm_ke=0.069, cash_flow_ccy="HKD")
    assert ke == pytest.approx(0.069) and "firm CAPM" in src


def test_h_share_dividends_are_net_of_prc_withholding(monkeypatch):
    from src.data import regional_comps as _rc
    monkeypatch.setattr(_rc, "get_fmp_classification",
                        lambda t: {"isin": {"01398.HK": "CNE1000003G1", "00941.HK": "HK0941009539"}.get(t, "")})
    assert d._is_h_share("01398.HK") and not d._is_h_share("00941.HK") and not d._is_h_share("D05.SI")
    monkeypatch.setattr(d, "get_sector_peer_multiples", lambda *a, **k: {})
    kw = dict(method_name="DDM", revenue_base=1e12, shares=1e10, net_debt=0.0, market_cap=1e12, wacc=0.08,
              growth_base=0.03, fcf_margin_base=0.1, tgr=0.02, fcf_floor=0.0, sector="Financials", scenario="base",
              profile_name="Telecom Carrier")
    row = {"dividends_per_share": 0.30, "net_income": 1e11, "free_cash_flow": 1e11, "_firm_cost_of_equity": 0.08}
    v_h, tr = d._traced_method_value(most_recent=dict(row), ticker="01398.HK", **kw)
    v_r, _ = d._traced_method_value(most_recent=dict(row), ticker="00941.HK", **kw)
    assert v_h == pytest.approx(v_r * 0.9) and tr["withholding_tax"] == pytest.approx(0.10)


# ── workbook audit follow-ups (2026-11-25) ───────────────────────────────────

def test_kd_moves_onto_the_local_curve_with_the_rf():
    from src.data import capm
    us_rf = capm.risk_free("US")[0]
    w = capm.firm_wacc(profile="Telecom Carrier", net_debt=9.4e9, market_cap=69e9, cost_of_debt=us_rf + 0.0023,
                       market="SG", own_beta=0.6)
    assert w["cost_of_debt_rebased"] and w["cost_of_debt"] == pytest.approx(0.0229 + 0.0023, abs=1e-5)
    assert w["cost_of_equity"] > w["cost_of_debt"]
    us = capm.firm_wacc(profile="Telecom Carrier", net_debt=9.4e9, market_cap=69e9, cost_of_debt=0.055, market="US")
    assert not us["cost_of_debt_rebased"] and us["cost_of_debt"] == pytest.approx(0.055)


def test_company_tax_rate_prefers_the_filed_rate_then_the_market_statutory_rate():
    rows = [{"income_tax_expense": 17.0, "pretax_income": 100.0}, {"income_tax_expense": 18.0, "pretax_income": 100.0}]
    assert d._company_tax_rate(rows, "C6L.SI")[0] == pytest.approx(0.175)
    assert d._company_tax_rate([], "C6L.SI") == (0.17, "SG statutory rate")
    assert d._company_tax_rate([], "00941.HK", "CNY")[0] == pytest.approx(0.25)


def test_insurer_pe_cohort_excludes_the_subject():
    from src.data import insurer_pev as ip
    pe = {"02378.HK": 12.0, "MFC": 14.0, "01828.HK": 20.0, "G07.SI": 10.0, "01299.HK": 15.0}
    out = ip.cohort_pe("01299.HK", pe=pe.get)
    assert out["value"] == pytest.approx(13.0) and "01299.HK" not in [m["symbol"] for m in out["members_used"]]
