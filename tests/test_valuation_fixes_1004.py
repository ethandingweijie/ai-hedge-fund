"""Plan 2026-10 Phase 1 (owner, 2026-10-04): the engine-wide fixes from the BIRK / MRNA / 9618.HK / BA
workbook reviews, pinned with the case that motivated each.

D1 delete a zero-weight DCF; D2 dated mid-year discounting; D3 listed minorities at market;
D5 risk-on band; D6 cascaded margins fade; D7 a failed anchor degrades; D8 company-specific spread.
"""
import copy
import importlib.util
import io
import os
import tempfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.agents.analysis import dcf_agent as d
from src.agents.analysis import guidance_forecast as gfm
from src.agents.analysis import three_statement as ts
from src.utils.valuation_workbook import build_workbook

_spec = importlib.util.spec_from_file_location("_wbtests", Path(__file__).resolve().parent / "test_valuation_workbook.py")
_wbt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wbt)


# ── D2: dating ─────────────────────────────────────────────────────────────────

def test_birk_timing_counts_a_quarter_of_fy26_and_carries_to_the_valuation_date():
    tm = d._dcf_timing("2025-09-30", "2026-06-30", "2026-10-04")
    assert tm["flow_fractions"][0] == pytest.approx(92 / 365, abs=1e-3)       # Jul–Sep 2026 still to come
    assert tm["flow_fractions"][1:] == [1.0] * 9
    assert tm["roll_forward_years"] == pytest.approx(96 / 365.25, abs=1e-4)


def test_a_year_already_in_the_balance_sheet_contributes_no_flow():
    # FY0+1 ended before the balance-sheet date: its cash is in the bridge already.
    tm = d._dcf_timing("2024-12-31", "2026-03-31", "2026-04-15")
    assert tm["flow_fractions"][0] == 0.0
    assert 0.0 < tm["flow_fractions"][1] < 1.0


def test_undated_projection_is_the_legacy_year_end_projection():
    a = d._project_dcf(1000.0, 0.2, 0.05, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0)
    b = d._project_dcf(1000.0, 0.2, 0.05, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0, timing=None)
    assert a[0] == pytest.approx(b[0])


def test_dated_projection_is_mid_year_and_stubbed():
    tm = d._dcf_timing("2025-12-31", "2026-06-30", "2026-06-30")
    iv, pv_f, pv_t, rows = d._project_dcf(1000.0, 0.2, 0.0, 0.0, 0.10, 0.0, -0.05, 0.0, 1.0, timing=tm)
    f1 = rows[0]["flow_fraction"]
    assert rows[0]["fcf"] == pytest.approx(200.0 * f1)
    assert rows[0]["discount_factor"] == pytest.approx(1.1 ** (-f1 / 2))
    assert rows[1]["discount_factor"] == pytest.approx(1.1 ** (-(f1 + 0.5)))


def _dated_run():
    tm = d._dcf_timing("2025-09-30", "2026-06-30", "2026-10-04")
    sched = [0.18, 0.19, 0.2, 0.2, 0.21, 0.21, 0.21, 0.21, 0.21, 0.21]
    iv, pv_f, pv_t, rows = d._project_dcf(1000.0, 0.2, 0.08, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0,
                                          margin_schedule=sched, minority_interest=40.0, preferred_equity=5.0,
                                          timing=tm)
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        legs = copy.deepcopy(dr[s]["leg_inputs"])
        legs["DCF"].update(value=iv, pv_fcf_per_share=pv_f, pv_tv_per_share=pv_t, projection_rows=rows,
                           growth_schedule=None, growth_base=0.08, timing=tm, minority_interest=40.0,
                           preferred_equity=5.0)
        dr[s]["leg_inputs"] = legs
        dr[s]["method_iv_table"]["DCF"] = iv
        dr[s]["intrinsic_value"] = round(0.4 * iv + 0.4 * 230.0 + 0.2 * 180.0, 2)
    iv_b = dr["base"]["intrinsic_value"]
    dr["12m_targets"] = {s: round(150.0 + 0.35 * (iv_b - 150.0), 2) for s in ("bear", "base", "bull")}
    return run, iv


def test_the_workbook_rebuilds_a_dated_projection_with_a_margin_path_to_zero():
    formulas = pytest.importorskip("formulas")
    run, iv = _dated_run()
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "dated.xlsx")
        Path(p).write_bytes(build_workbook(run, "TEST"))
        sol = {k.upper(): v for k, v in formulas.ExcelModel().loads(p).finish().calculate().items()}
        ws = load_workbook(p)["DCF"]
        checks = [c.row for c in ws["A"] if c.value == "Check"]
        assert checks
        for r in checks:
            v = sol[f"'[DATED.XLSX]DCF'!B{r}".upper()]
            v = getattr(v, "value", v)[0][0]
            assert abs(v) < 1e-6, (r, v)


# ── 1A: the workbook ───────────────────────────────────────────────────────────

