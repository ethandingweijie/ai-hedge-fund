"""Data-integrity audit, 2026-11-27: FX outage handling and three statement defects.

1. FX: an unresolvable pair returns None, never a silent 1.0 (Singtel's SOTP went 4.46 -> 22.85
   when the FMP batch was empty and THB/IDR stakes were added as SGD). THB, PHP, MYR, IDR, VND and
   ZAR have static fallbacks, marked as fallback; a leg whose FX cannot be resolved is excluded.
2a. Z74.SI FY2025: FMP swapped COGS and opex (gross profit == revenue). The year is flagged not
    comparable, its cost lines withheld, no growth across it, and operating income is the reported
    line on the web statements, the workbook and /analysis/financials alike.
2b. S68.SI: "FY2022" and "FY2022 (Ending 2022-06-30)" are one fiscal year.
2c. U11.SI (bank): EPS/BVPS zero-fills are derived, the key-metrics "revenue" is total income,
    and total equity / ROE read the shareholders' equity the run carries.

Fixtures are the stored runs' own figures. No network.
"""
from __future__ import annotations

import io
import time

import pytest

from src.tools import api
from src.tools.financial_statements import (
    build_financial_statements,
    noncomparable_flags,
    normalise_period_keys,
    repair_statements,
)


@pytest.fixture
def fmp_down(monkeypatch):
    """The FMP forex batch is empty (outage / throttle): only the static table answers."""
    monkeypatch.setattr(api, "_get_fx_batch", lambda key=None: {})
    yield


# ── 1. FX ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ccy,usd", [("THB", 0.0293858), ("PHP", 0.0157023), ("MYR", 0.24307),
                                     ("IDR", 0.00005424), ("VND", 0.000038675), ("ZAR", 0.0573326)])
def test_new_fallback_currencies_resolve_and_say_fallback(fmp_down, ccy, usd):
    d = api.get_fx_rate_detail(ccy, "SGD")
    assert d["source"] == "fallback" and d["fallback"] is True
    assert d["rate"] == pytest.approx(usd / 0.774875)
    assert d["rate"] < 0.5                      # never the old silent 1.0


def test_live_thb_and_idr_against_sgd_are_in_the_right_place(fmp_down):
    # live 2026-11-27: THB->SGD 0.0376, IDR->SGD 0.00007
    assert api.get_fx_rate("THB", "SGD") == pytest.approx(0.0376, rel=0.05)
    assert api.get_fx_rate("IDR", "SGD") == pytest.approx(0.00007, rel=0.05)


def test_unknown_pair_is_none_and_equal_currencies_are_identity(fmp_down):
    assert api.get_fx_rate("XYZ", "SGD") is None
    assert api.get_fx_rate_detail("XYZ", "SGD")["source"] == "unavailable"
    assert api.get_fx_rate("SGD", "SGD") == 1.0
    assert api.get_fx_rate_detail("SGD", "SGD")["source"] == "identity"


def test_live_rate_is_not_marked_fallback(monkeypatch):
    monkeypatch.setattr(api, "_FX_BATCH_CACHE", {"USDTHB": {"price": 34.0}, "USDSGD": {"price": 1.29}})
    monkeypatch.setattr(api, "_FX_BATCH_TS", time.time())
    d = api.get_fx_rate_detail("THB", "SGD")
    assert d["source"] == "live" and not d["fallback"]
    assert d["rate"] == pytest.approx(1.29 / 34.0)


def test_holdco_fx_refuses_an_unknown_pair_and_records_fallbacks(fmp_down):
    from src.agents.analysis import holdco_sotp as h
    h._FX_FALLBACK_SEEN.clear()
    assert h._fx("XYZ", "SGD") is None
    assert h._fx("THB", "SGD") == pytest.approx(0.0293858 / 0.774875)
    assert "THB->SGD" in h._FX_FALLBACK_SEEN


def test_lookthrough_excludes_a_division_it_cannot_convert(fmp_down, monkeypatch):
    from src.agents.analysis import holdco_sotp as h
    tpl = {"currency": "SGD", "holdco_discount": [0.0, 0.0], "divisions": [
        {"name": "Home", "basis": "fixed_value", "currency": "SGD", "amount": 100.0, "source": "t"},
        {"name": "Exotic stake", "basis": "fixed_value", "currency": "XYZ", "amount": 5e6, "source": "t"},
    ]}
    monkeypatch.setattr(h, "template_for", lambda t: tpl)
    res = h.look_through_value("TEST.SI", "2026-11-27")
    assert res is not None and res["complete"] is False
    why = {s["division"]: s["reason"] for s in res["skipped"]}
    assert "exchange rate" in why["Exotic stake"] and "excluded" in why["Exotic stake"]
    # the unconvertible part is not in the total at 1.0
    assert all(p["division"] != "Exotic stake" for p in res["parts"])
    # value_per_share declines an incomplete look-through rather than publishing a partial NAV
    assert h.value_per_share("TEST.SI", "2026-11-27", 10.0, to_currency="SGD") is None


