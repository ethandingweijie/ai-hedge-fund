"""
HK financial metrics via AKShare.

Merges three AKShare endpoints to populate a FinancialMetrics object:
  1. stock_hk_financial_indicator_em  → EPS, NAV/share, market cap, revenue,
                                        net income, ROE, ROA, P/E, P/B, net margin
  2. stock_hk_growth_comparison_em    → revenue growth, EPS growth, op-income growth
  3. stock_hk_valuation_comparison_em → P/E TTM, P/B MRQ, P/S TTM, P/CF TTM

Returns list[FinancialMetrics] — same type as the US path.
"""
from __future__ import annotations

import logging

from src.data.models import FinancialMetrics
from src.tools.hk._utils import _parse_float, _safe_div, _safe_sub, _safe_add
from src.tools.hk.mappings import (
    HK_INDICATOR_COLS,
    HK_GROWTH_COLS,
    HK_VALUATION_COLS,
)
from src.tools.hk.currency import get_reporting_currency
from src.tools.hk.ticker import to_akshare_code, to_canonical

_log = logging.getLogger(__name__)

_YIYI = 1e8  # 亿 → raw units


def get_hk_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    """
    Return a single FinancialMetrics snapshot for an HK-listed stock.

    The HK AKShare endpoints only provide the *latest* snapshot (no time series),
    so this always returns a list of length 0 or 1.

    Parameters
    ----------
    ticker   : any valid HK ticker format
    end_date : "YYYY-MM-DD" — used as report_period label
    period   : passed through ("ttm" / "annual")
    limit    : ignored (only 1 period available from snapshot endpoints)

    Returns
    -------
    list[FinancialMetrics] — empty on error, one element on success
    """
    try:
        import akshare as ak
    except ImportError:
        _log.error("akshare not installed")
        return []

    symbol = to_akshare_code(ticker)
    canonical = to_canonical(ticker)

    indicator = _fetch_indicator(ak, symbol)
    growth = _fetch_growth(ak, symbol)
    valuation = _fetch_valuation(ak, symbol)

    if not indicator and not valuation:
        _log.warning("No AKShare financial data returned for %s", symbol)
        return []

    # ── Market cap ────────────────────────────────────────────────────────
    market_cap = _extract_market_cap(indicator)

    # ── Per-share ─────────────────────────────────────────────────────────
    eps = _parse_float(indicator.get("earnings_per_share"))
    bvps = _parse_float(indicator.get("book_value_per_share"))

    # ── Income ────────────────────────────────────────────────────────────
    revenue = _parse_float(indicator.get("revenue"))
    net_income = _parse_float(indicator.get("net_income"))

    # ── Margins ───────────────────────────────────────────────────────────
    net_margin_pct = _parse_float(indicator.get("net_margin"))
    net_margin = net_margin_pct / 100 if net_margin_pct is not None else None

    roe_pct = _parse_float(indicator.get("return_on_equity"))
    roe = roe_pct / 100 if roe_pct is not None else None

    roa_pct = _parse_float(indicator.get("return_on_assets"))
    roa = roa_pct / 100 if roa_pct is not None else None

    # ── Valuation multiples ───────────────────────────────────────────────
    # Prefer TTM values from valuation_comparison; fall back to indicator
    pe = _parse_float(valuation.get("price_to_earnings_ratio")) \
         or _parse_float(indicator.get("price_to_earnings_ratio"))
    pb = _parse_float(valuation.get("price_to_book_ratio")) \
         or _parse_float(indicator.get("price_to_book_ratio"))
    ps = _parse_float(valuation.get("price_to_sales_ratio"))
    pcf = _parse_float(valuation.get("price_to_cash_flow_ratio"))

    # ── Dividend ──────────────────────────────────────────────────────────
    div_yield_pct = _parse_float(indicator.get("dividend_yield"))
    div_yield = div_yield_pct / 100 if div_yield_pct is not None else None

    payout_pct = _parse_float(indicator.get("payout_ratio"))
    payout = payout_pct / 100 if payout_pct is not None else None

    # ── Growth ────────────────────────────────────────────────────────────
    rev_growth_pct = _parse_float(growth.get("revenue_growth"))
    rev_growth = rev_growth_pct / 100 if rev_growth_pct is not None else None

    eps_growth_pct = _parse_float(growth.get("earnings_per_share_growth"))
    eps_growth = eps_growth_pct / 100 if eps_growth_pct is not None else None

    op_income_growth_pct = _parse_float(growth.get("operating_income_growth"))
    op_income_growth = op_income_growth_pct / 100 if op_income_growth_pct is not None else None

    # ── Enterprise value (approximate) ────────────────────────────────────
    # EV = market_cap + total_debt - cash  (debt/cash not in snapshot endpoints)
    # We compute a partial EV here; line_items.py fills the rest.
    enterprise_value = market_cap  # placeholder; refined in line_items if needed

    metrics = FinancialMetrics(
        ticker=canonical,
        report_period=end_date,
        period=period,
        currency=get_reporting_currency(symbol),
        # Market
        market_cap=market_cap,
        enterprise_value=enterprise_value,
        # Valuation
        price_to_earnings_ratio=pe,
        price_to_book_ratio=pb,
        price_to_sales_ratio=ps,
        enterprise_value_to_ebitda_ratio=None,   # not available from snapshot
        enterprise_value_to_revenue_ratio=None,  # not available from snapshot
        free_cash_flow_yield=None,
        peg_ratio=None,
        # Profitability
        gross_margin=None,       # requires income statement — populated in line_items
        operating_margin=None,   # requires income statement
        net_margin=net_margin,
        return_on_equity=roe,
        return_on_assets=roa,
        return_on_invested_capital=None,
        # Efficiency
        asset_turnover=None,
        inventory_turnover=None,
        receivables_turnover=None,
        days_sales_outstanding=None,
        operating_cycle=None,
        working_capital_turnover=None,
        # Liquidity
        current_ratio=None,      # requires balance sheet
        quick_ratio=None,
        cash_ratio=None,
        operating_cash_flow_ratio=None,
        # Leverage
        debt_to_equity=None,     # requires balance sheet
        debt_to_assets=None,
        interest_coverage=None,
        # Growth
        revenue_growth=rev_growth,
        earnings_growth=eps_growth,
        book_value_growth=None,
        earnings_per_share_growth=eps_growth,
        free_cash_flow_growth=None,
        operating_income_growth=op_income_growth,
        ebitda_growth=None,
        # Per-share
        earnings_per_share=eps,
        book_value_per_share=bvps,
        free_cash_flow_per_share=None,
        payout_ratio=payout,
    )

    return [metrics]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fetch_indicator(ak, symbol: str) -> dict:
    """Fetch stock_hk_financial_indicator_em and return a normalised dict."""
    try:
        df = ak.stock_hk_financial_indicator_em(symbol=symbol)
    except Exception as exc:
        _log.debug("financial_indicator_em failed for %s: %s", symbol, exc)
        return {}

    if df is None or df.empty:
        return {}

    try:
        # stock_hk_financial_indicator_em returns 1 row; column headers are the labels.
        # Correct parse: zip(column_names, row_values)
        raw: dict = dict(zip(df.columns, df.iloc[0]))
    except Exception:
        return {}

    result = {}
    for cn_key, en_key in HK_INDICATOR_COLS.items():
        if cn_key in raw:
            result[en_key] = raw[cn_key]
    return result