def test_d1_an_unweighted_dcf_has_no_tab_and_one_summary_line():
    run = _wbt._run_with({}, [{"method": "EV/EBITDA", "value_key": "EV/EBITDA", "bucket": "multi", "weight": 0.6},
                              {"method": "P/E", "value_key": "P/E", "bucket": "multi", "weight": 0.4}])
    wb = load_workbook(io.BytesIO(build_workbook(run, "TEST")))
    assert "DCF" not in wb.sheetnames
    lines = [str(c.value) for c in wb["Summary"]["A"] if c.value]
    assert sum(1 for x in lines if x.startswith("DCF EXCLUDED from the blend")) == 1


def test_capture_is_stated_with_its_reason():
    run = _wbt._run()
    run["data"]["dcf_range"]["TEST"]["pt_bridge"]["capture_reason"] = "35%: the standard rule"
    wb = load_workbook(io.BytesIO(build_workbook(run, "TEST")))
    assert any("35%: the standard rule" in str(c.value) for c in wb["Assumptions"]["A"] if c.value)


# ── 1C: the bridge ─────────────────────────────────────────────────────────────

def _row(**kw):
    base = {"net_debt": 100.0, "total_debt": 150.0, "cash_and_equivalents": 50.0, "short_term_investments": 0.0,
            "lease_liabilities": None, "period": "2025-12-31"}
    base.update(kw)
    return base


def test_us_gaap_leases_come_out_ifrs_leases_stay():
    us, b_us = d._valuation_net_debt(_row(lease_liabilities=30.0), "Consumer", "SBUX", "USD")
    ifrs, b_ifrs = d._valuation_net_debt(_row(lease_liabilities=30.0), "Consumer", "BIRK", "EUR")
    jd, _ = d._valuation_net_debt(_row(lease_liabilities=30.0), "Consumer", "09618.HK", "CNY")
    assert us == 70.0 and jd == 70.0 and ifrs == 100.0
    assert b_us["accounting_basis"] == "US GAAP" and b_ifrs["accounting_basis"] == "IFRS"


def test_biotech_treasury_is_cash_managed_care_float_is_not():
    r = _row(net_debt=-500.0, total_debt=1240.0, cash_and_equivalents=1740.0, short_term_investments=3200.0)
    biotech, _ = d._valuation_net_debt(dict(r), "Healthcare", "MRNA", "USD", "Biotechnology")
    plan, _ = d._valuation_net_debt(dict(r), "Healthcare", "XMCO", "USD", "Medical - Healthcare Plans")
    assert biotech == -3700.0
    # Plan IN1: a managed-care plan's cash and investments are regulated capital; with no parent-only
    # cash on record, net debt is the gross debt.
    assert plan == 1240.0


def test_interest_income_leaves_unlevered_fcf():
    from types import SimpleNamespace
    li = SimpleNamespace(report_period="2025-12-31", currency="CNY", revenue=1000.0, free_cash_flow=100.0,
                         interest_expense=10.0, interest_income=20.0, ebit=200.0, net_income=142.5)
    rows, _ = d._extract_annual_series([li])
    r = rows[-1]
    tax = r["ufcf_tax_rate"]
    assert r["ufcf_owner_earnings"] == pytest.approx(r["fcf_owner_earnings"] + 10.0 * (1 - tax) - 20.0 * (1 - tax))
    assert r["ufcf_interest_income_removed"] == pytest.approx(20.0 * (1 - tax))


def test_boeing_disposal_gain_is_not_core_earnings():
    prior = [{"operating_income": 1e9, "ebitda": 3.2e9, "depreciation_and_amortization": 2e9, "revenue": 80e9}] * 3
    ce = d._core_earnings({"operating_income": -5.42e9, "ebitda": 7.36e9, "depreciation_and_amortization": 1.95e9,
                           "revenue": 89.46e9, "net_income": 2.23e9, "ufcf_tax_rate": 0.15}, prior)
    assert ce["ebitda_core"] == pytest.approx(-3.47e9 + 0.2e9)        # the usual 0.2bn below the line stays
    assert ce["net_income_core"] < 0
    assert d._core_earnings({"operating_income": 10e9, "ebitda": 13.1e9, "depreciation_and_amortization": 3e9,
                             "revenue": 50e9, "net_income": 7e9}) == {}


def test_recurring_associate_income_is_core():
    # Sembcorp-shaped: a large share of associates' profit below the operating line, every year.
    rows = [{"operating_income": 0.5e9, "ebitda": 1.8e9, "depreciation_and_amortization": 0.3e9, "revenue": 7e9}] * 4
    assert d._core_earnings({"operating_income": 0.55e9, "ebitda": 1.9e9, "depreciation_and_amortization": 0.3e9,
                             "revenue": 7.2e9, "net_income": 1.0e9}, rows) == {}


# ── 1B / 1E: the guidance forecast ─────────────────────────────────────────────

