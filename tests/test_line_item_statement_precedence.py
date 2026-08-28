"""The income statement is authoritative for its own lines.

search_line_items merges four FMP endpoints onto one date. The cash flow
statement also carries `netIncome` and `depreciationAndAmortization`, and
for a number of issuers those are NOT the income-statement figures — FMP
puts pre-tax profit in the cash-flow reconciliation.

With the income statement merged FIRST, the cash-flow value won:

    DBS  FY2024   net income  S$12,884mn   (pre-tax)   vs  S$11,289mn actual
    OCBC          out by 20.7% in its worst year

`net_income` feeds EPS, ROE and therefore the bank GGM target P/B, so the
error propagated all the way to the target price. Singapore and Hong Kong
banks and REITs were the worst affected; US large caps happened to report
the same number on both statements, which is why this stayed invisible.

No network: the four endpoint payloads are stubbed with a deliberate
disagreement between them.
"""

from __future__ import annotations

from unittest.mock import patch

from src.tools.api import search_line_items

DATE = "2024-12-31"

_INCOME = [{
    "date": DATE, "period": "FY", "reportedCurrency": "SGD",
    "revenue": 20_000_000_000,
    "netIncome": 11_289_000_000,             # the truth
    "incomeBeforeTax": 12_884_000_000,
    "incomeTaxExpense": 1_594_000_000,
    "depreciationAndAmortization": 800_000_000,
}]
_BALANCE = [{"date": DATE, "totalAssets": 800_000_000_000}]
# FMP's cash-flow reconciliation starts from PRE-TAX profit for this filer.
_CASHFLOW = [{
    "date": DATE,
    "netIncome": 12_884_000_000,             # disagrees on purpose
    "depreciationAndAmortization": 950_000_000,
    "operatingCashFlow": 15_000_000_000,
}]
_RATIOS = [{"date": DATE, "bookValuePerShare": 24.28}]


def _stub(url, params=None, api_key=None, **kw):
    if "income-statement" in url:
        return _INCOME
    if "balance-sheet" in url:
        return _BALANCE
    if "cash-flow" in url:
        return _CASHFLOW
    if "ratios" in url:
        return _RATIOS
    return []


def _fetch(fields):
    with patch("src.tools.api._fmp_get", side_effect=_stub), \
         patch("src.tools.api.time.sleep", return_value=None):
        return search_line_items("D05.SI", fields, "2026-08-28", api_key="x")


def test_net_income_comes_from_the_income_statement():
    rows = _fetch(["net_income"])
    assert rows, "no rows returned"
    assert rows[0].net_income == 11_289_000_000, (
        "cash-flow netIncome overwrote the income statement — this is the "
        "DBS S$12,884mn / OCBC 20.7% regression"
    )


def test_dand_a_comes_from_the_income_statement():
    rows = _fetch(["depreciation_and_amortization"])
    assert rows[0].depreciation_and_amortization == 800_000_000


def test_cash_flow_only_fields_still_resolve():
    """Income winning must not shut the other statements out."""
    rows = _fetch(["operating_cash_flow", "total_assets", "book_value_per_share"])
    assert rows[0].operating_cash_flow == 15_000_000_000
    assert rows[0].total_assets == 800_000_000_000
    assert rows[0].book_value_per_share == 24.28


def test_pretax_and_net_income_stay_distinct():
    """The symptom that exposed it: a bank whose pre-tax equalled its net.

    Pre-tax less tax does not reconcile to net income exactly — DBS carries
    a S$1mn minority interest — so this checks the two lines are distinct
    and reconcile to within a rounding tolerance, not to the cent.
    """
    rows = _fetch(["net_income", "pretax_income", "income_tax_expense"])
    r = rows[0]
    assert r.pretax_income != r.net_income
    residual = r.pretax_income - r.income_tax_expense - r.net_income
    assert abs(residual) < 0.001 * r.net_income
