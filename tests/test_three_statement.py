"""Three-statement forecast FY+1E..FY+5E on the valuation agent's estimates (owner, 2026-10-03), the
owner's flow (IS -> schedules -> CF -> BS) and the reconciliation suite that gates the output."""
import copy
import importlib.util
import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

_wspec = importlib.util.spec_from_file_location("_wbt", Path(__file__).resolve().parent / "test_valuation_workbook.py")
_wbt = importlib.util.module_from_spec(_wspec)
_wspec.loader.exec_module(_wbt)

from src.agents.analysis import guidance_forecast as gf
from src.agents.analysis import three_statement as ts

_spec = importlib.util.spec_from_file_location("_gft", Path(__file__).resolve().parent / "test_guidance_forecast.py")
_gft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gft)
_spec2 = importlib.util.spec_from_file_location("_eot", Path(__file__).resolve().parent / "test_estimate_overrides.py")
_eot = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(_eot)

RAW = {
    "FY2024": {"revenue": 38e9, "cost_of_revenue": 32e9, "gross_profit": 6e9, "operating_income": 1.9e9, "pretax_income": 1.79e9, "income_tax_expense": 0.49e9, "net_income": 1.3e9,
               "interest_expense": 110e6, "interest_income": 40e6, "depreciation_and_amortization": 190e6, "capital_expenditure": -140e6, "stock_based_compensation": 60e6,
               "dividends_and_distributions": 0.0, "share_buyback": -600e6, "shares_outstanding": 56e6, "cash_and_equivalents": 4.0e9, "short_term_investments": 0.5e9,
               "accounts_receivable": 2.9e9, "inventory": 0.0, "current_assets": 9.0e9, "property_plant_equipment": 1.2e9, "goodwill": 1.6e9, "intangible_assets": 0.3e9,
               "total_assets": 14.0e9, "accounts_payable": 1.0e9, "short_term_debt": 0.4e9, "current_liabilities": 7.0e9, "long_term_debt": 2.0e9, "total_liabilities": 9.6e9,
               "shareholders_equity": 4.3e9, "minority_interest": 0.1e9, "retained_earnings": 3.6e9},
    "FY2025": {"revenue": 42e9, "cost_of_revenue": 35.5e9, "gross_profit": 6.5e9, "operating_income": 1.2e9, "pretax_income": 1.08e9, "income_tax_expense": 0.33e9, "net_income": 0.75e9,
               "interest_expense": 120e6, "interest_income": 45e6, "depreciation_and_amortization": 200e6, "capital_expenditure": -150e6, "stock_based_compensation": 70e6,
               "dividends_and_distributions": 0.0, "share_buyback": -700e6, "shares_outstanding": 52e6, "cash_and_equivalents": 4.2e9, "short_term_investments": 0.5e9,
               "accounts_receivable": 3.1e9, "inventory": 0.0, "current_assets": 9.6e9, "property_plant_equipment": 1.15e9, "goodwill": 1.6e9, "intangible_assets": 0.25e9,
               "total_assets": 14.5e9, "accounts_payable": 1.1e9, "short_term_debt": 0.4e9, "current_liabilities": 7.4e9, "long_term_debt": 2.0e9, "total_liabilities": 10.0e9,
               "shareholders_equity": 4.4e9, "minority_interest": 0.1e9, "retained_earnings": 3.7e9},
}


def _fc():
    return gf.build_forecast(_gft._BLOCK, scenario="base", series=_gft._SERIES, profile_name="Managed Care", sector="Healthcare",
                             wacc=0.08, tgr=0.025, shares=52e6, net_debt=1e9, spot=190.0, peer_ev_ebitda=9.0, market_growth=0.04)


def test_the_opening_sheet_is_the_latest_audited_year_with_residual_other_lines():
    op = ts.opening_from_raw(RAW)
    assert op["fy_label"] == "FY2025" and op["fiscal_year"] == 2025 and op["cash"] == 4.2e9
    assert op["other_current_assets"] == pytest.approx(9.6e9 - 4.2e9 - 0.5e9 - 3.1e9)
    assert op["goodwill_intangibles"] == pytest.approx(1.85e9) and op["other_noncurrent_assets"] == pytest.approx(14.5e9 - 9.6e9 - 1.15e9 - 1.85e9)
    assert op["balance_gap_as_filed"] == pytest.approx(0.0)