_HIST = {"revenue": 1000.0, "ebit": 200.0, "ebit_margin": 0.20, "da": 60.0, "da_pct_revenue": 0.06, "capex": 70.0,
         "net_income": 140.0, "interest": 10.0, "shares": 100.0, "tax_rate": 0.25, "tax_rate_source": "history",
         "capex_alpha": 0.1, "capex_alpha_n": 3, "nwc_intensity": 0.05, "nwc_n": 3, "buyback_median": 0.0,
         "roic_median": 0.15, "years": 5, "fy0": 2025}


def _fc(block, **kw):
    return gfm.build_forecast(block, scenario="base", series=None, hist=dict(_HIST), profile_name="Generic", sector="Consumer",
                              wacc=0.09, tgr=0.025, shares=100.0, net_debt=0.0, **kw)


def test_an_ebitda_margin_endpoint_is_converted_to_ebit():
    fc = _fc({"fiscal_year_1": "FY2026", "estimates": {"base": {"revenue_growth_fy1": 0.10, "ebitda_margin_fy1": 0.30}}})
    assert fc["margin_target"] == pytest.approx(0.24)


def test_research_fy1_a_year_late_runs_the_engine_path_first():
    fc = _fc({"fiscal_year_1": "FY2027", "estimates": {"base": {"revenue_growth_fy1": 0.03, "revenue_growth_fy2": 0.04}}},
             engine_growth_path=[-0.02] * 10)
    assert fc["growth_schedule"][:3] == pytest.approx([-0.02, 0.03, 0.04])
    assert fc["rows"][0]["phase"] == "engine path"


def test_guided_eps_is_converted_into_the_valuation_currency():
    blk = {"fiscal_year_1": "FY2026", "guidance": {"eps": {"currency": "EUR"}},
           "estimates": {"base": {"revenue_growth_fy1": 0.10, "eps_fy1": 1.0}}}
    a = _fc(blk)
    b = _fc(blk, fx_to_valuation=1.2, valuation_currency="USD")
    assert b["deconstruction"]["eps_T_guided"] == pytest.approx(1.2 * a["deconstruction"]["eps_T_guided"])


def test_three_statements_are_built_in_the_statement_currency():
    fc = {"rows": [{"revenue": 1120.0, "growth": 0.0, "ebit": 112.0, "da": 56.0, "capex": 56.0, "delta_nwc": 0.0}],
          "history": {"tax_rate": 0.25}}
    op = {"fiscal_year": 2025, "revenue": 1000.0, "cash": 100.0, "equity": 400.0, "ppe": 300.0, "payables": 0.0}
    out = ts.build(fc, op, {}, years=1, fx=1.12)
    assert out["income"]["revenue"][0] == pytest.approx(1000.0)
    assert out["income"]["growth"][0] == pytest.approx(0.0)


# ── 1F: blend and peers ────────────────────────────────────────────────────────

def test_d7_a_failed_anchor_moves_its_weight_to_the_dcf_and_says_degraded():
    methods = [{"name": "EV/EBIT (norm)", "weight": 0.3, "anchor": True},
               {"name": "DCF", "weight": 0.2}, {"name": "P/E", "weight": 0.5}]
    flags: list = []
    iv, bd = d._blend_methods(methods, {"EV/EBIT (norm)": None, "DCF": 50.0, "P/E": 100.0}, 1.0, flags, 0.5)
    assert bd["anchor_degraded"]["moved_to"] == "the DCF"
    assert iv == pytest.approx((0.5 * 50.0 + 0.5 * 100.0))
    assert any(f.startswith("DEGRADED") for f in flags)


def test_the_valued_company_is_not_in_its_own_curated_basket(monkeypatch):
    from src.data import regional_comps as rc
    seen = {}

    def _q(sql, params):
        seen["params"] = params
        return []
    monkeypatch.setattr(rc._db, "query", _q)
    monkeypatch.setattr(rc, "_ensure_table", lambda: None)
    rc.basket_multiples("HKSE", ("09988.HK", "00700.HK"), "China Internet Platform", exclude="09988.HK")
    assert "9988.HK" not in seen["params"] and "0700.HK" in seen["params"]


# ── Phase 1b (MOH / JD / D05 / 9988 reviews) ───────────────────────────────────

def test_en2_recovery_discount_prices_the_excess_when_consensus_reaches_it():
    v, info = d._recovery_discounted(1220.0, [274.0, 532.0, 700.0, 900.0, 1240.0], 0.08)
    assert info["years_to_recover"] == 5
    assert v == pytest.approx(274.0 + (1220.0 - 274.0) / 1.08 ** 5)
    assert d._recovery_discounted(200.0, [274.0], 0.08) == (200.0, None)          # below forward: unchanged


def test_en3_an_incompatible_margin_gives_way_to_guided_eps():
    blk = {"fiscal_year_1": "FY2026", "estimates": {"base": {"revenue_growth_fy1": 0.05, "ebitda_margin_fy1": 0.30, "eps_fy1": 0.5}}}
    fc = _fc(blk)
    dec = fc["deconstruction"]
    assert "margin_T_conflicting" in dec and dec["margin_T_guided"] < 0.10
    assert any("guided EPS implies" in f or "not compatible" in f for f in fc["flags"])


