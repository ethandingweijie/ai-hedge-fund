"""
src/tools/api.py — Financial Modeling Prep (FMP) adapter
=========================================================
All public function signatures are IDENTICAL to the previous implementation.
No other file in the codebase requires any changes.

API key priority (checked in order):
  1. FMP_API_KEY                   ← set this in .env.local
  2. FINANCIAL_DATASETS_API_KEY    ← legacy fallback

FMP stable base URL: https://financialmodelingprep.com/stable/
  (/api/v3 was deprecated August 2025 — legacy accounts only)

Free tier covers: prices, financials, ratios, key-metrics, market-cap.
News requires FMP Starter ($22/mo+).
Insider trades (/stable/insider-trading/search) require FMP Ultimate ($149/mo).
The pipeline degrades gracefully when plan-gated endpoints return empty.
"""

import datetime
import os
import time

import pandas as pd
import requests

from src.data.cache import get_cache
from src.tools.hk.ticker import is_hk_ticker, to_canonical
from src.tools.sg.ticker import is_sg_ticker, to_canonical as _sg_canonical
from src.data.models import (
    AnalystEstimates,
    CompanyNews,
    FinancialMetrics,
    Price,
    LineItem,
    InsiderTrade,
    CompanyFactsResponse,
    # response wrappers kept for import compatibility
    CompanyNewsResponse,
    FinancialMetricsResponse,
    PriceResponse,
    LineItemResponse,
    InsiderTradeResponse,
)

_cache = get_cache()
_STABLE = "https://financialmodelingprep.com/stable"
_V4     = "https://financialmodelingprep.com/api/v4"   # legacy — avoid; prefer /stable/ equivalents
_FREE_LIMIT = 5   # FMP free tier hard-cap (prices, financials). Paid endpoints bypass via uncap=True


# ── API key resolution ─────────────────────────────────────────────────────────

def _get_key(api_key: str | None) -> str | None:
    return (
        api_key
        or os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )


# ── FMP camelCase → snake_case field name tables ───────────────────────────────
# Based on actual /stable/ endpoint responses (verified 2026-03-14).

_INCOME_MAP: dict[str, str] = {
    "revenue":                           "revenue",
    "grossProfit":                       "gross_profit",
    "operatingIncome":                   "operating_income",
    "netIncome":                         "net_income",
    "ebitda":                            "ebitda",
    "ebit":                              "ebit",
    "researchAndDevelopmentExpenses":    "research_and_development",
    "depreciationAndAmortization":       "depreciation_and_amortization",
    "epsDiluted":                        "earnings_per_share",
    "weightedAverageShsOutDil":          "shares_outstanding",
    "interestExpense":                   "interest_expense",
    "incomeTaxExpense":                  "income_tax_expense",
    # Sector-specific additions
    "interestIncome":                    "interest_income",            # Financials: NIM reconstruction
    "provisionForCreditLosses":          "provision_for_loan_losses",  # Financials: credit cycle signal
}

_BALANCE_MAP: dict[str, str] = {
    "totalAssets":                       "total_assets",
    "totalCurrentAssets":                "current_assets",
    "totalLiabilities":                  "total_liabilities",
    "totalCurrentLiabilities":           "current_liabilities",
    "longTermDebt":                      "long_term_debt",
    "totalDebt":                         "total_debt",
    "netDebt":                           "net_debt",
    "accountsReceivables":               "accounts_receivable",
    "netReceivables":                    "accounts_receivable",   # fallback
    "cashAndCashEquivalents":            "cash_and_equivalents",
    "totalStockholdersEquity":           "shareholders_equity",
    # Sector-specific additions
    "deferredRevenue":                   "deferred_revenue",           # Tech: SaaS ARR proxy
    "goodwillAndIntangibleAssets":       "intangible_assets",          # Tech/Biopharma: IP moat
    "goodwill":                          "goodwill",                   # Biopharma: acquisition pipeline
    # Earnings quality additions
    "accountsPayables":                  "accounts_payable",           # EQ: DPO / cash conversion cycle
    "accountPayables":                   "accounts_payable",           # FMP alternate spelling
}

_CASHFLOW_MAP: dict[str, str] = {
    "operatingCashFlow":                 "operating_cash_flow",
    "freeCashFlow":                      "free_cash_flow",
    "capitalExpenditure":                "capital_expenditure",
    "netDividendsPaid":                  "dividends_and_distributions",
    "commonDividendsPaid":               "dividends_and_distributions",
    # Sector-specific additions
    "stockBasedCompensation":            "stock_based_compensation",   # Tech: dilution signal
}

# Ratios endpoint — annual fields (no suffix); TTM fields have 'TTM' suffix
_RATIOS_MAP: dict[str, str] = {
    "grossProfitMargin":                 "gross_margin",
    "netProfitMargin":                   "net_margin",
    "operatingProfitMargin":             "operating_margin",
    "priceToEarningsRatio":              "price_to_earnings_ratio",
    "priceToBookRatio":                  "price_to_book_ratio",
    "priceToSalesRatio":                 "price_to_sales_ratio",
    "debtToEquityRatio":                 "debt_to_equity",
    "debtToAssetsRatio":                 "debt_to_assets",
    "currentRatio":                      "current_ratio",
    "quickRatio":                        "quick_ratio",
    "cashRatio":                         "cash_ratio",
    "interestCoverageRatio":             "interest_coverage",
    "dividendPayoutRatio":               "payout_ratio",
    "operatingCashFlowRatio":            "operating_cash_flow_ratio",
    "bookValuePerShare":                 "book_value_per_share",
    "freeCashFlowPerShare":              "free_cash_flow_per_share",
    "operatingCashFlowPerShare":         "operating_cash_flow_per_share",
    "receivablesTurnover":               "receivables_turnover",
    "inventoryTurnover":                 "inventory_turnover",
    "assetTurnover":                     "asset_turnover",
    "daysOfSalesOutstanding":            "days_sales_outstanding",  # actually in key-metrics
}

# Key-metrics endpoint — annual fields
_KEY_METRICS_MAP: dict[str, str] = {
    "marketCap":                         "market_cap",
    "enterpriseValue":                   "enterprise_value",
    "evToSales":                         "enterprise_value_to_revenue_ratio",
    "evToEBITDA":                        "enterprise_value_to_ebitda_ratio",
    "freeCashFlowYield":                 "free_cash_flow_yield",
    "returnOnEquity":                    "return_on_equity",
    "returnOnAssets":                    "return_on_assets",
    "returnOnInvestedCapital":           "return_on_invested_capital",
    "currentRatio":                      "current_ratio",
    "daysOfSalesOutstanding":            "days_sales_outstanding",
    "operatingCycle":                    "operating_cycle",
    "earningsYield":                     "earnings_yield",
    "netDebtToEBITDA":                   "net_debt_ebitda",
    "workingCapital":                    "working_capital",
    "assetTurnover":                     "asset_turnover",
    "inventoryTurnover":                 "inventory_turnover",
    "receivablesTurnover":               "receivables_turnover",
}

# TTM suffix → strip 'TTM' and map the same base names
_KEY_METRICS_TTM_MAP: dict[str, str] = {
    "marketCap":                         "market_cap",
    "enterpriseValueTTM":                "enterprise_value",
    "evToSalesTTM":                      "enterprise_value_to_revenue_ratio",
    "evToEBITDATTM":                     "enterprise_value_to_ebitda_ratio",
    "freeCashFlowYieldTTM":              "free_cash_flow_yield",
    "returnOnEquityTTM":                 "return_on_equity",
    "returnOnAssetsTTM":                 "return_on_assets",
    "returnOnInvestedCapitalTTM":        "return_on_invested_capital",
    "currentRatioTTM":                   "current_ratio",
    "daysOfSalesOutstandingTTM":         "days_sales_outstanding",
    "earningsYieldTTM":                  "earnings_yield",
    "netDebtToEBITDATTM":               "net_debt_ebitda",
    "workingCapitalTTM":                 "working_capital",
    "assetTurnoverTTM":                  "asset_turnover",
    "inventoryTurnoverTTM":              "inventory_turnover",
}

