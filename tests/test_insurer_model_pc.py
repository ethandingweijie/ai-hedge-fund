"""Owner, 2026-11-24 (item 3, P&C first): a P&C insurer is modelled on premiums, the combined ratio and float x yield
-- not the bank model's loans and NIM -- with the combined ratio implied by the latest audited year when neither
guidance nor the extraction gives one; and the page prints the earnings-and-capital model's forward years on the
reported rows it shares with them (banks too)."""
import copy

import pytest

from src.agents.analysis import bank_model as bmod
from src.agents.analysis import dcf_agent as d
from src.tools.financial_statements import attach_forecast, build_financial_statements, forecast_labels

# PGR-shaped: revenue 87.6bn, pre-tax 14.2bn, assets 123bn
MR = {"total_equity": 30.3e9, "total_assets": 123.0e9, "net_income": 11.3e9, "shares_outstanding": 0.586e9, "goodwill": 0.0,
      "intangible_assets": 2.0e9, "pretax_income": 14.2e9, "income_tax_expense": 2.9e9, "report_period": "2025-12-31"}


def _opening():
    return bmod.opening_from_line_items(MR, MR["shares_outstanding"], 87.6e9, "FY2025")


def test_implied_combined_ratio_is_one_minus_underwriting_over_premiums():
    inv = 123.0e9 * 0.75 * 0.035
    prem = 87.6e9 - inv
    assert bmod.implied_combined_ratio(_opening(), 0.035, 0.75) == pytest.approx(1 - (14.2e9 - inv) / prem)
    assert bmod.implied_combined_ratio({**_opening(), "pretax": None}, 0.035, 0.75) is None
    assert bmod.implied_combined_ratio({**_opening(), "total_income": 1.0e9}, 0.035, 0.75) is None    # premiums ≤ 0


def test_with_nothing_given_the_audited_year_sets_the_combined_ratio_not_the_96pct_default():
    a = bmod.insurer_assumptions(_opening(), {}, {}, coe=0.09)
    assert a["combined_ratio_fy1"]["source"].startswith("line items: implied")
    assert a["combined_ratio_fy1"]["value"] == pytest.approx(0.8698, abs=1e-3)
    assert a["combined_ratio_fy2"]["value"] == a["combined_ratio_fy1"]["value"] and a["combined_ratio_fy2"]["source"].endswith("(held)")
    m = bmod.build_insurer(_opening(), a, scenario="base")
    assert m["reconciliation"]["ok"]
    # FY+1 earnings now track the audited year (the 96% default gave 5.4bn against 11.3bn actual)
    assert m["rows"]["net_income"][0] == pytest.approx(11.3e9 * 1.04, rel=0.06)


def test_guidance_and_the_extraction_still_outrank_the_implied_ratio():
    ext = bmod.insurer_assumptions(_opening(), {"combined_ratio": 0.93}, {}, coe=0.09)
    assert ext["combined_ratio_fy1"] == {"value": 0.93, "source": "insurance metrics: combined ratio", "needed_for": ext["combined_ratio_fy1"]["needed_for"]}
    gd = bmod.insurer_assumptions(_opening(), {"combined_ratio": 0.93}, {"family_metrics": {"insurer": {"combined_ratio_fy1": 0.91}}}, coe=0.09)
    assert gd["combined_ratio_fy1"]["value"] == 0.91 and gd["combined_ratio_fy1"]["source"].startswith("guidance")


def test_pc_takes_the_insurer_model_and_life_does_not():
    assert "Insurance (P&C)" in d._INSURER_MODEL_PROFILES
    assert "Insurance (P&C)" in d._BANK_PROFILE_CALIBRATION          # it reaches the capital-model block through the bank table
    assert "Insurance" not in d._INSURER_MODEL_PROFILES               # life: the P&C model overstates earnings ~5x (MET, PRU)


ROWS = {"FY2025": {"revenue": 87.6e9, "operating_expense": 70.0e9, "operating_income": 14.2e9, "net_income": 11.3e9,
                   "earnings_per_share": 19.3, "cash_and_equivalents": 1.0e9, "total_assets": 123.0e9, "total_liabilities": 92.7e9,
                   "shareholders_equity": 30.3e9, "book_value_per_share": 51.7, "operating_cash_flow": 15.0e9}}


def _fs():
    return build_financial_statements(copy.deepcopy(ROWS), sector="Financials", profile="Insurance (P&C)", currency="USD")