def test_in2_bank_scenarios_move_drivers_not_the_answer():
    import inspect
    src = inspect.getsource(d._compute_method_value)
    assert "_BANK_SCENARIO_ROE_SHIFT" in src and "_BANK_SCENARIO_COE_SHIFT" in src


def test_managed_care_regulated_cash_matches_the_engines_sector_name():
    # Molina resolves to "HealthcareServices"; the guard keyed on "Healthcare" never fired.
    r = {"net_debt": -4000.0, "total_debt": 4000.0, "cash_and_equivalents": 5000.0, "short_term_investments": 3000.0,
         "lease_liabilities": 184.0, "period": "2025-12-31"}
    nd, b = d._valuation_net_debt(dict(r), "HealthcareServices", "XMCO", "USD", "Medical - Healthcare Plans")
    assert b["regulated_cash_excluded"] is True and nd == 4000.0 - 184.0


def test_an_accepted_parent_cash_entry_counts_and_a_proposed_one_does_not(monkeypatch):
    from src.data import valuation_constants as vc
    doc = {"parent_cash": {"entries": {"MOH": {"value": 290e6, "status": "ACCEPTED"}, "XMCO": {"value": 1e9, "status": "PROPOSED"}}}}
    monkeypatch.setattr(vc, "load", lambda *a, **k: doc)
    assert d._parent_cash("MOH") == 290e6 and d._parent_cash("XMCO") is None


# ── Visa / Vertex reviews (owner, 2026-10-04): EV2, EV7, IV1, IV3 ───────────────

def test_ev2_routine_insider_selling_does_not_move_wacc():
    # Visa: $83m of sales (~0.01% of a ~$700bn cap) with a CEO/CFO conviction-sell flag added +16bp.
    bps, _ = d._insider_wacc_modifier({"net_buying_12m_usd": -83e6, "gross_sell_value_12m": 83e6,
                                       "conviction_sell_flag": True}, 700e9)
    assert bps == 0.0
    # Material selling (1% of cap) still widens.
    bps2, _ = d._insider_wacc_modifier({"net_buying_12m_usd": -7e9, "gross_sell_value_12m": 7e9}, 700e9)
    assert bps2 > 0
    # Buying is unaffected by the selling threshold.
    bps3, _ = d._insider_wacc_modifier({"net_buying_12m_usd": 1.4e9, "gross_buy_value_12m": 1.4e9}, 700e9)
    assert bps3 < 0


def test_ev7_terminal_multiple_band_is_two_sided_and_roic_aware():
    import inspect
    src = inspect.getsource(gfm)
    assert "ROIC-justified" in src and "BELOW the floor" in src


def test_iv1_payment_networks_take_the_unlevered_basis():
    assert d._interest_is_cost_of_goods("Payment Networks", "Financials") is False
    assert d._interest_is_cost_of_goods("Asset Manager", "Financials") is True
    assert d._interest_is_cost_of_goods("Money Center Bank", "Financials") is True


def test_iv3_biotech_long_term_securities_are_cash_pharma_stakes_are_not():
    r = _row(net_debt=-500.0, total_debt=1240.0, cash_and_equivalents=1740.0, short_term_investments=3200.0,
             long_term_investments=10000.0)
    biotech, b = d._valuation_net_debt(dict(r), "Healthcare", "VRTX", "USD", "Biotechnology")
    pharma, _ = d._valuation_net_debt(dict(r), "Healthcare", "PFE", "USD", "Drug Manufacturers - General")
    assert biotech == -13700.0 and b["long_term_investments_netted"] == 10000.0
    assert pharma == -3700.0


def test_iv2_pipeline_is_an_add_on_for_revenue_stage_drug_profiles():
    assert "Commercial Biotech" in d._PIPELINE_ADDON_PROFILES
    assert "Pre-approval Biotech" not in d._PIPELINE_ADDON_PROFILES
    # An approved product's sales are in the operating legs' revenue: the add-on carries unapproved assets only.
    import inspect
    assert 'a.get("phase") != "approved"' in inspect.getsource(d)


def test_ev6_ntm_roll_on_the_guidance_overlay():
    # Vertex, October 2026: FY2026 EPS $17.45 is three-quarters gone; NTM weights FY2027's $20.50 at 75%.
    est = {"confidence": "HIGH",
           "estimates": {"base": {"eps_fy1": 17.45, "eps_fy2": 20.50, "revenue_growth_fy1": 0.10,
                                  "revenue_growth_fy2": 0.10, "ebitda_margin_fy1": 0.45, "ebitda_margin_fy2": 0.45}}}
    fwd = {"eps": {"base": 18.0}, "ebitda": {"base": 1.0}, "revenue": {"base": 1.0}, "ebit": {"base": 1.0}}
    o0 = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", None, 1000.0)
    o75 = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", None, 1000.0, ntm_e=0.75)
    assert o0["eps"]["base"] == pytest.approx(17.45)
    assert o75["eps"]["base"] == pytest.approx(0.25 * 17.45 + 0.75 * 20.50)
    assert o75["revenue"]["base"] == pytest.approx(1100.0 * (0.25 + 0.75 * 1.10))