_RATIOS_TTM_MAP: dict[str, str] = {
    "grossProfitMarginTTM":              "gross_margin",
    "netProfitMarginTTM":               "net_margin",
    "operatingProfitMarginTTM":          "operating_margin",
    "priceToEarningsRatioTTM":           "price_to_earnings_ratio",
    "priceToBookRatioTTM":               "price_to_book_ratio",
    "priceToSalesRatioTTM":              "price_to_sales_ratio",
    "debtToEquityRatioTTM":              "debt_to_equity",
    "debtToAssetsRatioTTM":              "debt_to_assets",
    "currentRatioTTM":                   "current_ratio",
    "quickRatioTTM":                     "quick_ratio",
    "cashRatioTTM":                      "cash_ratio",
    "interestCoverageRatioTTM":          "interest_coverage",
    "dividendPayoutRatioTTM":            "payout_ratio",
    "bookValuePerShareTTM":              "book_value_per_share",
    "freeCashFlowPerShareTTM":           "free_cash_flow_per_share",
    "receivablesTurnoverTTM":            "receivables_turnover",
    "inventoryTurnoverTTM":              "inventory_turnover",
    "assetTurnoverTTM":                  "asset_turnover",
    "operatingCashFlowRatioTTM":         "operating_cash_flow_ratio",
}


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _make_api_request(
    url: str,
    headers: dict,
    method: str = "GET",
    json_data: dict = None,
    max_retries: int = 3,
) -> requests.Response:
    """HTTP request with linear back-off on 429."""
    for attempt in range(max_retries + 1):
        if method.upper() == "POST":
            response = requests.post(url, headers=headers, json=json_data)
        else:
            response = requests.get(url, headers=headers)
        if response.status_code == 429 and attempt < max_retries:
            delay = 60 + (30 * attempt)
            print(f"Rate limited. Attempt {attempt + 1}/{max_retries + 1}. Waiting {delay}s...")
            time.sleep(delay)
            continue
        return response
    return response


def _fmp_get(path: str, params: dict, api_key: str | None, uncap: bool = False) -> list | dict | None:
    """
    GET from FMP stable API, appending apikey to every request.
    Retries on 429 (rate-limit) and 402 (burst-throttle — FMP free tier quirk).
    Hard-fails on 401/403/404 (auth / plan restriction / not found).

    uncap=True  — bypass _FREE_LIMIT cap; use for paid-plan endpoints
                  (insider-trading, news) where you want full result sets.
    uncap=False — cap limit at _FREE_LIMIT=5 (safe default for free tier).
    """
    key = _get_key(api_key)
    capped = dict(params)
    if "limit" in capped and not uncap:
        capped["limit"] = min(int(capped["limit"]), _FREE_LIMIT)
    full_params = {**capped, "apikey": key or ""}

    # Build a short label for logging (strip base URL, hide key)
    endpoint = path.replace(_STABLE, "").replace(_V4, "v4").lstrip("/")
    # economic-indicators use 'name'; treasury-rates use neither — fall back to '?'
    symbol = params.get("symbol", params.get("symbols", params.get("name", "?")))
    key_status = "key=OK" if key else "key=MISSING"

    for attempt in range(3):
        try:
            resp = requests.get(path, params=full_params, timeout=15)
        except requests.RequestException as exc:
            print(f"  [FMP] {endpoint} ({symbol}) — network error: {exc}")
            time.sleep(1)
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
                count = len(data) if isinstance(data, list) else (1 if data else 0)
                print(f"  [FMP] {endpoint} ({symbol}) — {resp.status_code} OK, {count} record(s) [{key_status}]")
                return data
            except Exception:
                print(f"  [FMP] {endpoint} ({symbol}) — 200 but JSON parse failed")
                return None

        if resp.status_code == 429:
            print(f"  [FMP] {endpoint} ({symbol}) — 429 rate-limited, waiting 60s...")
            time.sleep(60)
            continue

        if resp.status_code == 402:
            print(f"  [FMP] {endpoint} ({symbol}) — 402 burst-throttle (attempt {attempt+1}/3)")
            if attempt < 2:
                time.sleep(1.5 + attempt * 1.5)
                continue
            print(f"  [FMP] {endpoint} ({symbol}) — 402 giving up (plan restriction or quota)")
            return None

        if resp.status_code in (401, 403, 404):
            print(f"  [FMP] {endpoint} ({symbol}) — {resp.status_code} (auth/plan/not-found)")
            return None

        print(f"  [FMP] {endpoint} ({symbol}) — {resp.status_code} unexpected, retrying...")
        time.sleep(1)

    return None


def _fmp_period(period: str) -> str:
    return "quarter" if period in ("quarterly", "quarter") else "annual"


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── 1. Prices ──────────────────────────────────────────────────────────────────

def get_prices(
    ticker: str,
    start_date: str,
    end_date: str,
    api_key: str = None,
) -> list[Price]:
    """
    Daily EOD prices from FMP stable/historical-price-eod/light.
    Response: {symbol, date, price, volume} — price used as close.
    """
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import get_hk_prices
        canonical = to_canonical(ticker)
        cache_key = f"hk_{canonical}_{start_date}_{end_date}"
        if cached := _cache.get_prices(cache_key):
            return [Price(**p) for p in cached]
        result = get_hk_prices(canonical, start_date, end_date)
        if result:
            _cache.set_prices(cache_key, [p.model_dump() for p in result])
        return result
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import get_sg_prices
        canonical = _sg_canonical(ticker)
        cache_key = f"sg_{canonical}_{start_date}_{end_date}"
        if cached := _cache.get_prices(cache_key):
            return [Price(**p) for p in cached]
        result = get_sg_prices(canonical, start_date, end_date)
        if result:
            _cache.set_prices(cache_key, [p.model_dump() if hasattr(p, 'model_dump') else p for p in result])
        return result if isinstance(result, list) and result and isinstance(result[0], Price) else [Price(**p) for p in (result or [])]
    # ── US / FMP path ─────────────────────────────────────────────────────
    cache_key = f"fmp_{ticker}_{start_date}_{end_date}"
    if cached := _cache.get_prices(cache_key):
        return [Price(**p) for p in cached]

    data = _fmp_get(
        f"{_STABLE}/historical-price-eod/light",
        {"symbol": ticker, "from": start_date, "to": end_date},
        api_key,
    )
    if not data or not isinstance(data, list):
        return []

    prices: list[Price] = []
    for row in data:
        try:
            price_val = float(row.get("price") or row.get("close") or 0)
            prices.append(Price(
                open=price_val,
                close=price_val,
                high=price_val,
                low=price_val,
                volume=int(row.get("volume") or 0),
                time=row["date"],
            ))
        except Exception:
            continue

    prices.sort(key=lambda p: p.time)
    _cache.set_prices(cache_key, [p.model_dump() for p in prices])
    return prices


def get_adv(ticker: str, days: int = 30, api_key: str = None) -> dict:
    """
    Average Daily Volume (ADV) over the most recent `days` trading sessions.

    Reuses get_prices() → historical-price-eod/light (free tier, returns volume).
    Window is 1.6× requested days to guarantee `days` trading rows after
    stripping weekends and holidays.

    Returns:
        {
          "adv_shares":  float,   # mean daily shares traded
          "last_price":  float,   # most recent close
          "adv_dollars": float,   # adv_shares × last_price
          "days_used":   int,     # actual trading days averaged
        }
    or {} on API failure / no volume data.
    """
    from datetime import datetime, timedelta
    end_date   = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=int(days * 1.6))).strftime("%Y-%m-%d")

    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    if not prices:
        return {}

    # prices are sorted ascending by get_prices(); take the most recent `days` rows
    recent  = prices[-days:]
    volumes = [p.volume for p in recent if p.volume and p.volume > 0]
    if not volumes:
        return {}

    adv_shares = sum(volumes) / len(volumes)
    last_price = recent[-1].close
    return {
        "adv_shares":  adv_shares,
        "last_price":  last_price,
        "adv_dollars": adv_shares * last_price,
        "days_used":   len(volumes),
    }


# ── 2. Financial Metrics ───────────────────────────────────────────────────────