def test_assumptions_come_from_history_with_their_source_named():
    a = ts.assumptions_from_history(RAW, spot=190.0)
    assert a["gross_margin"]["source"] == "history" and a["gross_margin"]["value"] == pytest.approx(((6e9 / 38e9) + (6.5e9 / 42e9)) / 2)
    assert a["interest_rate"]["value"] == pytest.approx((110e6 / 2.4e9 + 120e6 / 2.4e9) / 2) and a["payout_ratio"]["value"] == 0.0
    assert a["buyback_annual"]["value"] == pytest.approx(650e6) and a["receivable_days"]["value"] == pytest.approx(3.1e9 / 42e9 * 365)
    assert a["inventory_days"]["value"] == 0.0 and a["buyback_price"]["value"] == 190.0 and a["min_cash"]["value"] == 4.2e9
    cov = ts.coverage(_fc(), ts.opening_from_raw(RAW), a)
    assert all(c["status"] == "from the agent's forecast" for c in cov[:7]) and any(c["assumption"] == "opening balance sheet" for c in cov)


def test_the_statements_follow_the_flow_balance_and_pass_the_suite():
    fc = _fc()
    out = ts.build(fc, ts.opening_from_raw(RAW), ts.assumptions_from_history(RAW, spot=190.0))
    assert out["fy_labels"] == ["FY2026E", "FY2027E", "FY2028E", "FY2029E", "FY2030E"]
    assert out["flow"][0].startswith("Income statement") and out["flow"][-1].startswith("Balance sheet")
    IS, SCH, CF, BS = out["income"], out["schedules"], out["cashflow"], out["balance"]
    assert IS["revenue"][0] == pytest.approx(fc["rows"][0]["revenue"]) and IS["ebit"][1] == pytest.approx(fc["rows"][1]["ebit"])
    # schedules: PP&E rolls with capex and D&A; debt is held; interest is on opening debt
    assert SCH["ppe_close"][0] == pytest.approx(1.15e9 + fc["rows"][0]["capex"] - fc["rows"][0]["da"])
    assert SCH["debt_open"][0] == pytest.approx(2.4e9) and SCH["debt_interest"][0] == pytest.approx(2.4e9 * ts.assumptions_from_history(RAW)["interest_rate"]["value"])
    # cash flow: CFO = NI + D&A + SBC - working capital; FCF = CFO - capex
    assert CF["cfo"][0] == pytest.approx(CF["net_income"][0] + CF["da"][0] + CF["sbc"][0] + CF["change_nwc"][0] + CF["minority"][0])
    assert CF["fcf"][0] == pytest.approx(CF["cfo"][0] + CF["capex"][0])
    # balance sheet: balances every year; cash = opening + net change; retained earnings roll
    assert all(abs(g) < 0.01 for g in BS["balance_check"])
    assert BS["cash"][0] == pytest.approx(4.2e9 + CF["net_change_cash"][0]) and BS["cash"][1] == pytest.approx(BS["cash"][0] + CF["net_change_cash"][1])
    assert BS["retained_earnings"][0] == pytest.approx(3.7e9 + IS["net_income"][0] + CF["dividends"][0])
    assert BS["equity"][0] == pytest.approx(BS["retained_earnings"][0] + BS["other_equity"][0])
    rc = out["reconciliation"]
    assert rc["ok"] and len(rc["assertions"]) == 25 and {r["id"] for r in rc["assertions"]} == {1, 2, 3, 4, 5}
    # buybacks retire shares at the held price; the suite's NWC identity holds against the named and other lines
    assert IS["shares"][0] == pytest.approx(52e6 - (-CF["buybacks"][0]) / 190.0)
    assert len(out["checks"]) == 5


def test_a_filed_balance_gap_fails_assertion_one_and_withholds_the_statements():
    raw = copy.deepcopy(RAW)
    raw["FY2025"]["total_assets"] = 15.0e9                            # the filing does not balance by 0.5bn
    out = ts.build(_fc(), ts.opening_from_raw(raw), ts.assumptions_from_history(raw, spot=190.0))
    assert out["skipped"].startswith("RECONCILIATION FAILED") and out["fy_labels"] is None
    assert out["reconciliation"]["ok"] is False and all(f["id"] == 1 for f in out["reconciliation"]["failures"])
    assert "sign conventions" in out["skipped"]


def test_the_revolver_holds_the_minimum_cash_and_is_booked_as_short_term_debt():
    raw = copy.deepcopy(RAW)
    raw["FY2025"]["cash_and_equivalents"] = 0.3e9                     # thin cash, heavy buyback history
    raw["FY2025"]["current_assets"] = 5.7e9
    raw["FY2025"]["total_assets"] = 10.6e9
    raw["FY2025"]["shareholders_equity"] = 0.5e9
    raw["FY2025"]["retained_earnings"] = -0.2e9
    a = ts.assumptions_from_history(raw, spot=190.0)
    a["payout_ratio"]["value"] = 1.4                                   # pay out more than earned
    out = ts.build(_fc(), ts.opening_from_raw(raw), a)
    assert out["reconciliation"]["ok"]
    draws = [c["revolver_draw"] for c in out["checks"]]
    assert any(d > 0 for d in draws) and all(abs(c - 0.3e9) < 1.0 or c > 0.3e9 for c in out["balance"]["cash"])
    assert any("revolver" in n.lower() for n in out["notes"])


