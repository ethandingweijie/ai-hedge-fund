"""Owner, 2026-10-04: the Financials tab (web and mobile) prints FY+1E..FY+5E on the reported
rows of the income statement, balance sheet and cash flow. One mapping, shared with the PDF."""
import copy

import pytest

from src.tools import financial_statements as fs_mod
from src.tools.financial_statements import attach_forecast, build_financial_statements, forecast_labels

ROWS = {
    "FY2024": {"revenue": 900.0, "cost_of_revenue": 540.0, "gross_profit": 360.0, "operating_expense": 200.0,
               "operating_income": 160.0, "net_income": 110.0, "earnings_per_share": 1.10,
               "cash_and_equivalents": 80.0, "total_assets": 1000.0, "total_liabilities": 600.0,
               "shareholders_equity": 400.0, "book_value_per_share": 4.0,
               "operating_cash_flow": 150.0, "capital_expenditure": -40.0, "free_cash_flow": 110.0},
    "FY2025": {"revenue": 1000.0, "cost_of_revenue": 600.0, "gross_profit": 400.0, "operating_expense": 220.0,
               "operating_income": 180.0, "net_income": 125.0, "earnings_per_share": 1.25,
               "cash_and_equivalents": 100.0, "total_assets": 1100.0, "total_liabilities": 640.0,
               "shareholders_equity": 460.0, "book_value_per_share": 4.6,
               "operating_cash_flow": 170.0, "capital_expenditure": -45.0, "free_cash_flow": 125.0},
}
LABELS = ["FY2026E", "FY2027E", "FY2028E", "FY2029E", "FY2030E"]


def _th(**extra):
    n = 5
    rev = [1100.0, 1200.0, 1300.0, 1400.0, 1500.0]
    th = {
        "fy_labels": list(LABELS),
        "opening": {"fy_label": "FY2025", "goodwill": 50.0},
        "income": {"revenue": rev, "cogs": [-0.6 * r for r in rev], "gross_profit": [0.4 * r for r in rev],
                   "opex_ex_da": [-150.0] * n, "da": [-60.0] * n, "ebit": [0.2 * r for r in rev],
                   "interest_expense": [-10.0] * n, "interest_income": [2.0] * n, "pretax": [0.19 * r for r in rev],
                   "tax": [-0.04 * r for r in rev], "net_income": [0.15 * r for r in rev],
                   "eps": [1.5, 1.7, 1.9, 2.1, 2.3], "ebitda": [0.25 * r for r in rev], "shares": [100.0] * n},
        "balance": {"cash": [120.0, 150.0, 190.0, 240.0, 300.0], "current_assets": [400.0] * n,
                    "total_assets": [1200.0, 1300.0, 1400.0, 1500.0, 1600.0], "current_liabilities": [200.0] * n,
                    "total_liabilities": [660.0, 680.0, 700.0, 720.0, 740.0], "equity": [540.0, 620.0, 700.0, 780.0, 860.0],
                    "short_term_debt": [20.0] * n, "long_term_debt": [180.0] * n, "net_debt": [80.0, 50.0, 10.0, -40.0, -100.0]},
        "cashflow": {"net_income": [0.15 * r for r in rev], "da": [60.0] * n, "cfo": [200.0, 215.0, 230.0, 245.0, 260.0],
                     "capex": [-70.0] * n, "fcf": [130.0, 145.0, 160.0, 175.0, 190.0],
                     "net_change_cash": [20.0, 30.0, 40.0, 50.0, 60.0], "closing_cash": [120.0, 150.0, 190.0, 240.0, 300.0]},
        "reconciliation": {"ok": True, "assertions": [
            {"id": i, "name": f"assertion {i}", "ok": True, "year": y} for y in LABELS for i in (1, 2, 3, 4, 5)]},
    }
    th.update(extra)
    return th


@pytest.fixture
def fs():
    return build_financial_statements(copy.deepcopy(ROWS), sector="Tech", profile="Mature SaaS", currency="USD")


def _row(payload, stmt, key):
    return next(r for r in payload["statements"][stmt]["rows"] if r["key"] == key)


def test_forecast_years_land_on_the_reported_rows_of_all_three_statements(fs):
    out = attach_forecast(fs, _th())
    assert out["periods"] == ["FY2024", "FY2025"]                       # the reported years are untouched
    assert out["forecast_periods"] == LABELS
    rev = _row(out, "income", "revenue")
    assert [rev["values"][p] for p in LABELS] == [1100.0, 1200.0, 1300.0, 1400.0, 1500.0]
    assert rev["values"]["FY2025"] == 1000.0
    # reported sign conventions: cost is positive on the income statement, capex negative on the cash flow
    assert _row(out, "income", "cost_of_revenue")["values"]["FY2026E"] == pytest.approx(660.0)
    assert _row(out, "cashflow", "capital_expenditure")["values"]["FY2026E"] == -70.0
    assert _row(out, "balance", "total_assets")["values"]["FY2030E"] == 1600.0
    assert _row(out, "balance", "shareholders_equity")["values"]["FY2027E"] == 620.0
    assert _row(out, "cashflow", "free_cash_flow")["values"]["FY2028E"] == 160.0
    assert _row(out, "income", "earnings_per_share")["values"]["FY2026E"] == 1.5
    assert out["forecast"]["status"] == "ok" and out["forecast"]["rows_filled"] >= 12