def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[FinancialMetrics]:
    """
    Merge FMP key-metrics + ratios into FinancialMetrics.
    TTM: uses key-metrics-ttm + ratios-ttm (single record, TTM-suffix fields).
    Annual/quarterly: uses key-metrics + ratios (date-keyed, no suffix).
    """
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import get_hk_financial_metrics
        canonical = to_canonical(ticker)
        cache_key = f"hk_metrics_{canonical}_{period}_{end_date}_{limit}"
        if cached := _cache.get_financial_metrics(cache_key):
            return [FinancialMetrics(**m) for m in cached]
        result = get_hk_financial_metrics(canonical, end_date, period, limit)
        if result:
            _cache.set_financial_metrics(cache_key, [m.model_dump() for m in result])
        return result
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import get_sg_financial_metrics
        canonical = _sg_canonical(ticker)
        cache_key = f"sg_metrics_{canonical}"
        if cached := _cache.get_financial_metrics(cache_key):
            return [FinancialMetrics(**m) for m in cached]
        raw = get_sg_financial_metrics(canonical)
        if raw:
            fm = FinancialMetrics(**{k: v for k, v in raw.items() if k in FinancialMetrics.model_fields})
            _cache.set_financial_metrics(cache_key, [fm.model_dump()])
            return [fm]
        return []
    # ── US / FMP path ─────────────────────────────────────────────────────
    cache_key = f"fmp_metrics_{ticker}_{period}_{end_date}_{limit}"
    if cached := _cache.get_financial_metrics(cache_key):
        return [FinancialMetrics(**m) for m in cached]

    if period == "ttm":
        km_data = _fmp_get(f"{_STABLE}/key-metrics-ttm", {"symbol": ticker}, api_key)
        rt_data = _fmp_get(f"{_STABLE}/ratios-ttm", {"symbol": ticker}, api_key)
        km_row = (km_data[0] if isinstance(km_data, list) and km_data
                  else km_data if isinstance(km_data, dict) else {}) or {}
        rt_row = (rt_data[0] if isinstance(rt_data, list) and rt_data
                  else rt_data if isinstance(rt_data, dict) else {}) or {}
        merged = {**km_row, **rt_row}
        rows = [{"_date": end_date, "_period": "ttm", **merged}]
        km_field_map = _KEY_METRICS_TTM_MAP
        rt_field_map = _RATIOS_TTM_MAP
    else:
        fmp_p = _fmp_period(period)
        km_data = _fmp_get(f"{_STABLE}/key-metrics",
                           {"symbol": ticker, "period": fmp_p, "limit": limit}, api_key) or []
        rt_data = _fmp_get(f"{_STABLE}/ratios",
                           {"symbol": ticker, "period": fmp_p, "limit": limit}, api_key) or []
        rt_by_date = {r["date"]: r for r in rt_data if "date" in r}
        rows = []
        for km in km_data:
            d = km.get("date", "")
            if d > end_date:
                continue
            merged = {**km, **rt_by_date.get(d, {})}
            rows.append({"_date": d, "_period": fmp_p, **merged})
        km_field_map = _KEY_METRICS_MAP
        rt_field_map = _RATIOS_MAP

    result: list[FinancialMetrics] = []
    for row in rows[:limit]:
        fields: dict = {
            "ticker": ticker,
            "report_period": row["_date"],
            "period": row["_period"],
            "currency": row.get("reportedCurrency", "USD"),
        }
        for fmp_key, our_key in {**km_field_map, **rt_field_map}.items():
            if our_key not in fields:
                v = _safe_float(row.get(fmp_key))
                if v is not None:
                    fields[our_key] = v
        # Fill required model fields with None if missing
        required = [
            "market_cap", "enterprise_value", "price_to_earnings_ratio", "price_to_book_ratio",
            "price_to_sales_ratio", "enterprise_value_to_ebitda_ratio",
            "enterprise_value_to_revenue_ratio", "free_cash_flow_yield", "peg_ratio",
            "gross_margin", "operating_margin", "net_margin", "return_on_equity",
            "return_on_assets", "return_on_invested_capital", "asset_turnover",
            "inventory_turnover", "receivables_turnover", "days_sales_outstanding",
            "operating_cycle", "working_capital_turnover", "current_ratio", "quick_ratio",
            "cash_ratio", "operating_cash_flow_ratio", "debt_to_equity", "debt_to_assets",
            "interest_coverage", "revenue_growth", "earnings_growth", "book_value_growth",
            "earnings_per_share_growth", "free_cash_flow_growth", "operating_income_growth",
            "ebitda_growth", "payout_ratio", "earnings_per_share", "book_value_per_share",
            "free_cash_flow_per_share",
        ]
        for f in required:
            fields.setdefault(f, None)
        try:
            result.append(FinancialMetrics(**fields))
        except Exception:
            continue

    _cache.set_financial_metrics(cache_key, [m.model_dump() for m in result])
    return result


# ── 3. Search Line Items ───────────────────────────────────────────────────────

def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[LineItem]:
    """
    Fetch income statement + balance sheet + cash flow + ratios from FMP,
    merge by date, translate camelCase → snake_case, return only the
    requested fields as LineItem objects (extra="allow" accepts all attrs).
    """
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import search_hk_line_items
        canonical = to_canonical(ticker)
        return search_hk_line_items(canonical, line_items, end_date, period, limit)
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import search_sg_line_items
        canonical = _sg_canonical(ticker)
        raw = search_sg_line_items(canonical, line_items, period, limit)
        # Convert dicts to LineItem models
        result = []
        for row in raw:
            li_fields = {k: v for k, v in row.items() if k in LineItem.model_fields}
            result.append(LineItem(**li_fields))
        return result
    # ── US / FMP path ─────────────────────────────────────────────────────
    fmp_p = _fmp_period(period)
    fetch_limit = max(limit + 2, 10)

    # Small delay between calls avoids burst-throttle 402s on FMP free tier
    income   = _fmp_get(f"{_STABLE}/income-statement",
                        {"symbol": ticker, "period": fmp_p, "limit": fetch_limit}, api_key) or []
    time.sleep(0.25)
    balance  = _fmp_get(f"{_STABLE}/balance-sheet-statement",
                        {"symbol": ticker, "period": fmp_p, "limit": fetch_limit}, api_key) or []
    time.sleep(0.25)
    cashflow = _fmp_get(f"{_STABLE}/cash-flow-statement",
                        {"symbol": ticker, "period": fmp_p, "limit": fetch_limit}, api_key) or []
    time.sleep(0.25)
    ratios   = _fmp_get(f"{_STABLE}/ratios",
                        {"symbol": ticker, "period": fmp_p, "limit": fetch_limit}, api_key) or []

    bal_by_date = {r["date"]: r for r in balance  if "date" in r}
    cf_by_date  = {r["date"]: r for r in cashflow if "date" in r}
    rt_by_date  = {r["date"]: r for r in ratios   if "date" in r}

    _ALL_MAPS = {**_INCOME_MAP, **_BALANCE_MAP, **_CASHFLOW_MAP, **_RATIOS_MAP}

    result: list[LineItem] = []

    for inc in income:
        date = inc.get("date", "")
        if not date or date > end_date:
            continue

        merged = {
            **inc,
            **bal_by_date.get(date, {}),
            **cf_by_date.get(date, {}),
            **rt_by_date.get(date, {}),
        }

        # Translate camelCase → snake_case
        snake: dict = {}
        for fmp_key, our_key in _ALL_MAPS.items():
            val = _safe_float(merged.get(fmp_key))
            if val is not None and our_key not in snake:
                snake[our_key] = val

        # Derived: gross_margin from grossProfit / revenue
        if "gross_margin" not in snake:
            gp = _safe_float(merged.get("grossProfit"))
            rev = snake.get("revenue") or _safe_float(merged.get("revenue"))
            if gp and rev and rev != 0:
                snake["gross_margin"] = gp / rev

        # Derived: net_debt fallback
        if "net_debt" not in snake:
            td = snake.get("total_debt")
            cash = snake.get("cash_and_equivalents")
            if td is not None and cash is not None:
                snake["net_debt"] = td - cash

        # Derived: book_value_per_share from ratios if not present
        # (already handled by _RATIOS_MAP → "bookValuePerShare")

        item_fields: dict = {
            "ticker":        ticker,
            "report_period": date,
            "period":        merged.get("period", fmp_p),
            "currency":      merged.get("reportedCurrency", "USD"),
        }
        for field in line_items:
            if field in snake:
                item_fields[field] = snake[field]

        try:
            result.append(LineItem(**item_fields))
        except Exception:
            continue

        if len(result) >= limit:
            break

    return result