def test_sotp_skeleton_drops_reported_currency_facts_without_a_rate(fmp_down, monkeypatch):
    from types import SimpleNamespace
    from src.agents.analysis import sotp_extractor as se
    row = SimpleNamespace(currency="XYZ", cash_and_equivalents=5e9, short_term_investments=0.0,
                          total_debt=1e9, equity_method_investments=2e9, long_term_investments=None,
                          shares_outstanding=1e6, revenue=9e9)
    monkeypatch.setattr(se, "search_line_items", lambda *a, **k: [row])
    monkeypatch.setattr(se, "get_fx_rate", lambda *a, **k: None)
    sk = se._deterministic_skeleton("TEST", "2026-11-27", None)
    assert sk["net_cash"] is None and sk["associates"] is None and sk["revenue"] is None
    assert sk["shares"] == 1e6 and "XYZ->USD" in sk["fx_unavailable"]


def test_portfolio_line_without_a_rate_leaves_the_usd_totals(monkeypatch):
    from app.backend.services import portfolio_service as ps
    monkeypatch.setattr(api, "get_listing_currency", lambda t, *a, **k: "XYZ" if t == "ODD.XX" else "USD")
    monkeypatch.setattr(api, "get_fx_rate", lambda f, t="USD", *a, **k: None)
    info = ps.currency_info(["ODD.XX", "AAPL"])
    assert info["ODD.XX"]["fx_to_usd"] is None and info["AAPL"]["fx_to_usd"] == 1.0
    holdings = [{"id": 1, "ticker": "AAPL", "quantity": 10, "avg_cost": 100.0},
                {"id": 2, "ticker": "ODD.XX", "quantity": 1000, "avg_cost": 50.0}]
    d = ps.build_dashboard(holdings, {"AAPL": 110.0, "ODD.XX": 60.0}, {}, info)
    odd = d["holdings"][1]
    assert odd["fx_unavailable"] is True and odd["market_value_usd"] is None
    assert d["summary"]["total_market_value"] == pytest.approx(1100.0)     # not 1100 + 60,000
    from src.portfolio.replay import usd_rate
    assert usd_rate({"currency": "XYZ", "fx_to_usd": None}) == 0.0
    assert usd_rate({"fx_to_usd": None}) == 1.0                              # no currency = USD by contract


def test_disclosures_state_a_dcf_declined_for_fx():
    from src.utils.report_disclosures import data_limitations
    lim = data_limitations({"dcf_skip_reasons": {"Z74.SI": "fx_unavailable: no IDR->SGD rate"}}, "Z74.SI")
    assert any("exchange rate" in x for x in lim)


# ── 2a. Singtel FY2025: swapped COGS / opex ─────────────────────────────────

# The stored run's FMP rows (S$; Z74.SI, web_runs 27190196, 2026-11-27).
Z74 = {
    "FY2022": {"revenue": 15339.1e6, "cost_of_revenue": 12510.4e6, "gross_profit": 2828.7e6,
               "operating_expense": 1941.1e6, "operating_income": 887.6e6, "net_income": 1948.5e6},
    "FY2023": {"revenue": 14624.4e6, "cost_of_revenue": 11789.9e6, "gross_profit": 2834.5e6,
               "operating_expense": 1917.8e6, "operating_income": 916.7e6, "net_income": 2225.1e6},
    "FY2024": {"revenue": 14127.5e6, "cost_of_revenue": 10735.8e6, "gross_profit": 3391.7e6,
               "operating_expense": 3489.1e6, "operating_income": 933.6e6, "net_income": 795.0e6},
    "FY2025": {"revenue": 14146.1e6, "cost_of_revenue": 4188.1e6, "gross_profit": 14146.1e6,
               "operating_expense": 11779.9e6, "operating_income": 2366.2e6, "net_income": 4017.4e6,
               "pretax_income": 4593.3e6, "other_income_expense": 2227.1e6},
    "FY2026": {"revenue": 14260.6e6, "cost_of_revenue": 11391.6e6, "gross_profit": 2869.0e6,
               "operating_expense": 1744.4e6, "operating_income": 1124.6e6, "net_income": 5606.1e6},
}


def _row(fs, key):
    return next(r for st in fs["statements"].values() for r in st["rows"] if r["key"] == key)


def test_swapped_year_is_flagged_and_its_neighbour_is_not():
    flags = noncomparable_flags([Z74[k] for k in sorted(Z74)])
    assert [bool(f) for f in flags] == [False, False, False, True, False]


