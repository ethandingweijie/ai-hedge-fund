"""
src/research_ideas/sw46/_verify_fmp_fields.py
==============================================
Run with an FMP_API_KEY set. Prints the actual FMP /stable/ keys present
for one ticker (default MSFT) and flags which SW46 inputs we can find
versus which we have to estimate.

Invoke:
    $env:FMP_API_KEY = "your-key-here"
    .\.venv\Scripts\python.exe -m src.research_ideas.sw46._verify_fmp_fields MSFT

Reports a green check for each required field that came back populated,
a yellow tilde for fields that exist but are null, and a red cross for
fields that are absent from the response.
"""
from __future__ import annotations

import os
import sys

from src.tools.api import _fmp_get, _STABLE


# Fields we look at, grouped by endpoint.
INCOME_REQUIRED = [
    "netIncome",
    "revenue",
    "weightedAverageShsOutDil",
    "interestIncome",
]

BALANCE_REQUIRED = [
    "cashAndCashEquivalents",
    "shortTermInvestments",
    "longTermInvestments",
    "totalDebt",
    "totalStockholdersEquity",
    "capitalLeaseObligations",  # lease proxy
]

CASHFLOW_REQUIRED = [
    "stockBasedCompensation",
    "commonStockRepurchased",
    "commonStockIssuance",
]

CASHFLOW_OPTIONAL = [
    # FMP /stable/ doesn't expose these; we want to see if any are present
    # for free-tier accounts. If a name lights up, add it to data_fetch.py.
    "taxesPaidForNetShareSettlementOfEquityAwards",
    "paymentsRelatedToTaxWithholdingForShareBasedCompensation",
    "employeeTaxesPaidRelatedToNetShareSettlement",
    "paymentsOfCapitalLeaseObligations",
    "repaymentsOfCapitalLeaseObligations",
]

BALANCE_OPTIONAL = [
    "operatingLeaseLiabilitiesNonCurrent",
    "longTermOperatingLeaseLiabilities",
]


def _check(rows: list[dict], fields: list[str], label: str) -> None:
    print(f"\n=== {label} ===")
    if not rows:
        print("  (no rows returned — check API key + ticker)")
        return
    row = rows[0]
    available = set(row.keys())
    for f in fields:
        if f in available:
            v = row.get(f)
            mark = "OK " if v not in (None, 0, "") else "~~ "
            print(f"  [{mark}] {f:55s} = {v}")
        else:
            print(f"  [XX] {f:55s} (absent from response)")
    # Show ALL keys for the first row so user can spot unexpected names
    print(f"  ... (full key list, {len(available)} total): {sorted(available)[:30]}{'...' if len(available)>30 else ''}")


def main(ticker: str = "MSFT") -> None:
    if not os.environ.get("FMP_API_KEY") and not os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        print("ERROR: set FMP_API_KEY or FINANCIAL_DATASETS_API_KEY before running.")
        sys.exit(2)

    print(f"Checking FMP /stable/ field availability for {ticker}\n")

    income = _fmp_get(
        f"{_STABLE}/income-statement",
        {"symbol": ticker, "period": "annual", "limit": 1},
        api_key=None,
        uncap=True,
    ) or []
    _check(income, INCOME_REQUIRED, "income-statement (required)")

    balance = _fmp_get(
        f"{_STABLE}/balance-sheet-statement",
        {"symbol": ticker, "period": "annual", "limit": 1},
        api_key=None,
        uncap=True,
    ) or []
    _check(balance, BALANCE_REQUIRED, "balance-sheet-statement (required)")
    _check(balance, BALANCE_OPTIONAL, "balance-sheet-statement (optional, ideal-but-absent)")

    cashflow = _fmp_get(
        f"{_STABLE}/cash-flow-statement",
        {"symbol": ticker, "period": "annual", "limit": 1},
        api_key=None,
        uncap=True,
    ) or []
    _check(cashflow, CASHFLOW_REQUIRED, "cash-flow-statement (required)")
    _check(cashflow, CASHFLOW_OPTIONAL, "cash-flow-statement (optional — Rsu tax / lease pmts)")

    quote = _fmp_get(
        f"{_STABLE}/quote",
        {"symbol": ticker},
        api_key=None,
        uncap=True,
    ) or []
    _check(quote, ["price", "marketCap", "sharesOutstanding"], "quote")

    prices = _fmp_get(
        f"{_STABLE}/historical-price-eod/light",
        {"symbol": ticker, "from": "2024-01-01", "to": "2024-12-31"},
        api_key=None,
        uncap=True,
    ) or []
    print(f"\n=== historical-price-eod/light ===")
    if prices and isinstance(prices, list):
        print(f"  OK — {len(prices)} daily rows; sample: {prices[0]}")
    else:
        print(f"  (no rows)")

    # As-reported 10-K — the only place C ("Payments for taxes related to
    # net share settlement of equity awards") is preserved.
    from src.research_ideas.sw46.data_fetch import (
        _fetch_financial_report,
        _extract_c_per_year,
    )
    print(f"\n=== financial-reports-json (C extractor) ===")
    # Pick a recent FY likely to be filed; if it 404s, walk back one year.
    for try_year in (2024, 2023, 2022):
        report = _fetch_financial_report(ticker, try_year, period="FY")
        if not report:
            print(f"  ({ticker} FY{try_year}: no report)")
            continue
        c_by_year = _extract_c_per_year(report)
        if c_by_year:
            print(f"  OK — {ticker} FY{try_year} report yielded C for years:")
            for fy in sorted(c_by_year):
                print(f"    FY{fy}: ${c_by_year[fy]/1e6:,.1f}M")
        else:
            sections = list(report.keys())[:8]
            print(f"  ~~ {ticker} FY{try_year} report returned but C line not found.")
            print(f"     Sections present (first 8): {sections}")
        break

    print("\nSUMMARY")
    print("  - Required fields above are what data_fetch.py reads.")
    print("  - financial-reports-json is the PRIMARY source for C (RSU tax withholding).")
    print("  - SBC*0.37 estimator runs ONLY if the 10-K doesn't break C out.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "MSFT")