# ── 4. Insider Trades ─────────────────────────────────────────────────────────

def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[InsiderTrade]:
    """
    Insider trades from FMP.
    Endpoint: /stable/insider-trading/search?symbol=TICKER&page=N&limit=100
    Requires FMP Ultimate plan ($149/mo). Returns empty list on lower tiers (403).
    The pipeline degrades gracefully — value trap agent still runs on remaining
    4 indicators when insider data is unavailable.

    Date filtering: /stable/insider-trading/search has no date range query params.
    Filtering is applied client-side after fetching each page.
    Pagination stops early once transactions fall before start_date.
    """
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import get_hk_insider_trades
        canonical = to_canonical(ticker)
        cache_key = f"hk_insider_{canonical}_{start_date or 'none'}_{end_date}_{limit}"
        if cached := _cache.get_insider_trades(cache_key):
            return [InsiderTrade(**t) for t in cached]
        result = get_hk_insider_trades(canonical, end_date, start_date, limit)
        if result:
            _cache.set_insider_trades(cache_key, [t.model_dump() for t in result])
        return result
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import get_sg_insider_trades
        canonical = _sg_canonical(ticker)
        raw = get_sg_insider_trades(canonical, start_date or "", end_date, limit)
        return [InsiderTrade(**t) for t in raw] if raw else []
    # ── US / FMP path ─────────────────────────────────────────────────────
    cache_key = f"fmp_insider_{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**t) for t in cached]

    all_trades: list[InsiderTrade] = []
    page = 0

    while len(all_trades) < limit:
        # uncap=True — paid endpoint, needs up to 100 records per page
        params: dict = {"symbol": ticker, "page": page, "limit": 100}
        data = _fmp_get(f"{_STABLE}/insider-trading/search", params, api_key, uncap=True)
        if not data or not isinstance(data, list):
            break

        passed_start = False
        for row in data:
            # Only open-market discretionary transactions carry signal.
            # Exclude awards (A-Award), exemptions (M-Exempt), tax withholding
            # (F-InKindConsideration), gifts (G-Gift), and other non-market types.
            tx_type = row.get("transactionType", "")
            if tx_type and tx_type not in ("P-Purchase", "S-Sale"):
                continue

            # Stable API field (corrected from v4 typo acquistionOrDisposition)
            acq = row.get("acquisitionOrDisposition", "A")
            shares = _safe_float(row.get("securitiesTransacted")) or 0.0
            if acq == "D":
                shares = -abs(shares)

            price_val   = _safe_float(row.get("price")) or 0.0
            owned_after  = _safe_float(row.get("securitiesOwned"))
            owned_before = (owned_after - shares) if owned_after is not None else None

            owner_type  = row.get("typeOfOwner", "") or ""
            filing_date = (row.get("filingDate") or end_date or "")[:10]
            trans_date  = (row.get("transactionDate") or "")[:10]

            # Client-side date filtering
            if end_date and trans_date and trans_date > end_date:
                continue
            if start_date and trans_date and trans_date < start_date:
                # Results are ordered newest-first; once we're before start_date
                # all remaining rows on this and further pages are also too old
                passed_start = True
                break

            try:
                all_trades.append(InsiderTrade(
                    ticker=row.get("symbol", ticker),
                    issuer=row.get("issuerName"),
                    name=row.get("reportingName"),
                    title=owner_type or None,
                    is_board_director=any(
                        kw in owner_type.lower() for kw in ("director", "board")
                    ),
                    transaction_date=trans_date or None,
                    transaction_shares=shares,
                    transaction_price_per_share=price_val or None,
                    transaction_value=abs(shares) * price_val if (shares and price_val) else None,
                    shares_owned_before_transaction=owned_before,
                    shares_owned_after_transaction=owned_after,
                    security_title=row.get("securityName"),
                    filing_date=filing_date,
                ))
            except Exception:
                continue

        if passed_start or len(data) < 100:
            break
        page += 1

    if all_trades:
        _cache.set_insider_trades(cache_key, [t.model_dump() for t in all_trades])
    return all_trades[:limit]


def get_insider_statistics(
    ticker: str,
    api_key: str = None,
) -> dict:
    """
    Aggregated insider trade statistics from FMP.
    Endpoint: /stable/insider-trading/statistics?symbol=TICKER
    Returns quarterly buy/sell counts, ratios, and totals.
    Used as a supplementary signal in the value trap agent.
    Returns the most recent quarter (index 0), or {} on failure.
    """
    data = _fmp_get(
        f"{_STABLE}/insider-trading/statistics",
        {"symbol": ticker},
        api_key,
        uncap=True,
    )
    if not data or not isinstance(data, list):
        return {}
    return data[0] if data else {}


# ── 5. Economic Indicators ────────────────────────────────────────────────────

def get_economic_indicator(
    name: str,
    from_date: str,
    to_date: str,
    api_key: str = None,
) -> list[dict]:
    """
    Single economic indicator time series from FMP.
    Endpoint: /stable/economic-indicators?name=NAME&from=DATE&to=DATE
    Returns list of {name, date, value} dicts sorted most-recent-first.
    Max 90-day date range per request (matches the pipeline's 90-day window).

    Useful names:
      federalFunds, inflationRate, CPI, unemploymentRate, initialClaims,
      consumerSentiment, retailMoneyFunds, smoothedUSRecessionProbabilities,
      retailSales, durableGoods, totalNonfarmPayroll, tradeBalanceGoodsAndServices,
      industrialProductionTotalIndex, totalVehicleSales
    """
    data = _fmp_get(
        f"{_STABLE}/economic-indicators",
        {"name": name, "from": from_date, "to": to_date},
        api_key,
        uncap=True,   # no 'limit' param on this endpoint; uncap is a no-op but signals intent
    )
    if not data or not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x.get("date", ""), reverse=True)


# ── 6. Treasury Rates ─────────────────────────────────────────────────────────

def get_treasury_rates(
    from_date: str,
    to_date: str,
    api_key: str = None,
) -> list[dict]:
    """
    Treasury yield curve rates from FMP.
    Endpoint: /stable/treasury-rates?from=DATE&to=DATE
    Returns list of {date, month1, month2, month3, month6,
                      year1, year2, year3, year5, year7, year10, year20, year30}
    sorted most-recent-first. Max 90-day date range per request.

    Key signals:
      year2 vs year10 spread → yield curve inversion (< -0.25% = recession signal)
      month3               → short-end rate level (tracks Fed policy)
    """
    data = _fmp_get(
        f"{_STABLE}/treasury-rates",
        {"from": from_date, "to": to_date},
        api_key,
        uncap=True,
    )
    if not data or not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x.get("date", ""), reverse=True)


# ── 7. Company News ───────────────────────────────────────────────────────────

def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[CompanyNews]:
    """
    Ticker news from FMP stable/news/stock.
    NOTE: Requires FMP Starter plan ($22/mo). Returns empty on free tier.
    Macro regime falls back to LLM default when news is unavailable.
    """
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import get_hk_company_news
        canonical = to_canonical(ticker)
        cache_key = f"hk_news_{canonical}_{start_date or 'none'}_{end_date}_{limit}"
        if cached := _cache.get_company_news(cache_key):
            return [CompanyNews(**n) for n in cached]
        result = get_hk_company_news(canonical, end_date, start_date, limit)
        if result:
            _cache.set_company_news(cache_key, [n.model_dump() for n in result])
        return result
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import get_sg_company_news
        canonical = _sg_canonical(ticker)
        raw = get_sg_company_news(canonical, start_date or "", end_date, limit)
        return [CompanyNews(**n) for n in raw] if raw else []
    # ── US / FMP path ─────────────────────────────────────────────────────
    cache_key = f"fmp_news_{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached := _cache.get_company_news(cache_key):
        return [CompanyNews(**n) for n in cached]

    all_news: list[CompanyNews] = []
    page = 0
    per_page = min(limit, 50)

    while len(all_news) < limit:
        params: dict = {
            "symbols": ticker,
            "limit": per_page,
            "page": page,
        }
        if end_date:
            params["publishedBefore"] = f"{end_date}T23:59:59"
        if start_date:
            params["publishedAfter"] = f"{start_date}T00:00:00"

        data = _fmp_get(f"{_STABLE}/news/stock", params, api_key)
        if not data or not isinstance(data, list):
            break

        for row in data:
            raw_date = row.get("publishedDate", "") or row.get("date", "") or ""
            date_str = raw_date[:10] if raw_date else end_date
            try:
                all_news.append(CompanyNews(
                    ticker=row.get("symbol", ticker),
                    title=row.get("title", ""),
                    author=row.get("author") or "",
                    source=row.get("site", "") or row.get("source", ""),
                    date=date_str,
                    url=row.get("url", ""),
                    sentiment=None,
                ))
            except Exception:
                continue

        if len(data) < per_page:
            break
        page += 1

    if all_news:
        _cache.set_company_news(cache_key, [n.model_dump() for n in all_news])
    return all_news[:limit]