def test_bridge_adjustments_price_only_when_accepted(monkeypatch):
    from src.data import valuation_constants as vc
    doc = {"bridge_adjustments": {"entries": {"V": {"debt_like": 363.0, "status": "ACCEPTED"},
                                              "MA": {"debt_like": 999.0, "status": "PROPOSED"}}}}
    monkeypatch.setattr(vc, "load", lambda *a, **k: doc)
    v, b = d._valuation_net_debt(_row(), "Financials", "V", "USD")
    ma, _ = d._valuation_net_debt(_row(), "Financials", "MA", "USD")
    assert v == 463.0 and b["debt_like_items"] == 363.0 and ma == 100.0


def test_the_visa_entry_is_owner_accepted():
    from src.data import valuation_constants as vc
    e = vc.load()["bridge_adjustments"]["entries"]["V"]
    assert e["status"] == "ACCEPTED" and e["preferred_in_shares"] is True
    assert d._bridge_adjustment("V")["shares_as_converted"] == 1880000000.0


def test_d8_scenario_wacc_shift_is_removed():
    assert set(d._WACC_SCENARIO_SHIFT.values()) == {0.0}


def test_payment_networks_price_on_the_toll_road_basket():
    from src.data.regional_comps import PROFILE_PEER_BASKETS
    assert PROFILE_PEER_BASKETS["Payment Networks"]["US"] == ("V", "MA", "AXP", "SPGI", "MCO", "ICE", "CME", "MSCI")


def test_guidance_eps_on_the_forward_legs_is_converted_into_the_valuation_currency():
    # IHH (2026-10-06): RM0.26 / RM0.29 guided EPS sat unconverted in an SGD forward P/E (S$5.09 vs ~S$1.59).
    est = {"confidence": "HIGH", "estimates": {"base": {"eps_fy1": 0.26, "eps_fy2": 0.29}}}
    fwd = {"eps": {"base": 0.1}, "ebitda": {"base": 1.0}, "revenue": {"base": 1.0}, "ebit": {"base": 1.0}}
    o = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", None, None, ntm_e=0.76,
                                    fx_to_valuation=0.31288, valuation_currency="SGD")
    assert o["eps"]["base"] == pytest.approx((0.24 * 0.26 + 0.76 * 0.29) * 0.31288)
    est["guidance"] = {"eps": {"currency": "SGD"}}
    o2 = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", None, None, fx_to_valuation=0.31288,
                                     valuation_currency="SGD")
    assert o2["eps"]["base"] == pytest.approx(0.26)


def test_the_subject_exclusion_and_frozen_members_reach_the_payload(monkeypatch):
    # 2026-10-06: computed in get_regional_multiples, dropped in get_sector_peer_multiples' basis copy.
    from src.data import sector_profiles as sp
    row = {"value": 46.36, "basis": "industry", "cohort": "large", "peer_count": 5, "key": "Biotechnology",
           "exchange": "HKSE", "subject_excluded": True, "value_with_subject": 52.91,
           "members_used": [{"symbol": "6160.HK", "value": 40.0}]}
    import inspect
    src = inspect.getsource(sp.get_sector_peer_multiples)
    assert '"subject_excluded", "members_used", "excluded_no_market_cap", "value_with_subject"' in src
    tr = d._multiples_trace({"pe": 46.36, "_comp_basis": {"pe": row}})
    assert tr["fields"]["pe"]["subject_excluded"] is True and tr["fields"]["pe"]["members_used"]


def test_e26_holders_and_institutions_are_not_insiders():
    from src.agents.intelligence.insider_activity_agent import _is_reportable_insider as rep
    assert rep("10% Owner", "LILLY ENDOWMENT INC") is False
    assert rep(None, "Lilly Endowment Inc") is False
    assert rep("Director, 10% Owner", "Jane Doe") is True
    assert rep("EVP & CFO", "John Smith") is True


def test_e16_cash_runway_is_computed_and_overrides_the_extractor(monkeypatch):
    from src.data import sector_kpi_framework as k
    monkeypatch.setattr(k, "_fmp_risk_kpis", lambda t: {"cash_runway_qtrs": 16.6, "cash_runway_years": 4.15})
    out = k._augment_metrics_with_fmp_risk("MRNA", {"cash_runway_qtrs": 1.7})
    assert out["cash_runway_qtrs"] == 16.6


def test_e24_a_research_fy1_without_fy2_rolls_on_consensus_growth_and_says_so():
    est = {"confidence": "HIGH", "estimates": {"base": {"eps_fy1": 36.0}}}
    fwd = {"eps": {"base": 40.0}, "ebitda": {"base": 1.0}, "revenue": {"base": 1.0}, "ebit": {"base": 1.0},
           "_fy1_fy2": {"eps": {"base": (34.0, 44.2)}}}
    o = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", None, None, ntm_e=0.76)
    assert o["eps"]["base"] == pytest.approx(0.24 * 36.0 + 0.76 * 36.0 * 44.2 / 34.0)
    assert "consensus FY+2/FY+1 growth" in o["_source"]["eps"]["base"]
    o2 = d._guidance_forward_overlay(est, {**copy.deepcopy(fwd), "_fy1_fy2": {}}, "base", None, None, ntm_e=0.76)
    assert o2["eps"]["base"] == pytest.approx(36.0) and "no FY+2 to roll" in o2["_source"]["eps"]["base"]