def _fetch_growth(ak, symbol: str) -> dict:
    """Fetch stock_hk_growth_comparison_em and return a normalised dict for the target ticker."""
    try:
        df = ak.stock_hk_growth_comparison_em(symbol=symbol)
    except Exception as exc:
        _log.debug("growth_comparison_em failed for %s: %s", symbol, exc)
        return {}

    if df is None or df.empty:
        return {}

    # The DataFrame contains the target ticker plus peers.
    # Row 0 is the target company.
    try:
        row = df.iloc[0]
    except IndexError:
        return {}

    result = {}
    for cn_key, en_key in HK_GROWTH_COLS.items():
        if cn_key in df.columns:
            result[en_key] = row[cn_key]
    return result


def _fetch_valuation(ak, symbol: str) -> dict:
    """Fetch stock_hk_valuation_comparison_em and return a normalised dict for the target ticker."""
    try:
        df = ak.stock_hk_valuation_comparison_em(symbol=symbol)
    except Exception as exc:
        _log.debug("valuation_comparison_em failed for %s: %s", symbol, exc)
        return {}

    if df is None or df.empty:
        return {}

    try:
        row = df.iloc[0]
    except IndexError:
        return {}

    result = {}
    for cn_key, en_key in HK_VALUATION_COLS.items():
        if cn_key in df.columns:
            result[en_key] = row[cn_key]
    return result


def _extract_market_cap(indicator: dict) -> float | None:
    """Extract market cap from indicator dict, handling unit conversion."""
    raw = indicator.get("market_cap_raw") or indicator.get("hk_market_cap_raw")
    val = _parse_float(raw)
    if val is None:
        return None
    # If value already looks like full HKD (>1e10 = 100 billion HKD), skip scaling
    if val > 1e10:
        return val
    return val * _YIYI