def get_press_releases(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 20,
    api_key: str | None = None,
) -> list[CompanyNews]:
    """
    Press releases from FMP stable/news/press-releases.
    NOTE: Requires FMP Starter plan ($22/mo). Returns empty on free tier.
    Official company-authored releases carry higher signal weight in sentiment scoring.
    """
    # ── HK routing — FMP has no HK press-release data ─────────────────────
    if is_hk_ticker(ticker):
        return []
    # ── SG routing — no FMP press data for SGX ────────────────────────────
    if is_sg_ticker(ticker):
        return []
    cache_key = f"fmp_press_{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached := _cache.get_company_news(cache_key):
        return [CompanyNews(**n) for n in cached]

    params: dict = {
        "symbols": ticker,
        "limit": min(limit, 250),
        "page": 0,
    }
    if end_date:
        params["publishedBefore"] = f"{end_date}T23:59:59"
    if start_date:
        params["publishedAfter"] = f"{start_date}T00:00:00"

    data = _fmp_get(f"{_STABLE}/news/press-releases", params, api_key)
    if not data or not isinstance(data, list):
        return []

    releases: list[CompanyNews] = []
    for row in data:
        raw_date = row.get("publishedDate", "") or row.get("date", "") or ""
        date_str = raw_date[:10] if raw_date else end_date
        # Skip if outside requested window
        if date_str > end_date:
            continue
        if start_date and date_str < start_date:
            continue
        try:
            releases.append(CompanyNews(
                ticker=row.get("symbol", ticker),
                title=row.get("title", ""),
                author=row.get("author") or row.get("publisher") or "",
                source=row.get("site", "") or row.get("source", ""),
                date=date_str,
                url=row.get("url", ""),
                sentiment=None,
            ))
        except Exception:
            continue

    if releases:
        _cache.set_company_news(cache_key, [r.model_dump() for r in releases])
    return releases[:limit]


# ── 6. Market Cap ─────────────────────────────────────────────────────────────

def get_market_cap(
    ticker: str,
    end_date: str,
    api_key: str = None,
) -> float | None:
    """Current or historical market cap from FMP."""
    # ── HK routing ────────────────────────────────────────────────────────
    if is_hk_ticker(ticker):
        from src.tools.hk import get_hk_market_cap
        return get_hk_market_cap(to_canonical(ticker), end_date)
    # ── SG routing ────────────────────────────────────────────────────────
    if is_sg_ticker(ticker):
        from src.tools.sg import get_sg_market_cap
        return get_sg_market_cap(_sg_canonical(ticker))
    # ── US / FMP path ─────────────────────────────────────────────────────
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if end_date >= today:
        data = _fmp_get(f"{_STABLE}/market-capitalization", {"symbol": ticker}, api_key)
        if data and isinstance(data, list) and data:
            return _safe_float(data[0].get("marketCap"))

    # Historical
    data = _fmp_get(
        f"{_STABLE}/historical-market-capitalization",
        {"symbol": ticker, "limit": 5, "to": end_date},
        api_key,
    )
    if data and isinstance(data, list):
        for row in data:
            if row.get("date", "") <= end_date:
                v = _safe_float(row.get("marketCap"))
                if v:
                    return v

    # Fallback: derive from financial metrics
    metrics = get_financial_metrics(ticker, end_date, api_key=api_key)
    if metrics and metrics[0].market_cap:
        return metrics[0].market_cap

    return None


# ── 8. Analyst Consensus Estimates ────────────────────────────────────────────

def get_analyst_estimates(
    ticker: str,
    end_date: str,
    period: str = "annual",
    limit: int = 3,
    api_key: str = None,
) -> list[AnalystEstimates]:
    """
    Forward analyst consensus estimates from FMP /stable/analyst-estimates.

    Returns fiscal-year estimates whose period_end is AFTER end_date — i.e.,
    genuinely forward-looking from the analysis date. For backtesting runs this
    means estimates for years that hadn't closed yet as of end_date, which is the
    correct interpretation (we can't see consensus that post-dates our end_date).

    FMP plan requirement: Basic ($29/mo) or higher.
    On free tier FMP returns 402/403 → _fmp_get returns None → this returns [].
    The DCF agent handles [] by falling back to historical-average growth rates.

    limit=3 returns the next 3 fiscal years of estimates (Y+1, Y+2, Y+3).
    Y+1 (nearest year) is the primary anchor for the DCF base-case growth rate.
    """
    # ── HK routing — FMP has no HK analyst estimate data ──────────────────
    if is_hk_ticker(ticker):
        return []
    # ── SG routing — limited analyst estimates on free tier ────────────────
    if is_sg_ticker(ticker):
        return []
    cache_key = f"fmp_estimates_{ticker}_{end_date}_{_fmp_period(period)}_{limit}"
    if cached := _cache.get_analyst_estimates(cache_key):
        return [AnalystEstimates(**e) for e in cached]

    data = _fmp_get(
        f"{_STABLE}/analyst-estimates",
        {"symbol": ticker, "period": _fmp_period(period), "limit": limit},
        api_key,
    )
    if not data or not isinstance(data, list):
        return []

    estimates: list[AnalystEstimates] = []
    for row in data:
        period_end = (row.get("date") or "")[:10]
        # Only include estimates for fiscal years that are forward of end_date
        if not period_end or period_end <= end_date:
            continue
        try:
            rev_count = row.get("numberAnalystEstimatedRevenue")
            eps_count = row.get("numberAnalystsEstimatedEps")
            estimates.append(AnalystEstimates(
                ticker=row.get("symbol", ticker),
                period_end=period_end,
                revenue_avg=_safe_float(row.get("estimatedRevenueAvg")),
                revenue_low=_safe_float(row.get("estimatedRevenueLow")),
                revenue_high=_safe_float(row.get("estimatedRevenueHigh")),
                ebitda_avg=_safe_float(row.get("estimatedEbitdaAvg")),
                net_income_avg=_safe_float(row.get("estimatedNetIncomeAvg")),
                eps_avg=_safe_float(row.get("estimatedEpsAvg")),
                eps_low=_safe_float(row.get("estimatedEpsLow")),
                eps_high=_safe_float(row.get("estimatedEpsHigh")),
                analyst_count_revenue=int(rev_count) if rev_count is not None else None,
                analyst_count_eps=int(eps_count) if eps_count is not None else None,
            ))
        except Exception:
            continue

    # Sort ascending by period_end so estimates[0] is always the nearest fiscal year
    estimates.sort(key=lambda e: e.period_end)

    if estimates:
        _cache.set_analyst_estimates(cache_key, [e.model_dump() for e in estimates])
    return estimates


# ── Tavily Web Intelligence ───────────────────────────────────────────────────