def test_the_page_prints_the_insurer_model_on_the_rows_it_shares():
    m = bmod.build_insurer(_opening(), bmod.insurer_assumptions(_opening(), {}, {}, coe=0.09), scenario="base")
    out = attach_forecast(_fs(), m)
    labels = m["fy_labels"]
    assert out["forecast_periods"] == labels and out["forecast"]["status"] == "ok"
    assert "insurer earnings-and-capital model" in out["forecast"]["source"]
    row = lambda st, k: next(r for r in out["statements"][st]["rows"] if r["key"] == k)       # noqa: E731
    r = m["rows"]
    # An insurer renders the standard layout since 2026-11-27 (only the Banks family gets the bank one): the
    # model's premiums + investment income print on the revenue line.
    assert out["layout"] == "standard"
    assert row("income", "revenue")["values"][labels[0]] == pytest.approx(r["net_earned_premiums"][0] + r["investment_income"][0])
    assert row("income", "net_income")["values"][labels[0]] == pytest.approx(r["net_income"][0])
    assert row("income", "earnings_per_share")["values"][labels[-1]] == pytest.approx(r["eps"][-1])
    assert row("balance", "shareholders_equity")["values"][labels[0]] == pytest.approx(r["equity"][0])
    assert row("balance", "total_liabilities")["values"][labels[0]] == pytest.approx(r["total_assets"][0] - r["equity"][0])
    assert labels[0] not in row("balance", "cash_and_equivalents")["values"]                   # a line the model does not build stays blank
    assert out["forecast"]["reconciliation"]["ok"] is True


def test_a_bank_model_prints_too_and_an_empty_or_withheld_one_does_not():
    op = bmod.opening_from_line_items({"total_equity": 68.9e9, "total_assets": 897.5e9, "net_income": 11.0e9, "shares_outstanding": 2.85e9,
                                       "goodwill": 6.3e9, "interest_income": 40e9, "interest_expense": 25e9, "pretax_income": 12.6e9,
                                       "income_tax_expense": 1.6e9, "report_period": "2025-12-31"}, 2.85e9, 22.3e9, "FY2025")
    m = bmod.build_bank(op, bmod.bank_assumptions(op, {}, {}, coe=0.09, target_roe=0.15, cet1_target=0.13), scenario="base")
    assert forecast_labels(m) == m["fy_labels"]
    bank_fs = build_financial_statements(
        {"FY2025": {"revenue": 50.0e9, "interest_income": 40e9, "interest_expense": 25e9, "operating_expense": 10.0e9,
                    "net_income": 11.0e9, "total_assets": 897.5e9, "shareholders_equity": 68.9e9}},
        sector="Financials", profile="Money Center Bank (SG)", currency="SGD")
    out = attach_forecast(bank_fs, m)
    pre = next(r for r in out["statements"]["income"]["rows"] if r["key"] == "pre_provision_profit")
    assert pre["values"][m["fy_labels"][0]] == pytest.approx(m["rows"]["pre_provision_profit"][0])
    assert forecast_labels({"kind": "bank", "fy_labels": m["fy_labels"], "rows": {}}) == []
    assert forecast_labels({**m, "skipped": "RECONCILIATION FAILED"}) == []


def test_the_workbook_links_the_insurer_lines_and_describes_an_insurer():
    import importlib.util, io
    from pathlib import Path
    from openpyxl import load_workbook
    from src.utils.valuation_workbook import build_workbook
    spec = importlib.util.spec_from_file_location("_bmt", Path(__file__).resolve().parent / "test_bank_model.py")
    bmt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bmt)
    p = bmt._bank_payload()
    op = _opening()
    a = bmod.insurer_assumptions(op, {}, {}, coe=0.09)
    m = bmod.build_insurer(op, a, scenario="base"); m["coverage"] = bmod.coverage(m)
    dr_ = p["data"]["dcf_range"]["D05.SI"]
    dr_["three_statements"] = m
    dr_["forecast_context"]["bank_model"] = {"kind": "insurer", "opening": op, "assumptions": a}
    dr_["profile"] = "Insurance (P&C)"
    wb = load_workbook(io.BytesIO(build_workbook(p, "D05.SI", load_statements=bmt._wbt._statements)))
    ws = wb["Model"]
    col_a = [str(c.value) for c in ws["A"] if c.value is not None]
    assert "Combined ratio" in col_a and "Total revenue (premiums + investment income)" in col_a and "CET1 ratio" not in col_a
    assert any("underwriting result" in str(c.value) for row in ws.iter_rows(max_row=3) for c in row if c.value)
    is_ws = wb["IS"]
    f0 = next(c for c in range(3, 20) if str(is_ws.cell(row=4, column=c).value).startswith("=EDATE("))
    for lab in ("Net earned premiums", "Underwriting result", "Investment income", "Revenue"):
        rr = [c.row for c in is_ws["A"] if c.value == lab]
        row_ = rr[0] if lab == "Revenue" else rr[-1]                 # the reported Revenue row (a later block repeats the label)
        assert rr and str(is_ws.cell(row=row_, column=f0).value).startswith("='Model'!"), lab