def _payload_with_statements():
    p = _eot._payload()
    dr = p["data"]["dcf_range"]["MOH"]
    ctx = dr["forecast_context"]
    ctx["opening_balance_sheet"] = ts.opening_from_raw(RAW)
    ctx["statement_assumptions"] = ts.assumptions_from_history(RAW, spot=190.0)
    ctx["statements_family_ok"] = True
    fc = _fc()
    th = ts.build(fc, ctx["opening_balance_sheet"], ctx["statement_assumptions"])
    th["coverage"] = ts.coverage(fc, ctx["opening_balance_sheet"], ctx["statement_assumptions"])
    dr["three_statements"] = th
    p["data"]["raw_financials"] = RAW
    return p


def test_the_dcf_agent_payload_helper_skips_banks_and_builds_for_operating_companies():
    from src.agents.analysis import dcf_agent as d
    ctx = {"opening_balance_sheet": ts.opening_from_raw(RAW), "statement_assumptions": ts.assumptions_from_history(RAW), "statements_family_ok": True}
    out = d._three_statements_payload(_fc(), ctx, "Managed Care", "Healthcare")
    assert out["fy_labels"] and out["reconciliation"]["ok"] and out["coverage"]
    bank = d._three_statements_payload(_fc(), {**ctx, "statements_family_ok": False}, "Money Center Bank (SG)", "Financials")
    assert "balance-sheet business" in bank["skipped"] and "earnings-and-capital model" in bank["skipped"]
    assert "no guidance-derived forecast" in d._three_statements_payload(None, ctx, "x", "y")["skipped"]
    assert d._three_statements_payload(_fc(), None, "x", "y") is None
    assert "the Money Center Bank (SG) profile is a balance-sheet business" in bank["skipped"]


def test_the_workbench_rebuilds_the_statements_on_assumption_overrides_and_applies_them_on_read():
    from app.backend.services import estimate_override_service as eo
    p = _payload_with_statements()
    res = eo.recompute(p, "MOH", {"shared": {"gross_margin": 0.20, "payout_ratio": 0.30}, "scenarios": {"base": {"revenue_growth_fy2": 0.15}}})
    th = res["scenarios"]["base"]["three_statements"]
    assert th["fy_labels"] and th["reconciliation"]["ok"]
    assert th["assumptions"]["gross_margin"] == {"value": 0.20, "source": "user override", "needed_for": "gross profit and the COGS line"}
    assert th["income"]["gross_margin"][0] == pytest.approx(0.20) and th["cashflow"]["dividends"][0] == pytest.approx(-0.30 * th["income"]["net_income"][0])
    assert th["income"]["revenue"][1] == pytest.approx(th["income"]["revenue"][0] * 1.15)
    out = eo.apply_to_payload(copy.deepcopy(p), "MOH", {"id": "o", "created_at": "2026-10-03T00:00:00", "note": "margin reset", "overrides": res["overrides"], "result": res})
    assert out["data"]["dcf_range"]["MOH"]["three_statements"]["override"]["note"] == "margin reset"
    assert out["data"]["dcf_range"]["MOH"]["three_statements"]["income"]["gross_margin"][0] == pytest.approx(0.20)
    with pytest.raises(ValueError, match="outside"):
        eo.normalize_overrides({"shared": {"payout_ratio": 2.0}})