def test_e23_the_forward_growth_adjustment_is_the_gap_over_peers_bounded():
    fc = {"_fy1_fy2": {"revenue": {"base": (100.0, 125.0)}}}
    f, note = d._forward_growth_adjustment(fc, {"growth_avg": 0.05})
    assert f == pytest.approx(1.3) and "held at" in note             # (1.25/1.05)^3 = 1.69 -> the owner band 1.30
    f2, _ = d._forward_growth_adjustment({"_fy1_fy2": {"revenue": {"base": (100.0, 105.0)}}}, {"growth_avg": 0.05})
    assert f2 == pytest.approx(1.0)
    assert d._forward_growth_adjustment({}, {"growth_avg": 0.05})[0] is None


def test_e28_adjusted_eps_neither_sets_the_margin_nor_fails_the_share_check():
    import inspect
    src = inspect.getsource(gfm)
    assert 'block.get("_eps_adjusted")' in src and "n/a: guided EPS is on an adjusted (non-GAAP) basis" in src


def test_e1_windfall_years_leave_normalised_earnings_for_non_cyclicals():
    # Pfizer: 2021-22 COVID years at 27% / 31% net margin against ~12% otherwise.
    rows = [{"period": f"{y}-12-31", "revenue": r, "net_income": n} for y, r, n in
            ((2021, 81.3e9, 22.0e9), (2022, 100.3e9, 31.4e9), (2023, 58.5e9, 2.1e9), (2024, 63.6e9, 8.0e9), (2025, 62.6e9, 7.8e9))]
    assert d._windfall_periods(rows, "net_income") == ["2021", "2022"]
    full = d._normalized_earnings(rows, "net_income")
    ex = d._normalized_earnings(rows, "net_income", exclude_windfalls=True)
    assert ex < full and ex / 62.6e9 == pytest.approx((2.1 / 58.5 + 8.0 / 63.6 + 7.8 / 62.6) / 3, rel=1e-6)


def test_ddm_low_yield_and_scale_cap_rules_are_wired():
    import inspect
    src = inspect.getsource(d)
    assert d._DDM_MIN_YIELD == 0.02 and "if _yld < _DDM_MIN_YIELD:" in src
    assert "_cons_near_ok = True" in src and "the capped rate governs year 3 onward" in src


def test_e30_preferreds_and_notes_are_not_comps():
    from src.data.regional_comps import is_non_common_security as f
    assert f("Brookfield Finance Inc. 4.625% Subordinated Notes due 2080", "BNH")
    assert f("Strive, Inc. Variable Rate Series A Perpetual Preferred Stock", "SATA")
    assert f("Wells Fargo & Company", "WFC-PL")
    assert not f("Brookfield Corporation", "BN") and not f("Noteworthy AI", "NOTE")


def test_e27_the_risk_on_floor_only_undoes_the_cut_and_the_wacc_tab_shows_it():
    import inspect
    src = inspect.getsource(d)
    assert "_floor = round(min(_capm_wacc - _RISK_ON_CAPM_BAND, wacc - _ov), 4)" in src
    from src.utils import valuation_workbook as vw
    assert "CAPM band: risk-on floor (D5)" in inspect.getsource(vw)


def test_i9_i10_i11_curated_baskets(monkeypatch):
    from src.data import regional_comps as rc
    assert "REGN" in rc.PROFILE_PEER_BASKETS["Commercial Biotech"]["US"]
    assert rc.TICKER_BASKET_BLENDS["BLK"] == (("Asset Manager", 0.75), ("Alt Asset Manager", 0.25))
    assert "SES" in rc.CROSS_MARKET_BASKETS["Grocery & Discount Retail"]["markets"]
    calls = []
    monkeypatch.setattr(rc, "basket_multiples", lambda ex, syms, key, **k: calls.append((ex, key)) or
                        {"pe_ntm": {"value": 10.0 if key == "Asset Manager" else 20.0, "peer_count": 6, "members": []}})
    out = rc.profile_basket_multiples("US", "Asset Manager", exclude="BLK")
    assert out["pe_ntm"]["value"] == pytest.approx(0.75 * 10 + 0.25 * 20)
    rc.profile_basket_multiples("SES", "Grocery & Discount Retail", exclude="OV8.SI")
    assert calls[-1] == ("XMKT", "Grocery & Discount Retail")