def get_web_intelligence(
    ticker: str,
    sector: str,
    end_date: str,
    tavily_api_key: str | None = None,
) -> dict[str, str]:
    """
    Fetch real-time web intelligence for the Industry Specialist Agent (Phase 3).

    Makes 4 targeted Tavily searches and returns a dict with keys:
        company_news   — recent headlines and developments for this ticker
        ma_activity    — recent M&A deals and transaction multiples in the sector
        regulatory     — regulatory/policy developments affecting the sector
        competitive    — competitive landscape shifts and market share moves

    Returns {} on any failure (key not set, network error, quota exhausted).
    Results are cached per (ticker, sector, end_date) to avoid repeat calls.
    """
    api_key = tavily_api_key or os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {}

    _TAVILY_CACHE: dict = globals().setdefault("_TAVILY_CACHE", {})
    cache_key = f"{ticker}_{sector}_{end_date}"
    if cache_key in _TAVILY_CACHE:
        return _TAVILY_CACHE[cache_key]

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)

        year = end_date[:4] if end_date else "2026"
        prev_year = str(int(year) - 1)

        queries = {
            "company_news": (
                f"{ticker} company earnings revenue guidance analyst upgrade downgrade "
                f"news {prev_year} {year}"
            ),
            "ma_activity": (
                f"{sector} industry M&A acquisitions mergers deals transaction multiples "
                f"EV EBITDA {prev_year} {year}"
            ),
            "regulatory": (
                f"{sector} industry regulatory policy legislation antitrust tariff "
                f"government risk {prev_year} {year}"
            ),
            "competitive": (
                f"{sector} industry competitive landscape market share leader disruption "
                f"pricing power {year}"
            ),
        }

        results: dict[str, str] = {}
        for key, query in queries.items():
            try:
                response = client.search(
                    query=query,
                    max_results=4,
                    search_depth="basic",
                )
                snippets = []
                for r in response.get("results", []):
                    title = r.get("title", "")
                    content = r.get("content", "")[:300]
                    url = r.get("url", "")
                    snippets.append(f"• {title}: {content} [{url}]")
                results[key] = "\n".join(snippets) if snippets else "No results found."
            except Exception:
                results[key] = "Search unavailable."

        _TAVILY_CACHE[cache_key] = results
        return results

    except ImportError:
        return {}
    except Exception:
        return {}


# ── 9. SEC EDGAR — Form 4 Insider Trades (free, no API key) ──────────────────
#
# Source: https://data.sec.gov/submissions/CIK##########.json
#   - Lists all recent filings (including Form 4) with accession numbers
# Source: https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}
#   - Individual Form 4 XML files parsed for open-market buy/sell transactions
#
# Tier ordering in insider_activity_agent.py:
#   Tier 1: FMP /stable/insider-trading/search (Ultimate plan, $149/mo)
#   Tier 2: SEC EDGAR XML parsing (free, no auth)
#
# SEC rate-limit guideline: stay below 10 req/sec.  We sleep 0.12 s per request.
# User-Agent is mandatory per SEC policy.

import xml.etree.ElementTree as ET

_EDGAR_UA = "AI-Hedge-Fund-Research research@hedgefund.local"
_EDGAR_CIK_CACHE: dict[str, str] = {}   # ticker.upper() → CIK string (no leading zeros)
_EDGAR_SURPRISES_CACHE: dict[str, list] = {}  # cache key → earnings surprise rows