def test_swapped_year_withholds_cost_lines_and_keeps_reported_operating_income():
    fs = build_financial_statements(Z74, sector="Communication Services", currency="SGD")
    assert list(fs["not_comparable"]) == ["FY2025"]
    gp, cogs, oi, rev = (_row(fs, k) for k in ("gross_profit", "cost_of_revenue", "operating_income", "revenue"))
    assert gp["values"]["FY2025"] is None and cogs["values"]["FY2025"] is None
    assert oi["values"]["FY2025"] == pytest.approx(2366.2e6)              # reported, not rebuilt (-1,821.9m)
    # no +317% / -80%: growth is not computed into or out of the year
    assert gp["growth"]["FY2025"] is None and gp["growth"]["FY2026"] is None
    assert oi["growth"]["FY2025"] is None and oi["growth"]["FY2026"] is None
    assert gp["growth"]["FY2024"] == pytest.approx(3391.7 / 2834.5 - 1)
    assert rev["growth"]["FY2025"] == pytest.approx(14146.1 / 14127.5 - 1)   # revenue is unaffected


def test_comparability_test_has_no_false_positives():
    # a software company with no COGS line (gross margin 100% every year) and an ordinary industrial
    sw = {f"FY{y}": {"revenue": r, "gross_profit": r} for y, r in ((2022, 100.0), (2023, 120.0), (2024, 140.0))}
    ind = {f"FY{y}": {"revenue": r, "cost_of_revenue": c, "gross_profit": r - c}
           for y, r, c in ((2022, 100.0, 60.0), (2023, 110.0, 68.0), (2024, 105.0, 61.0), (2025, 90.0, 47.0))}
    assert not build_financial_statements(sw)["not_comparable"]
    assert not build_financial_statements(ind)["not_comparable"]
    # a bank layout is never tested (no COGS split)
    assert not build_financial_statements(Z74, sector="Banking")["not_comparable"]


def test_stored_statements_are_repaired_on_read():
    stored = build_financial_statements({k: dict(v) for k, v in Z74.items()})
    # emulate a pre-fix archive: the swapped figures and the absent flag
    for k in ("gross_profit", "cost_of_revenue", "operating_expense"):
        _row(stored, k)["values"]["FY2025"] = Z74["FY2025"][k]
    stored.pop("not_comparable")
    fixed = repair_statements(stored)
    assert list(fixed["not_comparable"]) == ["FY2025"]
    assert _row(fixed, "gross_profit")["values"]["FY2025"] is None
    assert _row(fixed, "gross_profit")["growth"]["FY2026"] is None
    assert _row(fixed, "operating_income")["values"]["FY2025"] == pytest.approx(2366.2e6)
    assert repair_statements(fixed) == fixed                                   # idempotent


def _z74_statement_rows():
    return [{"period": f"{k[2:]}-03-31", **v} for k, v in sorted(Z74.items())]


def test_workbook_ebit_is_the_reported_operating_income_in_the_flagged_year():
    from openpyxl import load_workbook
    from src.utils.valuation_workbook import build_workbook
    from tests.test_valuation_workbook import _run
    rows = _z74_statement_rows()

    def load(t, e):
        return {"rows": rows, "currency": "SGD", "estimates": [], "beta": None, "company_name": "T"}

    wb = load_workbook(io.BytesIO(build_workbook(_run(), "TEST", load_statements=load)))
    ws = wb["IS"]
    lab = {str(r[0].value): [c.value for c in r[2:7]] for r in ws.iter_rows(min_row=1, max_row=60) if r[0].value}
    ebit = lab["EBIT (operating income)"]
    assert ebit[3] == pytest.approx(2366.2)                  # FY2025: reported input, was =GP+opex (-1,821.9)
    assert str(ebit[2]).startswith("=")                       # other years still rebuild
    assert lab["Cost of goods sold"][3] is None and lab["Gross profit"][3] == "n/c"


def test_financials_route_reads_the_same_operating_income_and_flag():
    from app.backend.routes.analysis import _sg_financial_items
    items = _sg_financial_items([{"report_period": r["period"], **r} for r in _z74_statement_rows()],
                                "annual", "Telecommunications Services")
    fy25 = next(i for i in items if i["period_label"] == "FY2025")
    assert fy25["operating_income"] == pytest.approx(2366.2e6) and fy25["not_comparable"]
    assert [i["period_label"] for i in items] == ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]


# ── 2b. S68.SI: one column per fiscal year ──────────────────────────────────