def test_market_minority_reads_consolidated_stakes_below_half(monkeypatch):
    # Owner, 2026-10-07 (IHH review): Fortis at 31% is consolidated -- a `consolidated: true` entry prices its
    # outside holders at market; a sub-50% entry without the flag is an associate and prices nothing.
    from src.agents.analysis import holdco_sotp as hs
    from src.data import valuation_constants as vc
    reg = {"listed_subsidiaries": {"entries": {
        "IHHX": [{"listed": "FORTIS.NS", "stake_pct": 0.3117, "consolidated": True, "status": "ACCEPTED"},
                 {"listed": "ASSOC.NS", "stake_pct": 0.30, "status": "ACCEPTED"}],
        "PROP": [{"listed": "C2PU.SI", "stake_pct": 0.3294, "consolidated": True, "status": "PROPOSED"}]}}}
    monkeypatch.setattr(vc, "load", lambda: reg)
    monkeypatch.setattr(hs, "_market_value", lambda listed, end: 1000.0)
    monkeypatch.setattr(hs, "_fx", lambda a, b: 0.05)
    monkeypatch.setattr(hs, "template_for", lambda t: None)
    out = hs.listed_minority_at_market("IHHX", "2026-10-07", "MYR")
    assert [p["listed"] for p in out["parts"]] == ["FORTIS.NS"]
    assert abs(out["value"] - (1 - 0.3117) * 1000.0 * 0.05) < 1e-9
    assert hs.listed_minority_at_market("PROP", "2026-10-07", "MYR") is None   # PROPOSED prices nothing


def test_blk_bridge_entry_and_minority_override_are_wired():
    # Owner, 2026-10-07 (BlackRock review): the bridge entry prices only once ACCEPTED, and its
    # minority_interest replaces the feed's (CIP NCI and Subco units are not outside claims).
    import inspect
    from src.agents.analysis import dcf_agent as d
    from src.data import valuation_constants as vc
    e = vc.load()["bridge_adjustments"]["entries"]["BLK"]
    assert e["minority_interest"] < 1e9 and e["shares_as_converted"] == 164_600_000.0
    if str(e.get("status")).upper() != "ACCEPTED":
        assert d._bridge_adjustment("BLK") == {}
    src = inspect.getsource(d)
    assert '_bridge_adjustment(ticker).get("minority_interest")' in src


def test_rerun_a_failed_share_count_check_prices_the_forecast_eps():
    # Sample rerun 2026-10-07 (REGN): research EPS $35.5 against forecast NI that implies $41.5 on today's
    # shares -- invariant 3 fails, and the forward P/E prices the forecast's EPS, labelled as such.
    est = {"confidence": "HIGH", "estimates": {"base": {"eps_fy1": 32.0, "eps_fy2": 35.5}}}
    fwd = {"eps": {"base": 60.0}, "ebitda": {"base": 1.0}, "revenue": {"base": 1.0}, "ebit": {"base": 1.0}}
    gf = {"rows": [{"eps": 39.0, "ebit": 5.0}, {"eps": 41.5}], "invariants": [{"id": 3, "ok": False}]}
    o = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", gf, None, ntm_e=0.76)
    assert o["eps"]["base"] == pytest.approx(0.24 * 39.0 + 0.76 * 41.5)
    assert "failed the share-count check" in o["_source"]["eps"]["base"]
    gf_ok = {**gf, "invariants": [{"id": 3, "ok": True}]}
    o2 = d._guidance_forward_overlay(est, copy.deepcopy(fwd), "base", gf_ok, None, ntm_e=0.76)
    assert o2["eps"]["base"] == pytest.approx(0.24 * 32.0 + 0.76 * 35.5)


def test_rerun_e29_collaboration_heavy_revenue_stands_the_ev_revenue_leg_down():
    # Sample rerun 2026-10-07 (REGN): a blended leg values the whole company, so the revenue leg stands down
    # above 30% collaboration income instead of pricing the product share alone; the E25 flag leaves when
    # the guidance forecast replaces the schedule.
    import inspect
    src = inspect.getsource(d)
    assert "fwd_rev = fwd_rev * (1.0 - _collab_share)" not in src
    assert '"_ev_rev_collab_stood_down"' in src and src.count("_ev_rev_collab_stood_down") >= 3
    assert 'startswith("Consensus sets DCF years 1-2")' in src


# ── REGN review (2026-10-07): EPS basis, LOE, the effective method ────────────

def test_regn_forward_pe_reverts_to_street_eps_in_every_scenario_when_guidance_diverges():
    def sc(s, cur, cons):
        return {"eps": {s: cur}, "_consensus": {"eps": {s: cons}}, "_source": {"eps": {s: "forecast EPS"}}}
    out, dec, flag = d._eps_street_basis_guard(sc("base", 42.1, 60.4), "base", None, 15)
    assert dec is True and out["eps"]["base"] == 60.4 and "street EPS" in flag
    out_b, dec_b, flag_b = d._eps_street_basis_guard(sc("bear", 29.9, 45.7), "bear", dec, 15)
    assert out_b["eps"]["bear"] == 45.7 and flag_b is None                       # one basis for all scenarios
    _, dec2, _ = d._eps_street_basis_guard(sc("base", 43.7, 44.5), "base", None, 15)   # LLY: in line, kept
    assert dec2 is False
    _, dec3, _ = d._eps_street_basis_guard(sc("base", 42.1, 60.4), "base", None, 3)    # thin coverage, kept
    assert dec3 is False


