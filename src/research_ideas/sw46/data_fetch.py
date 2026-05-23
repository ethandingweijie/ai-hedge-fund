"""
src/research_ideas/sw46/data_fetch.py
======================================
FMP wrappers that retrieve everything the Tragic Algebra + ROIC + IV15
pipeline needs for one ticker. Uses src/tools/api.py for the standard
income/balance/cash-flow merge, then makes a small number of raw FMP
calls for fields that search_line_items() doesn't surface (RSU tax
withholding, short-term investments, lease liabilities, average price).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

from src.tools.api import (
    _fmp_get,
    _safe_float,
    _STABLE,
    get_market_cap,
    get_analyst_estimates,
    search_line_items,
)


# ------ Yearly cash-flow / balance-sheet rows we don't get cleanly from
# search_line_items(): re-fetch raw FMP statements once to grab the extra
# fields. Keep this minimal — one cash-flow call + one balance-sheet call
# per ticker.


@dataclass
class YearlyStatement:
    fiscal_year: int
    report_date: str
    # Income / cash-flow
    net_income: Optional[float] = None
    sbc_expense: Optional[float] = None
    common_stock_repurchased: Optional[float] = None
    common_stock_issued: Optional[float] = None
    # RSU tax-withholding lines (FMP exposes these inconsistently; we try
    # several alternate names and fall back to estimator if all blank).
    rsu_tax_withholding: Optional[float] = None
    rsu_tax_withholding_estimated: bool = False
    interest_income: Optional[float] = None
    capital_lease_payments: Optional[float] = None
    revenue: Optional[float] = None
    # Balance sheet
    cash_and_equivalents: Optional[float] = None
    short_term_investments: Optional[float] = None
    long_term_investments: Optional[float] = None
    total_debt: Optional[float] = None
    long_term_operating_leases: Optional[float] = None
    shareholders_equity: Optional[float] = None
    diluted_shares: Optional[float] = None
    # Derived
    avg_share_price: Optional[float] = None


@dataclass
class TickerBundle:
    ticker: str
    years: list[YearlyStatement]
    market_cap: Optional[float] = None
    current_price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    forward_revenue_growth: Optional[float] = None
    forward_net_income: Optional[float] = None   # consensus Y+1 net income
    reported_currency: str = "USD"


# ─── FMP fetchers ──────────────────────────────────────────────────────────


def _fetch_annual_income_statement(ticker: str, limit: int) -> list[dict]:
    data = _fmp_get(
        f"{_STABLE}/income-statement",
        {"symbol": ticker, "period": "annual", "limit": limit},
        api_key=None,
        uncap=True,
    )
    return data if isinstance(data, list) else []


def _fetch_annual_balance_sheet(ticker: str, limit: int) -> list[dict]:
    data = _fmp_get(
        f"{_STABLE}/balance-sheet-statement",
        {"symbol": ticker, "period": "annual", "limit": limit},
        api_key=None,
        uncap=True,
    )
    return data if isinstance(data, list) else []


def _fetch_annual_cash_flow(ticker: str, limit: int) -> list[dict]:
    data = _fmp_get(
        f"{_STABLE}/cash-flow-statement",
        {"symbol": ticker, "period": "annual", "limit": limit},
        api_key=None,
        uncap=True,
    )
    return data if isinstance(data, list) else []


# ─── As-reported 10-K extractor (the real C source) ───────────────────────
#
# `/stable/financial-reports-json` returns the issuer's 10-K as nested
# sections, each preserving the original GAAP line names. This is the only
# FMP endpoint that exposes "Payments for taxes related to net share
# settlement of equity awards" — i.e. C in the Tragic Algebra formula.
#
# Each annual report typically contains THREE fiscal years of cash-flow data
# (current + 2 prior), so one call covers a 3-year window. For a 7-year
# history we fetch the latest report + one report from 3 years earlier and
# merge by fiscal year.


# Match the C line under any of its common GAAP wordings. Patterns are
# checked top-to-bottom; first match wins.
#
# Observed wordings across tickers tested:
#   AAPL : "Payments for taxes related to net share settlement of equity awards"
#   NOW  : "Taxes paid related to net share settlement of equity awards"
#   FRSH : "Payment of withholding taxes on net share settlement of equity awards"
#   GOOG : "Tax withholding payments related to net share settlement of stock awards"
#   META : "Net taxes paid related to net share settlement of equity awards"
#   MSFT : (not broken out — lumped into "Common stock repurchased")
#   CRM  : (not broken out — lumped into "Common stock repurchased")
#
# The catch-all anchor is "net share settlement" + a tax/withhold word within
# 40 chars on either side. That's tight enough to avoid false positives
# (lease lines, equity-awards narratives) but flexible on word order.
_C_LINE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(?:tax|withhold)\w*[\s\w]{0,40}net\s+share\s+settlement"
        r"|net\s+share\s+settlement[\s\w]{0,40}(?:tax|withhold)",
        re.IGNORECASE,
    ),
    re.compile(r"tax\s+withholding\s+(?:on|related\s+to|for)\s+(?:RSU|restricted\s+stock|equity\s+awards?|share[-\s]based|stock[-\s]based)", re.IGNORECASE),
    re.compile(r"payments?\s+of\s+withholding\s+tax", re.IGNORECASE),
]

# Match operating-lease and finance/capital-lease principal-payment lines
# in the 10-K "Supplemental Cash Flow Information Related to Leases" section.
# Sum any positive matches per year — that gives total annual lease cash outflow.
_LEASE_OPERATING_PATTERNS: list[re.Pattern] = [
    re.compile(r"operating\s+cash\s+flows?\s+from\s+operating\s+leases?", re.IGNORECASE),
    re.compile(r"cash\s+paid.*operating\s+leases?", re.IGNORECASE),
]

_LEASE_FINANCING_PATTERNS: list[re.Pattern] = [
    # Note: "Financing cash flows from finance leases" is the principal portion;
    # "Operating cash flows from finance leases" is the interest portion — both
    # are real cash outflows; we sum them.
    re.compile(r"financing\s+cash\s+flows?\s+from\s+finance\s+leases?", re.IGNORECASE),
    re.compile(r"operating\s+cash\s+flows?\s+from\s+finance\s+leases?", re.IGNORECASE),
    re.compile(r"principal\s+payments?\s+(?:on|of|under)\s+(?:finance|capital)\s+leases?", re.IGNORECASE),
    re.compile(r"repayments?\s+of\s+(?:finance|capital)\s+lease", re.IGNORECASE),
]


def _fetch_financial_report(ticker: str, year: int, period: str = "FY") -> Optional[dict]:
    """One as-reported 10-K (or 10-Q if period is Q1..Q4) from FMP."""
    data = _fmp_get(
        f"{_STABLE}/financial-reports-json",
        {"symbol": ticker, "year": year, "period": period},
        api_key=None,
        uncap=True,
    )
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def _parse_fiscal_year(label: str) -> Optional[int]:
    """Extract the four-digit year from labels like 'Sep. 24, 2022' or '2024-06-30'."""
    if not label:
        return None
    m = re.search(r"\b(20\d{2}|19\d{2})\b", str(label))
    return int(m.group(1)) if m else None


def _parse_units(header: str) -> float:
    """Return the USD multiplier from the section header (e.g. '$ in Millions' -> 1e6)."""
    if not header:
        return 1.0
    h = str(header).lower()
    if "in billions" in h:
        return 1e9
    if "in millions" in h:
        return 1e6
    if "in thousands" in h:
        return 1e3
    return 1.0


def _extract_c_per_year(report: dict) -> dict[int, float]:
    """
    Walk every section of a financial-reports-json response, find any row whose
    key matches a C-line pattern, parse the 'items' array to map columns to
    fiscal years, and return {fiscal_year: dollars_in_USD}.

    Returns {} if no C line is found (issuer doesn't break it out).
    """
    out: dict[int, float] = {}
    if not isinstance(report, dict):
        return out

    for section_name, rows in report.items():
        if not isinstance(rows, list) or not rows:
            continue

        # Section header is the FIRST row's first key; carries the unit annotation.
        section_header_key = None
        if isinstance(rows[0], dict):
            for k in rows[0].keys():
                section_header_key = k
                break
        units_mult = _parse_units(section_header_key or "")

        # Find the 'items' row to learn what fiscal years each column represents.
        items_labels: list[str] = []
        for row in rows:
            if isinstance(row, dict) and "items" in row:
                items_labels = row["items"]
                break
        if not items_labels:
            continue
        year_by_index = {i: _parse_fiscal_year(lbl) for i, lbl in enumerate(items_labels)}

        # Scan every other row's key for a C-line match.
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, vals in row.items():
                if key == "items":
                    continue
                if not isinstance(vals, list):
                    continue
                if not any(p.search(str(key)) for p in _C_LINE_PATTERNS):
                    continue
                # Match! Pull values column-by-column.
                for i, v in enumerate(vals):
                    fy = year_by_index.get(i)
                    if fy is None:
                        continue
                    try:
                        f = float(v) if v not in (None, "", " ") else None
                    except (TypeError, ValueError):
                        continue
                    if f is None:
                        continue
                    val = abs(f) * units_mult  # cash outflow often reported as negative
                    # Don't overwrite if already populated from a higher-priority section.
                    out.setdefault(fy, val)
    return out


def _extract_lease_payments_per_year(report: dict) -> dict[int, float]:
    """
    Walk every section of the 10-K JSON and sum any rows matching operating-
    lease or finance-lease principal-payment patterns. The MSFT-style
    "Supplemental Cash Flow Information Related to Leases" section is the
    canonical home; other issuers put lease cash flows directly on the
    main statement.

    Returns {fiscal_year: total_annual_lease_cash_outflow_USD}.
    """
    out: dict[int, float] = {}
    if not isinstance(report, dict):
        return out

    for section_name, rows in report.items():
        if not isinstance(rows, list) or not rows:
            continue

        section_header_key = None
        if isinstance(rows[0], dict):
            for k in rows[0].keys():
                section_header_key = k
                break
        units_mult = _parse_units(section_header_key or "")

        items_labels: list[str] = []
        for row in rows:
            if isinstance(row, dict) and "items" in row:
                items_labels = row["items"]
                break
        if not items_labels:
            continue
        year_by_index = {i: _parse_fiscal_year(lbl) for i, lbl in enumerate(items_labels)}

        # Track which patterns already fired for each year IN THIS SECTION to
        # avoid double-counting (e.g. several "operating cash flows from
        # finance leases" wording variants might match the same row).
        seen_pattern_year: set = set()

        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, vals in row.items():
                if key == "items":
                    continue
                if not isinstance(vals, list):
                    continue
                matched = None
                for p in _LEASE_OPERATING_PATTERNS:
                    if p.search(str(key)):
                        matched = ("op", p.pattern)
                        break
                if matched is None:
                    for p in _LEASE_FINANCING_PATTERNS:
                        if p.search(str(key)):
                            matched = ("fin", p.pattern)
                            break
                if matched is None:
                    continue
                for i, v in enumerate(vals):
                    fy = year_by_index.get(i)
                    if fy is None:
                        continue
                    sig = (section_name, matched[0], matched[1], fy)
                    if sig in seen_pattern_year:
                        continue
                    seen_pattern_year.add(sig)
                    try:
                        f = float(v) if v not in (None, "", " ") else None
                    except (TypeError, ValueError):
                        continue
                    if f is None:
                        continue
                    out[fy] = out.get(fy, 0.0) + abs(f) * units_mult
    return out


def fetch_lease_payments_history(ticker: str, fiscal_years: list[int]) -> dict[int, float]:
    """
    Mirror of fetch_c_history but for lease cash outflows. Walks back the
    same way; each annual report covers ~3 years of supplemental disclosures.
    """
    if not fiscal_years:
        return {}

    target_years = sorted(set(fiscal_years), reverse=True)
    out: dict[int, float] = {}

    while target_years:
        request_year = target_years[0]
        report = _fetch_financial_report(ticker, request_year, period="FY")
        time.sleep(0.20)
        if report:
            found = _extract_lease_payments_per_year(report)
            for fy, v in found.items():
                out.setdefault(fy, v)
        target_years = [y for y in target_years if y not in out and y > request_year - 3]
        if not report or (request_year in target_years):
            target_years = [y for y in target_years if y < request_year - 2]
        if len(out) == 0 and target_years and target_years[0] == request_year:
            break

    return out


def fetch_10k_supplements_history(
    ticker: str, fiscal_years: list[int]
) -> dict[int, dict]:
    """
    One pass over /stable/financial-reports-json that extracts BOTH:
      - C (RSU tax-withholding cash outflow)
      - Lease cash payments (operating + finance/capital combined)
    from each year's 10-K. Each annual report typically contains ~3 fiscal
    years of supplemental data, so a 7-year window needs at most 3 API calls.

    Strategy: for each fiscal year still needing data, pick the next request
    year (highest remaining) that is NOT within ±2 years of a year we've
    already requested. After fetching, remove satisfied years from the
    needed list. Stop when no candidates remain.

    Returns {fiscal_year: {"c": float | None, "lease_pmts": float | None}}.
    Years where the issuer doesn't break a line out simply lack that key.
    """
    if not fiscal_years:
        return {}

    needed = sorted(set(fiscal_years), reverse=True)
    tried_with_data: list[int] = []   # requests that produced at least one match
    tried_all: list[int] = []         # every request attempt (avoid infinite loop)
    out: dict[int, dict] = {}

    while needed:
        # Eligible candidate years: not within ±2 of a SUCCESSFUL prior request
        # (which would already have covered them), and not previously tried.
        candidates = [
            y for y in needed
            if not any(abs(y - t) <= 2 for t in tried_with_data)
            and y not in tried_all
        ]
        if not candidates:
            break
        request_year = max(candidates)
        tried_all.append(request_year)

        report = _fetch_financial_report(ticker, request_year, period="FY")
        time.sleep(0.20)
        if report:
            c_found = _extract_c_per_year(report)
            lease_found = _extract_lease_payments_per_year(report)
            added_any = False
            for fy in set(list(c_found.keys()) + list(lease_found.keys())):
                slot = out.setdefault(fy, {})
                if fy in c_found:
                    slot.setdefault("c", c_found[fy])
                    added_any = True
                if fy in lease_found:
                    slot.setdefault("lease_pmts", lease_found[fy])
                    added_any = True
            if added_any:
                tried_with_data.append(request_year)

        needed = [y for y in needed if y not in out]

    return out


def fetch_c_history(ticker: str, fiscal_years: list[int]) -> dict[int, float]:
    """Back-compat wrapper. Use fetch_10k_supplements_history() instead."""
    sup = fetch_10k_supplements_history(ticker, fiscal_years)
    return {fy: v["c"] for fy, v in sup.items() if "c" in v}


# ─── End as-reported extractor ────────────────────────────────────────────


def _fetch_quote(ticker: str) -> Optional[dict]:
    data = _fmp_get(
        f"{_STABLE}/quote",
        {"symbol": ticker},
        api_key=None,
        uncap=True,
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


def _fetch_year_avg_price(ticker: str, year: int) -> Optional[float]:
    """Avg of daily close over the fiscal year. One quote-history call per year."""
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    data = _fmp_get(
        f"{_STABLE}/historical-price-eod/light",
        {"symbol": ticker, "from": start, "to": end},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    prices = [_safe_float(r.get("price")) for r in data if r.get("price") is not None]
    prices = [p for p in prices if p is not None]
    if not prices:
        return None
    return sum(prices) / len(prices)


# ─── Field extractors with multiple alternate FMP names ────────────────────


def _pick(row: dict, *keys, default=None):
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return default


def _extract_rsu_tax_withholding(cf_row: dict) -> Optional[float]:
    """
    FMP /stable/cash-flow-statement does NOT expose RSU tax withholding as a
    discrete line — it's bundled into `otherFinancingActivities`. Returning
    None here forces tragic_algebra._estimate_C() to take the SBC * 0.37
    fallback path (the article notes ~37% IRS withholding on RSU vesting).
    Field kept as a hook in case FMP exposes it for paid-tier accounts;
    if you find a working field name, add it to the _pick() list below.
    """
    raw = _pick(
        cf_row,
        # No working name in FMP /stable/ as of 2026. Listed for future hope:
        "taxesPaidForNetShareSettlementOfEquityAwards",
        "paymentsRelatedToTaxWithholdingForShareBasedCompensation",
        "employeeTaxesPaidRelatedToNetShareSettlement",
    )
    val = _safe_float(raw)
    if val is None:
        return None
    return abs(val)


def _extract_capital_lease_payments(cf_row: dict) -> Optional[float]:
    """
    FMP /stable/cash-flow-statement does NOT break out lease payments as a
    discrete line either. They're inside `otherFinancingActivities`. We
    return None and roic.py treats that as 0 in the OE adjustment. To get a
    real number, parse it from the 10-K MD&A and add a per-ticker override
    JSON (analogous to sw46_purchase_obligations.json).
    """
    raw = _pick(
        cf_row,
        "paymentsOfCapitalLeaseObligations",  # not in /stable/ — kept for FMP plan upgrades
        "repaymentsOfCapitalLeaseObligations",
        "principalPaymentsUnderCapitalLeases",
    )
    val = _safe_float(raw)
    if val is None:
        return None
    return abs(val)


def _extract_interest_income(income_row: dict) -> Optional[float]:
    """FMP /stable/income-statement exposes `interestIncome` for most issuers."""
    raw = _pick(
        income_row,
        "interestIncome",
        "netInterestIncome",
        "nonOperatingIncomeExcludingInterest",  # rare last-resort
    )
    val = _safe_float(raw)
    if val is None or val < 0:
        return None
    return val


def _extract_lt_operating_leases(bal_row: dict) -> Optional[float]:
    """
    For the ROIC denominator we want LONG-TERM (non-current) lease
    obligations only — the current portion stays in working capital, which
    is part of the productive capital base.

    FMP /stable/balance-sheet-statement actually exposes the split:
      capitalLeaseObligationsCurrent     <- skip (in WC)
      capitalLeaseObligationsNonCurrent  <- THIS is what we want
      capitalLeaseObligations            <- TOTAL (sum of the two)

    Verified on MSFT 2024-06-30:
      capitalLeaseObligations          = 69.0B
      capitalLeaseObligationsNonCurrent = ~67B  (most of the total)
      capitalLeaseObligationsCurrent    = ~2B

    Note: post-ASC-842, this lump now includes operating leases too, not
    just finance/capital leases — the field name is legacy. We use it as
    a proxy for "all LT lease obligations" which is the right ROIC input.
    """
    raw = _pick(
        bal_row,
        "capitalLeaseObligationsNonCurrent",  # PRIMARY — what we want
        "capitalLeaseObligations",            # fallback if split not provided
        "operatingLeaseLiabilitiesNonCurrent",
        "longTermOperatingLeaseLiabilities",
    )
    return _safe_float(raw)


# ─── Public bundler ────────────────────────────────────────────────────────


def fetch_ticker_bundle(ticker: str, history_years: int = 7) -> Optional[TickerBundle]:
    """
    Fetch everything one SW46 ticker needs. history_years in [4, 11] per
    the article. Returns None on hard failure (e.g., FMP 404).
    """
    limit = max(4, min(history_years, 11))

    income = _fetch_annual_income_statement(ticker, limit)
    time.sleep(0.25)
    balance = _fetch_annual_balance_sheet(ticker, limit)
    time.sleep(0.25)
    cashflow = _fetch_annual_cash_flow(ticker, limit)
    time.sleep(0.25)
    quote = _fetch_quote(ticker)

    if not income:
        return None

    bal_by_date = {r["date"]: r for r in balance if "date" in r}
    cf_by_date = {r["date"]: r for r in cashflow if "date" in r}
    reported_currency = income[0].get("reportedCurrency", "USD") if income else "USD"

    years: list[YearlyStatement] = []
    for row in income:
        date_str = row.get("date", "")
        if not date_str:
            continue
        try:
            fy = int(date_str[:4])
        except ValueError:
            continue

        bal_row = bal_by_date.get(date_str, {})
        cf_row = cf_by_date.get(date_str, {})

        rsu = _extract_rsu_tax_withholding(cf_row)

        ys = YearlyStatement(
            fiscal_year=fy,
            report_date=date_str,
            net_income=_safe_float(row.get("netIncome")),
            sbc_expense=_safe_float(cf_row.get("stockBasedCompensation")),
            common_stock_repurchased=_safe_float(cf_row.get("commonStockRepurchased")),
            # FMP /stable/ field is `commonStockIssuance`; keep `commonStockIssued`
            # as a fallback for any legacy /v3 responses that still flow through.
            common_stock_issued=_safe_float(
                cf_row.get("commonStockIssuance") or cf_row.get("commonStockIssued")
            ),
            rsu_tax_withholding=rsu,
            rsu_tax_withholding_estimated=(rsu is None),
            interest_income=_extract_interest_income(row),
            capital_lease_payments=_extract_capital_lease_payments(cf_row),
            revenue=_safe_float(row.get("revenue")),
            cash_and_equivalents=_safe_float(bal_row.get("cashAndCashEquivalents")),
            short_term_investments=_safe_float(bal_row.get("shortTermInvestments")),
            long_term_investments=_safe_float(bal_row.get("longTermInvestments")),
            total_debt=_safe_float(bal_row.get("totalDebt")),
            long_term_operating_leases=_extract_lt_operating_leases(bal_row),
            shareholders_equity=_safe_float(bal_row.get("totalStockholdersEquity")),
            diluted_shares=_safe_float(row.get("weightedAverageShsOutDil")),
        )
        years.append(ys)

    # Sort old -> new for stable downstream loops
    years.sort(key=lambda x: x.fiscal_year)

    # ── Pull real C and lease payments from as-reported 10-K(s) ─────────
    # `/stable/financial-reports-json` exposes the original GAAP lines that
    # /stable/cash-flow-statement flattens away. One annual report covers
    # ~3 fiscal years (current + 2 prior), so a 7-year history needs at
    # most 3 API calls. fetch_10k_supplements_history() extracts BOTH:
    #   - C (RSU tax withholding) from the financing CF section
    #   - Annual lease cash payments from "Supplemental Cash Flow
    #     Information Related to Leases" (operating + finance, summed)
    try:
        sup = fetch_10k_supplements_history(ticker, [y.fiscal_year for y in years])
    except Exception:
        sup = {}
    for ys in years:
        slot = sup.get(ys.fiscal_year, {})
        real_c = slot.get("c")
        if real_c is not None:
            ys.rsu_tax_withholding = real_c
            ys.rsu_tax_withholding_estimated = False
        # else: tragic_algebra._estimate_C() will run SBC*0.37 fallback.

        real_lease_pmts = slot.get("lease_pmts")
        if real_lease_pmts is not None:
            # Overwrite the cf_row guess (which was None for /stable/ anyway).
            ys.capital_lease_payments = real_lease_pmts

    # One year-avg-price call per fiscal year (cheap on FMP free tier
    # because we only have <=11 years per ticker).
    for ys in years:
        ys.avg_share_price = _fetch_year_avg_price(ticker, ys.fiscal_year)
        time.sleep(0.15)

    # Forward revenue growth + forward NI from analyst consensus (used by IV15).
    fwd_rev = None
    fwd_ni = None
    try:
        today = _dt.date.today().isoformat()
        estimates = get_analyst_estimates(ticker, end_date=today, period="annual", limit=2)
        if estimates and len(estimates) >= 1 and years:
            last_rev = years[-1].revenue or 0
            yr1 = estimates[0].revenue_avg
            if last_rev and yr1 and last_rev > 0:
                fwd_rev = (yr1 / last_rev) - 1.0
            fwd_ni = estimates[0].net_income_avg
    except Exception:
        pass

    current_price = _safe_float(quote.get("price")) if quote else None
    market_cap = _safe_float(quote.get("marketCap")) if quote else None
    shares_now = _safe_float(quote.get("sharesOutstanding")) if quote else None
    if shares_now is None and years:
        shares_now = years[-1].diluted_shares

    return TickerBundle(
        ticker=ticker,
        years=years,
        market_cap=market_cap,
        current_price=current_price,
        shares_outstanding=shares_now,
        forward_revenue_growth=fwd_rev,
        forward_net_income=fwd_ni,
        reported_currency=reported_currency,
    )