def test_the_model_tab_and_the_pdf_print_the_four_blocks_and_the_suite():
    from src.utils.valuation_workbook import build_workbook
    p = _payload_with_statements()
    wb = load_workbook(io.BytesIO(build_workbook(p, "MOH", load_statements=_wbt._statements)))
    assert "Model" in wb.sheetnames
    ws = wb["Model"]
    col_a = [str(c.value) for c in ws["A"] if c.value is not None]
    for head in ("1. Income statement (revenue & EBIT)", "2. Supporting schedules", "3. Cash flow statement (net change in cash & free cash flow)",
                 "4. Balance sheet (assets = liabilities + equity)", "5. Reconciliation suite (every forecast column must read OK)"):
        assert head in col_a
    assert ws["D4"].value == "FY2026E" and ws["H4"].value == "FY2030E" and str(ws["C4"].value) == "FY2025A"
    # the forecast columns are formulas; the opening column is a value; the suite rows are OK/FAIL formulas
    rev_row = next(c.row for c in ws["A"] if c.value == "Revenue")
    assert str(ws.cell(row=rev_row, column=4).value).startswith("=") and isinstance(ws.cell(row=rev_row, column=3).value, (int, float))   # openpyxl reads 42000.0 back as int
    chk = next(c.row for c in ws["A"] if str(c.value).startswith("1. ABS(total assets"))
    assert str(ws.cell(row=chk, column=4).value).startswith('=IF(ABS(') and "OK" in str(ws.cell(row=chk, column=4).value)
    suite = next(c.row for c in ws["A"] if c.value == "Suite result")
    assert "COUNTIF" in str(ws.cell(row=suite, column=8).value)
    assert any(v.startswith("Engine suite (Python build): ALL OK") for v in col_a)
    # Owner, 2026-10-03 (Anta): FY+1E..FY+5E on the statements themselves, linked to the Model tab, which sits right after CFS
    assert wb.sheetnames.index("Model") == wb.sheetnames.index("CFS") + 1
    is_ws, bs_ws, cf_ws = wb["IS"], wb["BS"], wb["CFS"]
    for ws_, label in ((is_ws, "Revenue"), (is_ws, "Net income"), (bs_ws, "Cash & equivalents"), (bs_ws, "Total assets"), (bs_ws, "Shareholders' equity"), (cf_ws, "Cash from operations"), (cf_ws, "Free cash flow (CFO + capex)")):
        rr = [c.row for c in ws_["A"] if c.value == label][-1]
        first = ws_.cell(row=rr, column=8).value                                              # FY2026E is the first forecast column after five actuals
        assert str(first).startswith("='Model'!D"), (ws_.title, label, first)
        assert str(ws_.cell(row=rr, column=12).value).startswith("='Model'!H")
    assert str(bs_ws.cell(row=4, column=8).value).startswith("=EDATE(") and str(cf_ws.cell(row=4, column=8).value).startswith("=EDATE(")
    rev_model = next(c.row for c in ws["A"] if c.value == "Revenue")
    rr = [c.row for c in is_ws["A"] if c.value == "Revenue"][-1]
    assert is_ws.cell(row=rr, column=8).value == f"='Model'!D{rev_model}"
    assert not any("{" in str(c.value) for row in ws.iter_rows(min_col=4, max_col=8) for c in row if isinstance(c.value, str))   # every forward reference resolved
    from src.utils import pdf_report as pr
    from reportlab.lib.styles import getSampleStyleSheet
    flow = pr._three_statement_block_pdf(p["data"]["dcf_range"]["MOH"], getSampleStyleSheet(), 500.0)
    texts = " ".join(getattr(f, "text", "") for f in flow)
    assert "Three-statement forecast" in texts and "Flow: Income statement" in texts
    tables = [f for f in flow if f.__class__.__name__ == "Table"]
    assert len(tables) == 5                                                                      # IS, schedules, CF, BS, the suite
    # a failed suite prints the trace, not the statements, on both surfaces
    bad = copy.deepcopy(p)
    bad["data"]["dcf_range"]["MOH"]["three_statements"] = {"skipped": "RECONCILIATION FAILED: FY2026E #1 ...", "reconciliation": {"ok": False, "failures": [
        {"id": 1, "name": "Total assets = total liabilities + equity", "year": "FY2026E", "lhs": 1e9, "rhs": 0.9e9, "diff": 1e8, "ok": False}]}, "fy_labels": None}
    wb2 = load_workbook(io.BytesIO(build_workbook(bad, "MOH", load_statements=_wbt._statements)))
    a2 = [str(c.value) for c in wb2["Model"]["A"] if c.value is not None]
    assert not any(v.startswith("1. Income statement") for v in a2) and any(v == "FY2026E" for v in a2)
    # a withheld model leaves the statements' forecast columns with the reason, not blanks
    is2 = wb2["IS"]
    assert any(str(c.value).startswith("Forecast columns: RECONCILIATION FAILED") for row in is2.iter_rows(min_col=8, max_col=8) for c in row)
    assert wb2["BS"].cell(row=4, column=8).value is None                                       # no forecast columns without a model
    flow2 = pr._three_statement_block_pdf(bad["data"]["dcf_range"]["MOH"], getSampleStyleSheet(), 500.0)
    assert "RECONCILIATION FAILED" in " ".join(getattr(f, "text", "") for f in flow2) and not [f for f in flow2 if f.__class__.__name__ == "Table"]