def test_i14_loe_overlay_erodes_named_drugs_and_haircuts_the_terminal_value():
    entry = {"total_revenue": 100.0, "drugs": [
        {"name": "Dupi", "revenue_fy": 40.0, "loe_year": 2031, "modality": "biologic"},
        {"name": "Late", "revenue_fy": 20.0, "loe_year": 2040, "modality": "small_molecule"}]}
    o = d._franchise_loe_overlay(entry, 2025, [0.05] * 10, 0.08, 0.025)
    idx = o["index"]
    assert idx[4] == pytest.approx(1.0)                                         # 2030: before any LOE
    assert idx[5] == pytest.approx(1.0 - 0.4 * 0.15)                            # 2031: LOE year, biologic 15%
    assert idx[9] == pytest.approx(1.0 - 0.4 * 0.65)                            # 2035: fifth year, 65% (curve end)
    assert (1 + o["growth_schedule"][5]) == pytest.approx(1.05 * idx[5] / idx[4])
    # after the horizon: Dupi is fully eroded by 2035; Late loses 90% from 2040, five years past 2035, discounted
    disc = 1.025 / 1.08
    lost = 0.2 * 0.90 * disc ** 5
    assert o["terminal_multiplier"] == pytest.approx(1.0 - lost / idx[9])
    # a drug already eroding loses only what is left of its curve
    e2 = {"total_revenue": 100.0, "drugs": [{"name": "Eylea", "revenue_fy": 30.0, "loe_year": 2024, "modality": "biologic"}]}
    o2 = d._franchise_loe_overlay(e2, 2025, [0.0] * 10, 0.08, 0.025)
    # years 1-2 (2026-27) are guidance / consensus and already price it; 2028 = year 5 vs year 4 at end-2027
    assert o2["index"][:2] == [1.0, 1.0]
    assert o2["index"][2] == pytest.approx(1.0 - 0.3 * (0.65 - 0.60) / (1 - 0.60))
    assert d._franchise_loe_entry("REGN") == {} or d._franchise_loe_entry("REGN").get("status") == "ACCEPTED"


def test_i14_terminal_multiplier_scales_the_terminal_value_and_the_workbook_reproduces_it():
    a = d._project_dcf(1000.0, 0.2, 0.05, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0)
    b = d._project_dcf(1000.0, 0.2, 0.05, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0, terminal_multiplier=0.8)
    assert b[2] == pytest.approx(0.8 * a[2]) and b[1] == pytest.approx(a[1])
    formulas = pytest.importorskip("formulas")
    tm = d._dcf_timing("2025-09-30", "2026-06-30", "2026-10-04")
    sched = [0.18, 0.19, 0.2, 0.2, 0.21, 0.21, 0.21, 0.21, 0.21, 0.21]
    iv, pv_f, pv_t, rows = d._project_dcf(1000.0, 0.2, 0.08, 0.0, 0.09, 0.03, -0.05, 100.0, 10.0,
                                          margin_schedule=sched, minority_interest=40.0, preferred_equity=5.0,
                                          timing=tm, terminal_multiplier=0.8)
    run = _wbt._run()
    dr = run["data"]["dcf_range"]["TEST"]
    for s in ("bear", "base", "bull"):
        legs = copy.deepcopy(dr[s]["leg_inputs"])
        legs["DCF"].update(value=iv, pv_fcf_per_share=pv_f, pv_tv_per_share=pv_t, projection_rows=rows,
                           growth_schedule=None, growth_base=0.08, timing=tm, minority_interest=40.0,
                           preferred_equity=5.0, terminal_loe_multiplier=0.8)
        dr[s]["leg_inputs"] = legs
        dr[s]["method_iv_table"]["DCF"] = iv
        dr[s]["intrinsic_value"] = round(0.4 * iv + 0.4 * 230.0 + 0.2 * 180.0, 2)
    iv_b = dr["base"]["intrinsic_value"]
    dr["12m_targets"] = {s: round(150.0 + 0.35 * (iv_b - 150.0), 2) for s in ("bear", "base", "bull")}
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "loe.xlsx")
        Path(p).write_bytes(build_workbook(run, "TEST"))
        sol = {k.upper(): v for k, v in formulas.ExcelModel().loads(p).finish().calculate().items()}
        ws = load_workbook(p)["DCF"]
        checks = [c.row for c in ws["A"] if c.value == "Check"]
        assert checks
        for r in checks:
            v = sol[f"'[LOE.XLSX]DCF'!B{r}".upper()]
            v = getattr(v, "value", v)[0][0]
            assert abs(v) < 1e-6, (r, v)


def test_regn_a_quarantined_pipeline_says_the_valuation_is_the_commercial_business_alone():
    import inspect
    assert "not the hybrid SOTP the method selection chose" in inspect.getsource(d)