def _edgar_get(url: str) -> dict | list | None:
    """GET from SEC EDGAR with required User-Agent header.  No retries — EDGAR is reliable."""
    try:
        resp = requests.get(url, headers={"User-Agent": _EDGAR_UA}, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  [EDGAR] {resp.status_code} {url[:80]}")
        return None
    except Exception as exc:
        print(f"  [EDGAR] network error: {exc}")
        return None


def _get_cik(ticker: str) -> str | None:
    """
    Look up SEC CIK for a ticker symbol.
    Downloads the SEC company_tickers.json index (cached for the session).
    Returns the CIK as a plain string (no leading zeros), e.g. '320193' for AAPL.
    """
    ticker_up = ticker.upper()
    if ticker_up in _EDGAR_CIK_CACHE:
        return _EDGAR_CIK_CACHE[ticker_up]

    data = _edgar_get("https://www.sec.gov/files/company_tickers.json")
    if not isinstance(data, dict):
        return None

    # Populate the whole cache from this one call
    for entry in data.values():
        t = (entry.get("ticker") or "").upper()
        c = str(entry.get("cik_str", ""))
        if t and c:
            _EDGAR_CIK_CACHE[t] = c

    return _EDGAR_CIK_CACHE.get(ticker_up)


def _parse_form4_xml(xml_text: str, ticker: str, filing_date: str) -> list[InsiderTrade]:
    """
    Parse a single Form 4 XML file and return open-market buy (P) and sell (S)
    transactions as InsiderTrade objects.  Grants (A), tax withholding (F),
    gifts (G), and option exercises (M) are excluded.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_name  = (root.findtext(".//rptOwnerName")  or "").strip()
    owner_title = (root.findtext(".//officerTitle")   or "").strip()
    is_director = root.findtext(".//isDirector") == "1"

    trades: list[InsiderTrade] = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = (txn.findtext(".//transactionCode") or "").strip()
        if code not in ("P", "S"):
            continue
        try:
            txn_date    = (txn.findtext(".//transactionDate/value") or filing_date)[:10]
            shares_raw  = txn.findtext(".//transactionShares/value")
            price_raw   = txn.findtext(".//transactionPricePerShare/value")
            post_raw    = txn.findtext(".//sharesOwnedFollowingTransaction/value")

            shares = float(shares_raw) if shares_raw else 0.0
            if shares == 0:
                continue
            price  = float(price_raw) if price_raw else None
            value  = shares * price   if price    else None
            post   = float(post_raw)  if post_raw else None

            # transaction_shares: positive = buy (P), negative = sell (S)
            # transaction_value:  always unsigned (abs), matching FMP adapter convention.
            # The insider_activity_agent reads is_buy from sign of shares and applies
            # its own sign to value — so value must always be positive here.
            signed_shares = shares if code == "P" else -shares

            trades.append(InsiderTrade(
                ticker=ticker,
                issuer=ticker,
                name=owner_name,
                title=owner_title or None,
                is_board_director=is_director,
                transaction_date=txn_date,
                transaction_shares=signed_shares,
                transaction_price_per_share=price,
                transaction_value=value,   # always positive — sign is in transaction_shares
                shares_owned_before_transaction=None,
                shares_owned_after_transaction=post,
                security_title="Common Stock",
                filing_date=filing_date,
            ))
        except (ValueError, TypeError):
            continue

    return trades


def get_insider_trades_edgar(
    ticker: str,
    start_date: str,
    end_date: str,
    max_filings: int = 40,
) -> list[InsiderTrade]:
    """
    Fetch Form 4 insider trades from SEC EDGAR at no cost (no API key required).

    Flow:
      1. Look up CIK via company_tickers.json
      2. GET submissions/CIK##########.json to retrieve recent Form 4 filing list
      3. For each Form 4 in the date range, fetch + parse the XML
      4. Return InsiderTrade objects (same schema as FMP adapter)

    Falls back to [] if CIK lookup fails or no filings are found in range.
    """
    cache_key = f"edgar_form4_{ticker}_{start_date}_{end_date}"
    if cached := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**t) for t in cached]

    cik = _get_cik(ticker)
    if not cik:
        print(f"  [EDGAR] CIK not found for {ticker}")
        return []

    cik_padded = cik.zfill(10)
    subs = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    if not isinstance(subs, dict):
        return []

    recent   = subs.get("filings", {}).get("recent", {})
    forms    = recent.get("form", [])
    dates    = recent.get("filingDate", [])
    accnums  = recent.get("accessionNumber", [])
    pri_docs = recent.get("primaryDocument", [])

    # Collect Form 4 filings within the requested date window
    form4 = [
        {
            "date":      dates[i],
            "accession": accnums[i].replace("-", ""),
            "doc":       pri_docs[i],
        }
        for i, f in enumerate(forms)
        if f == "4" and i < len(dates) and start_date <= dates[i] <= end_date
    ][:max_filings]

    if not form4:
        print(f"  [EDGAR] No Form 4 filings for {ticker} in {start_date}–{end_date}")
        return []

    print(f"  [EDGAR] Parsing {len(form4)} Form 4 filing(s) for {ticker}")
    trades: list[InsiderTrade] = []
    for filing in form4:
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/"
            f"{filing['accession']}/{filing['doc']}"
        )
        try:
            time.sleep(0.12)   # Stay well under SEC's 10 req/sec guideline
            resp = requests.get(url, headers={"User-Agent": _EDGAR_UA}, timeout=15)
            if resp.status_code != 200:
                continue
            trades.extend(_parse_form4_xml(resp.text, ticker, filing["date"]))
        except Exception:
            continue

    print(f"  [EDGAR] {ticker} — {len(trades)} open-market transaction(s) parsed")
    if trades:
        _cache.set_insider_trades(cache_key, [t.model_dump() for t in trades])
    return trades


# ── 9b. EDGAR Annual Filing Reference ─────────────────────────────────────────

# US states of incorporation — companies incorporated here are domestic 10-K filers.
_US_STATES = {
    "DE", "CA", "NY", "NV", "MA", "TX", "FL", "WA", "OH", "PA",
    "MD", "IL", "MN", "WI", "CO", "OR", "NJ", "GA", "NC", "VA",
    "AZ", "IN", "CT", "UT", "MO", "TN", "MI", "WY", "SC", "LA",
}

_EDGAR_FILING_CACHE: dict[str, dict] = {}   # ticker.upper() → filing ref dict


def get_edgar_filing_refs(ticker: str) -> dict:
    """
    Fetch the most recent substantial SEC filing for a ticker from EDGAR.

    Resolution order (most authoritative → best-available):
      Pass 1 — Annual reports:        20-F, 20-F/A  (foreign)  /  10-K, 10-K/A  (domestic)
      Pass 2 — Cross-form annual:     try the other family (20-F if 10-K failed, vice-versa)
      Pass 3 — Recent IPO/prospectus: F-1, F-1/A, S-1, S-1/A  (company filed an IPO but no
                                       annual yet — common for tickers <1 year post-listing)
      Pass 4 — Periodic reports:      6-K, 6-K/A, 10-Q, 10-Q/A  (last-resort interim filing)
      Pass 5 — CIK stub:              No filing found but CIK is known — return minimal ref
                                       so citations can still say "SEC EDGAR CIK XXXXXXXXXX"

    Returns a dict with:
        cik, company_name, filing_type, is_foreign,
        accession_number  (e.g. "0001234567-24-012345")  — may be None for stub
        filing_date       (e.g. "2024-04-30")
        period_of_report  (e.g. "2023-12-31")
        filing_url        (direct EDGAR archives link)
        viewer_url        (EDGAR filing browser URL)
        fiscal_year       (4-digit string)
        is_ipo_prospectus (True when only F-1/S-1 was found — no annual yet)
        is_stub           (True when only CIK is available — no filing found)

    Returns {} only if CIK not in SEC company_tickers.json or EDGAR is unreachable.
    """
    ticker_up = ticker.upper()
    if ticker_up in _EDGAR_FILING_CACHE:
        return _EDGAR_FILING_CACHE[ticker_up]

    cik = _get_cik(ticker)
    if not cik:
        _EDGAR_FILING_CACHE[ticker_up] = {}
        return {}

    cik_padded = cik.zfill(10)
    subs = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    if not isinstance(subs, dict):
        _EDGAR_FILING_CACHE[ticker_up] = {}
        return {}

    company_name = subs.get("name", ticker_up)

    # Determine filing type: foreign issuers file 20-F, domestic file 10-K.
    # stateOfIncorporation is a 2-letter US state abbreviation for domestic corps,
    # or a foreign country code (e.g. "X2" for China, "I0" for Ireland, "V8" for Cayman).
    state_of_inc = (subs.get("stateOfIncorporation") or "").upper()
    is_foreign = state_of_inc not in _US_STATES and state_of_inc != ""

    filings      = subs.get("filings", {}).get("recent", {})
    forms        = filings.get("form",            [])
    accessions   = filings.get("accessionNumber", [])
    filing_dates = filings.get("filingDate",      [])
    periods      = filings.get("reportDate",      [])

    def _find(form_types: tuple) -> tuple:
        """Return (form, accession, filing_date, period) for the first match."""
        for form, acc, fdate, period in zip(forms, accessions, filing_dates, periods):
            if form in form_types:
                return form, acc, fdate, period
        return None, None, None, None

    # ── Pass 1: preferred annual form ────────────────────────────────────────
    preferred = ("20-F", "20-F/A") if is_foreign else ("10-K", "10-K/A")
    form, accession, filing_date, period = _find(preferred)

    # ── Pass 2: cross-family annual (handles mis-classified domestic/foreign) ─
    if not form:
        cross = ("10-K", "10-K/A") if is_foreign else ("20-F", "20-F/A")
        form, accession, filing_date, period = _find(cross)

    is_ipo_prospectus = False
    is_stub = False

    # ── Pass 3: recent IPO — prospectus only, no annual filed yet ────────────
    if not form:
        form, accession, filing_date, period = _find(("F-1", "F-1/A", "F-3", "F-3/A",
                                                        "S-1", "S-1/A", "S-11", "S-11/A"))
        if form:
            is_ipo_prospectus = True
            print(
                f"  [EDGAR] {ticker_up}: only IPO prospectus found ({form}) "
                f"— no annual report yet (recent IPO)"
            )

    # ── Pass 4: interim / current reports (last resort before stub) ──────────
    if not form:
        form, accession, filing_date, period = _find(("6-K", "6-K/A", "10-Q", "10-Q/A",
                                                        "20-F/A", "NT 20-F", "NT 10-K"))
        if form:
            print(f"  [EDGAR] {ticker_up}: using interim/periodic report ({form}) as fallback")

    # ── Pass 5: CIK stub — company exists in EDGAR but no useful filing ───────
    if not form:
        is_stub = True
        ref = {
            "cik":              cik,
            "cik_padded":       cik_padded,
            "company_name":     company_name,
            "filing_type":      "20-F" if is_foreign else "10-K",
            "is_foreign":       is_foreign,
            "accession_number": None,
            "filing_date":      None,
            "period_of_report": None,
            "fiscal_year":      None,
            "filing_url":       None,
            "viewer_url": (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik_padded}&type=&dateb=&owner=include&count=10"
            ),
            "is_ipo_prospectus": False,
            "is_stub":           True,
        }
        _EDGAR_FILING_CACHE[ticker_up] = ref
        print(
            f"  [EDGAR] {ticker_up}: CIK={cik} found but no annual/periodic filing "
            f"— returning CIK stub for citation attribution"
        )
        return ref

    filing_type   = "20-F" if "20-F" in form else ("10-K" if "10-K" in form else form)
    accession_url = accession.replace("-", "")
    fiscal_year   = (period or "")[:4] or (filing_date or "")[:4] or "unknown"

    ref = {
        "cik":              cik,
        "cik_padded":       cik_padded,
        "company_name":     company_name,
        "filing_type":      filing_type,
        "is_foreign":       is_foreign,
        "accession_number": accession,
        "filing_date":      filing_date,
        "period_of_report": period,
        "fiscal_year":      fiscal_year,
        "filing_url": (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_url}/"
        ),
        "viewer_url": (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik_padded}&type={filing_type}&dateb=&owner=include&count=5"
        ),
        "is_ipo_prospectus": is_ipo_prospectus,
        "is_stub":           False,
    }
    _EDGAR_FILING_CACHE[ticker_up] = ref
    print(
        f"  [EDGAR] {ticker_up}: {filing_type} | acc={accession} | "
        f"filed={filing_date} | period={period} | foreign={is_foreign}"
        + (" [IPO prospectus]" if is_ipo_prospectus else "")
    )
    return ref


# ── 10. Earnings Surprises (FMP free tier) ────────────────────────────────────

def get_earnings_surprises(
    ticker: str,
    end_date: str,
    limit: int = 8,
    api_key: str = None,
) -> list[dict]:
    """
    Earnings beat/miss history from FMP /stable/earnings-surprises.
    Available on FMP free tier.

    Returns a list of dicts (newest first):
        date, eps_actual, eps_estimated, surprise_pct, beat (bool)
    """
    # ── HK routing — FMP has no HK earnings-surprise data ─────────────────
    if is_hk_ticker(ticker):
        return []
    # ── SG routing — no free earnings-surprise data for SGX ───────────────
    if is_sg_ticker(ticker):
        return []
    cache_key = f"fmp_surprises_{ticker}_{end_date}_{limit}"
    if cache_key in _EDGAR_SURPRISES_CACHE:
        return _EDGAR_SURPRISES_CACHE[cache_key]

    data = _fmp_get(
        f"{_STABLE}/earnings-surprises",
        {"symbol": ticker, "limit": limit},
        api_key,
        uncap=True,
    )
    if not data or not isinstance(data, list):
        return []

    results = []
    for row in data:
        date = (row.get("date") or "")[:10]
        if date > end_date:
            continue
        eps_act = _safe_float(row.get("actualEarningResult") or row.get("actual"))
        eps_est = _safe_float(row.get("estimatedEarning")    or row.get("estimated"))
        if eps_act is None or eps_est is None:
            continue
        surprise_pct = (
            (eps_act - eps_est) / abs(eps_est) * 100 if eps_est != 0 else 0.0
        )
        results.append({
            "date":          date,
            "eps_actual":    eps_act,
            "eps_estimated": eps_est,
            "surprise_pct":  round(surprise_pct, 2),
            "beat":          eps_act >= eps_est,
        })

    results.sort(key=lambda x: x["date"], reverse=True)
    results = results[:limit]
    if results:
        _EDGAR_SURPRISES_CACHE[cache_key] = results
    return results


# ── Utility helpers (unchanged signatures) ────────────────────────────────────

def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


def get_short_interest(
    ticker: str,
    api_key: str = None,  # unused; kept for call-site compatibility
) -> list[dict]:
    """
    Short interest data via yfinance (FINRA bi-monthly settlement data, free).

    Returns up to 2 normalised dicts (newest first):
      [0] current settlement period
      [1] prior month (for trend calculation)

    Each dict keys: date, short_interest, short_percent (% of float),
      shares_float, days_to_cover, borrow_rate (always None), borrow_rate_is_bps, source.

    Returns empty list on error or if ticker has no short data.
    """
    # ── HK routing — FINRA short interest data is US-only ─────────────────
    if is_hk_ticker(ticker):
        return []
    # ── SG routing — FINRA short interest is US-only ──────────────────────
    if is_sg_ticker(ticker):
        return []
    cache_key = f"short_interest_yf_{ticker}"
    if cached := _cache.get_analyst_estimates(cache_key):
        return cached

    try:
        import yfinance as yf
        from datetime import date as _date

        info = yf.Ticker(ticker).info
        shares_short       = _safe_float(info.get("sharesShort"))
        shares_short_prior = _safe_float(info.get("sharesShortPriorMonth"))
        float_shares       = _safe_float(info.get("floatShares"))
        dtc                = _safe_float(info.get("shortRatio"))           # days-to-cover
        short_pct_raw      = _safe_float(info.get("shortPercentOfFloat"))  # 0–1 decimal

        # yfinance returns shortPercentOfFloat as a decimal (e.g. 0.16 = 16%)
        short_pct = short_pct_raw * 100.0 if short_pct_raw is not None else None

        # Derive from raw counts if the percentage field is missing
        if short_pct is None and shares_short and float_shares and float_shares > 0:
            short_pct = shares_short / float_shares * 100.0

        # Prior-month short % for trend
        short_pct_prior = None
        if shares_short_prior is not None and float_shares and float_shares > 0:
            short_pct_prior = shares_short_prior / float_shares * 100.0

        results: list[dict] = []

        if shares_short is not None or short_pct is not None:
            results.append({
                "date":               str(_date.today()),
                "short_interest":     shares_short,
                "short_percent":      round(short_pct, 4) if short_pct is not None else None,
                "shares_float":       float_shares,
                "days_to_cover":      dtc,
                "borrow_rate":        None,   # not available via yfinance
                "borrow_rate_is_bps": False,
                "source":             "yfinance",
            })

        if shares_short_prior is not None:
            results.append({
                "date":               str(info.get("sharesShortPreviousMonthDate", "")),
                "short_interest":     shares_short_prior,
                "short_percent":      round(short_pct_prior, 4) if short_pct_prior is not None else None,
                "shares_float":       float_shares,
                "days_to_cover":      None,   # prior DTC not provided by yfinance
                "borrow_rate":        None,
                "borrow_rate_is_bps": False,
                "source":             "yfinance",
            })

        if results:
            _cache.set_analyst_estimates(cache_key, results)
        return results

    except Exception as exc:
        print(f"  [api] get_short_interest error for {ticker}: {exc}")
        return []


def get_price_data(
    ticker: str,
    start_date: str,
    end_date: str,
    api_key: str = None,
) -> pd.DataFrame:
    return prices_to_df(get_prices(ticker, start_date, end_date, api_key=api_key))


# ── 7. FX Rate ────────────────────────────────────────────────────────────────

# Fallback rates for common ADR / cross-listed reporting currencies → USD.
# Updated Jan 2026 (Damodaran / Bloomberg midpoints).
# Used only when the FMP forex endpoint is unavailable or returns no data.
_FX_FALLBACK_RATES: dict[str, float] = {
    "CNYUSD": 0.1376,   # China (mainland) RMB / offshore CNY  (Mar 2026 midpoint)
    "CNHUSD": 0.1376,   # offshore CNH ≈ CNY
    "HKDUSD": 0.1282,   # Hong Kong dollar
    "EURUSD": 1.085,    # Euro
    "GBPUSD": 1.265,    # British pound
    "JPYUSD": 0.00655,  # Japanese yen
    "INRUSD": 0.01175,  # Indian rupee
    "KRWUSD": 0.000705, # South Korean won
    "TWDUSD": 0.0305,   # Taiwan dollar
    "BRLUSD": 0.171,    # Brazilian real
    "CADUSD": 0.738,    # Canadian dollar
    "AUDUSD": 0.628,    # Australian dollar
    "SGDUSD": 0.745,    # Singapore dollar
    "CHFUSD": 1.115,    # Swiss franc
    "SEKUSD": 0.093,    # Swedish krona
    "NOKUSD": 0.090,    # Norwegian krone
    "ILSUSD": 0.267,    # Israeli shekel (ILS)
    "MXNUSD": 0.049,    # Mexican peso
}


def get_fx_rate(
    from_currency: str,
    to_currency: str = "USD",
    api_key: str = None,
) -> float:
    """
    Return the FX rate to convert 1 unit of *from_currency* into *to_currency*.

    Priority:
        1. FMP /stable/fx-quote (live mid-rate)
        2. _FX_FALLBACK_RATES hardcoded table (Jan 2026 midpoints)
        3. 1.0 with a warning (unknown pair — caller should flag in output)

    Used by dcf_agent.py to normalise ADR financials reported in non-USD
    currencies (e.g. Alibaba/BABA reports in CNY, BIDU in CNY, etc.) before
    running the DCF engine, which assumes all monetary inputs are in USD.
    """
    from_currency = (from_currency or "USD").upper().strip()
    to_currency   = (to_currency   or "USD").upper().strip()

    if from_currency == to_currency:
        return 1.0

    pair = f"{from_currency}{to_currency}"

    # ── 1. Try FMP live forex quote (direct pair) ─────────────────────────
    def _fetch_fx_quote(symbol: str) -> float | None:
        try:
            data = _fmp_get(f"{_STABLE}/fx-quote", {"symbol": symbol}, api_key)
            if data and isinstance(data, list) and len(data) > 0:
                row = data[0]
                price = _safe_float(
                    row.get("bid") or row.get("ask") or row.get("price") or row.get("last")
                )
                if price and price > 0:
                    return price
        except Exception:
            pass
        return None

    price = _fetch_fx_quote(pair)
    if price:
        print(f"  [FX] {pair} — live rate {price:.6f} (FMP direct)")
        return price

    # ── 1b. Try inverse pair (e.g. USDCNY when CNYUSD not available) ─────
    # FMP offers many pairs only in one direction (e.g. USDCNY but not CNYUSD).
    inverse_pair = f"{to_currency}{from_currency}"
    inv_price = _fetch_fx_quote(inverse_pair)
    if inv_price and inv_price > 0:
        rate = 1.0 / inv_price
        print(f"  [FX] {pair} — live rate {rate:.6f} (FMP inverse of {inverse_pair}={inv_price:.4f})")
        return rate

    # ── 2. Hardcoded fallback ─────────────────────────────────────────────
    rate = _FX_FALLBACK_RATES.get(pair)
    if rate:
        print(f"  [FX] {pair} — fallback rate {rate:.6f} (FMP unavailable; Mar 2026 midpoint)")
        return rate

    # ── 3. Unknown pair ───────────────────────────────────────────────────
    print(f"  [FX] {pair} — unknown pair, returning 1.0 (no conversion applied)")
    return 1.0