def test_period_key_variants_merge_into_one_fiscal_year():
    rows = {"FY2025": {"net_income": 648.0e6, "operating_expense": 275.6e6},
            "FY2025 (Ending 2025-06-30)": {"net_income": 647.983e6, "total_assets": 4144.0e6},
            "FY2026 (Ending 2026-06-30)": {"net_income": 698.4e6}}
    n = normalise_period_keys(rows)
    assert sorted(n) == ["FY2025", "FY2026"]
    assert n["FY2025"]["net_income"] == 648.0e6                 # the canonical key wins
    assert n["FY2025"]["total_assets"] == 4144.0e6              # the variant fills what it lacks
    fs = build_financial_statements(rows)
    assert fs["periods"] == ["FY2025", "FY2026"]


def test_stored_duplicate_periods_are_merged_on_read():
    stored = {"layout": "bank", "currency": "SGD",
              "periods": ["FY2024", "FY2024 (Ending 2024-06-30)", "FY2025", "FY2025 (Ending 2025-06-30)"],
              "statements": {"income": {"title": "Income Statement", "rows": [
                  {"key": "total_income", "label": "Total income", "emphasis": True, "indent": 0, "growth": {},
                   "values": {"FY2024": 1221.4e6, "FY2024 (Ending 2024-06-30)": 1231.7e6,
                              "FY2025": 1353.3e6, "FY2025 (Ending 2025-06-30)": 1370.6e6}}]}}}
    fixed = repair_statements(stored)
    assert fixed["periods"] == ["FY2024", "FY2025"]
    r = fixed["statements"]["income"]["rows"][0]
    assert r["values"] == {"FY2024": 1221.4e6, "FY2025": 1353.3e6}
    assert r["growth"]["FY2025"] == pytest.approx(1353.3 / 1221.4 - 1)


def test_key_financials_read_one_record_per_fiscal_year():
    from src.data.report_families import family_rows
    raw = {"FY2025 (Ending 2025-06-30)": {"revenue": 1.37e9, "net_income": 0.65e9},
           "FY2026 (Ending 2026-06-30)": {"revenue": 1.56e9, "net_income": 0.70e9}}
    fys, _ = family_rows(raw, "Fee financials")
    assert fys == ["FY2025", "FY2026"]


# ── 2c. U11.SI: bank statement mapping ──────────────────────────────────────

# UOB, the stored run's raw_financials (S$).
U11 = {
    "FY2024": {"revenue": 27868e6, "interest_income": 23259e6, "interest_expense": 13585e6,
               "net_interest_income": 9674e6, "operating_expense": 6310e6, "net_income": 6045e6,
               "shareholders_equity": 49733e6, "shares_outstanding": 1698.04e6,
               "earnings_per_share": 0.0, "book_value_per_share": 0.0},
    "FY2025": {"revenue": 26103e6, "interest_income": 20676e6, "interest_expense": 11321e6,
               "net_interest_income": 9355e6, "operating_expense": 7083e6, "net_income": 4682e6,
               "shareholders_equity": 48500e6, "shares_outstanding": 1605.84e6,
               "earnings_per_share": 0.0, "book_value_per_share": 0.0},
}


def test_bank_per_share_zero_fills_are_derived_not_printed():
    fs = build_financial_statements({k: dict(v) for k, v in U11.items()}, sector="Banking", currency="SGD")
    assert fs["layout"] == "bank"
    bvps = _row(fs, "book_value_per_share")["values"]
    eps = _row(fs, "earnings_per_share")["values"]
    assert bvps["FY2025"] == pytest.approx(30.20, abs=0.01) and bvps["FY2024"] == pytest.approx(29.29, abs=0.01)
    assert eps["FY2025"] == pytest.approx(4682 / 1605.84, rel=1e-6)
    assert _row(fs, "total_income")["values"]["FY2025"] == pytest.approx(14782e6)


def test_bank_key_metrics_agree_with_the_web_statements():
    from src.data.report_families import family_rows
    fys, rows = family_rows({k: dict(v) for k, v in U11.items()}, "Banks")
    d = {label: vals for label, _, vals in rows}
    assert "Revenue" not in d
    assert d["Total income (NII + non-interest income)"] == [pytest.approx(14283e6), pytest.approx(14782e6)]
    assert d["Total equity"] == [pytest.approx(49733e6), pytest.approx(48500e6)]          # was n/a
    assert d["Return on equity"][1] == pytest.approx(4682 / 48500)                         # was n/a
    assert d["Book value / share"][1] == pytest.approx(30.20, abs=0.01)                    # zero-fill derived


def test_bank_route_revenue_is_total_income():
    from app.backend.routes.analysis import _sg_financial_items
    items = _sg_financial_items([{"report_period": "2025-12-31", **U11["FY2025"]}], "annual", "Banks - Regional")
    assert items[0]["revenue"] == pytest.approx(14782e6)