def test_growth_runs_through_the_join_and_per_share_ratios_carry_none(fs):
    out = attach_forecast(fs, _th())
    rev = _row(out, "income", "revenue")
    assert rev["growth"]["FY2026E"] == pytest.approx(0.10)              # FY2026E against reported FY2025
    assert rev["growth"]["FY2027E"] == pytest.approx(1200.0 / 1100.0 - 1)
    assert "FY2026E" not in _row(out, "balance", "book_value_per_share")["growth"]
    # net debt crosses zero in FY2029E: a sign flip has no percentage
    assert _row(out, "balance", "total_assets")["growth"]["FY2026E"] == pytest.approx(1200.0 / 1100.0 - 1)


def test_the_suite_summary_travels_with_the_columns(fs):
    out = attach_forecast(fs, _th(), override={"created_at": "2026-10-03T10:00:00", "fields": ["revenue_growth_fy1"]})
    rc = out["forecast"]["reconciliation"]
    assert rc["ok"] is True and [a["id"] for a in rc["assertions"]] == [1, 2, 3, 4, 5]
    assert all(a["ok"] for a in rc["assertions"])
    assert out["forecast"]["override"] == {"created_at": "2026-10-03", "fields": ["revenue_growth_fy1"]}
    th = _th()
    th["reconciliation"]["assertions"][7]["ok"] = False                  # one year of assertion 3 fails
    bad = attach_forecast(fs, th)["forecast"]["reconciliation"]["assertions"]
    assert [a["ok"] for a in bad] == [True, True, False, True, True]


def test_idempotent_and_the_input_is_not_mutated(fs):
    before = copy.deepcopy(fs)
    once = attach_forecast(fs, _th())
    assert fs == before
    th2 = _th()
    th2["income"]["revenue"] = [1050.0, 1100.0, 1150.0, 1200.0, 1250.0]   # a user override recomputed the model
    twice = attach_forecast(once, th2)
    assert twice["forecast_periods"] == LABELS
    assert _row(twice, "income", "revenue")["values"]["FY2026E"] == 1050.0
    # a shorter forecast replaces, never stacks
    th3 = _th(fy_labels=LABELS[:3])
    thrice = attach_forecast(twice, th3)
    assert thrice["forecast_periods"] == LABELS[:3]
    assert "FY2030E" not in _row(thrice, "income", "revenue")["values"]


def test_withheld_bank_and_absent_models_say_why_and_add_no_columns(fs):
    w = attach_forecast(fs, {"skipped": "RECONCILIATION FAILED: cash roll FY2027E", "fy_labels": None})
    assert w["forecast_periods"] == [] and w["forecast"]["status"] == "withheld"
    assert "cash roll" in w["forecast"]["reason"]
    b = attach_forecast(fs, {"kind": "bank", "fy_labels": LABELS, "rows": {}})
    assert b["forecast_periods"] == [] and b["forecast"]["status"] == "not_applicable"
    n = attach_forecast(fs, None)
    assert n["forecast_periods"] == [] and n["forecast"] == {"status": "none"}
    assert "FY2026E" not in _row(n, "income", "revenue")["values"]
    assert attach_forecast(None, _th()) is None and attach_forecast({}, _th()) == {}
    assert forecast_labels(_th()) == LABELS and forecast_labels({"skipped": "x", "fy_labels": LABELS}) == []


def test_the_pdf_uses_the_same_mapping():
    from src.utils import pdf_report
    th = _th()
    assert pdf_report._fs_forecast_values(th) == fs_mod.forecast_values(th)


def test_the_api_attaches_after_overrides_and_at_save(fs):
    import inspect
    from app.backend.services import analysis_service as svc
    payload = {"ticker": "ZZCO", "data": {"tickers": ["ZZCO"], "financial_statements": fs,
                                          "dcf_range": {"ZZCO": {"base": {"intrinsic_value": 1.0}, "three_statements": _th(),
                                                                 "estimate_override": {"created_at": "2026-10-03", "fields": ["wacc"]}}}}}
    svc._attach_statement_forecast(payload)
    got = payload["data"]["financial_statements"]
    assert got["forecast_periods"] == LABELS and got["forecast"]["override"]["fields"] == ["wacc"]
    svc._attach_statement_forecast(payload)                                # idempotent on read
    assert payload["data"]["financial_statements"]["forecast_periods"] == LABELS
    # a payload without statements, or without a dcf entry, is left alone and never raises
    svc._attach_statement_forecast({"data": {"dcf_range": {}}})
    lone = {"data": {"financial_statements": fs, "dcf_range": {}}}
    svc._attach_statement_forecast(lone)
    assert lone["data"]["financial_statements"]["forecast"] == {"status": "none"}
    # wiring: on read it runs AFTER the override is applied; at save it runs before the web run is stored
    src = inspect.getsource(svc.get_run_result)
    assert src.index("_eo.apply_saved(run_id, _payload)") < src.index("_attach_statement_forecast(_payload)")
    full = inspect.getsource(svc)
    assert full.index("_attach_statement_forecast(result)") < full.index("await asyncio.to_thread(_save_web_run, run_id, t, model_name, result,")
