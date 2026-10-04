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
    plan, _ = d._valuation_net_debt(dict(r), "Healthcare", "MOH", "USD", "Medical - Healthcare Plans")
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
    nd, b = d._valuation_net_debt(dict(r), "HealthcareServices", "MOH", "USD", "Medical - Healthcare Plans")
    assert b["regulated_cash_excluded"] is True and nd == 4000.0 - 184.0
