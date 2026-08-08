"""
src/research_ideas/hundred_q/data_fetch.py
=============================================
Pulls everything the Phase-0 quant scorer needs into one HundredQBundle,
reading through the shared Knowledge Graph cache first so a weekly full
sweep over the pilot universe doesn't hammer FMP with duplicate calls
already served by the live pipeline / VGPM screener.

All fetches go through src/tools/api.py, which already throttles every
FMP call via a shared process-wide token bucket (_fmp_acquire) — no need
for extra manual sleeps here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.backend.services.knowledge_graph import (
    get_ttm_metrics_cached,
    set_ttm_metrics,
)
from src.tools.api import (
    get_adv,
    get_earnings_surprises,
    get_financial_metrics,
    get_financial_scores,
    get_insider_trades_edgar,
    get_short_interest,
    search_line_items,
)

# Broader than knowledge_graph.py's own _ANNUAL_LINE_ITEM_FIELDS (that list is
# scoped to the live pipeline's narrower VGPM needs) — Pillar 1/2 questions
# need the fuller income/balance/cash-flow set, so this module calls
# search_line_items directly rather than routing through
# get_kg_annual_line_items().
_ANNUAL_FIELDS = [
    "revenue", "gross_profit", "operating_income", "net_income", "ebitda",
    "research_and_development", "interest_expense",
    "total_assets", "total_liabilities", "total_debt", "net_debt",
    "current_assets", "current_liabilities",
    "accounts_receivable", "accounts_payable", "cash_and_equivalents",
    "shareholders_equity", "goodwill", "intangible_assets",
    "capital_expenditure", "operating_cash_flow", "free_cash_flow",
    "stock_based_compensation", "share_buyback", "dividends_and_distributions",
    "shares_outstanding",
]


@dataclass
class HundredQBundle:
    ticker: str
    name: str = ""
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None

    # ── TTM ratios (FinancialMetrics) ───────────────────────────────────────
    return_on_equity: Optional[float] = None
    return_on_assets: Optional[float] = None
    return_on_invested_capital: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None
    debt_to_equity: Optional[float] = None
    payout_ratio: Optional[float] = None
    asset_turnover: Optional[float] = None
    inventory_turnover: Optional[float] = None
    price_to_earnings_ratio: Optional[float] = None
    price_to_book_ratio: Optional[float] = None
    price_to_sales_ratio: Optional[float] = None
    enterprise_value_to_ebitda_ratio: Optional[float] = None
    enterprise_value_to_revenue_ratio: Optional[float] = None
    free_cash_flow_yield: Optional[float] = None
    peg_ratio: Optional[float] = None
    enterprise_value: Optional[float] = None

    # ── 5yr annual ratio series (all free — same fm_annual call as PE) ──────
    annual_pe_series: list[Optional[float]] = field(default_factory=list)
    annual_gross_margin_series: list[Optional[float]] = field(default_factory=list)
    annual_operating_margin_series: list[Optional[float]] = field(default_factory=list)
    annual_asset_turnover_series: list[Optional[float]] = field(default_factory=list)
    annual_inventory_turnover_series: list[Optional[float]] = field(default_factory=list)

    # ── Annual line-item series, oldest -> newest (up to 5-6 FYs) ───────────
    annual_periods: list[str] = field(default_factory=list)
    revenue_series: list[Optional[float]] = field(default_factory=list)
    gross_profit_series: list[Optional[float]] = field(default_factory=list)
    operating_income_series: list[Optional[float]] = field(default_factory=list)
    net_income_series: list[Optional[float]] = field(default_factory=list)
    ebitda_series: list[Optional[float]] = field(default_factory=list)
    rd_series: list[Optional[float]] = field(default_factory=list)
    capex_series: list[Optional[float]] = field(default_factory=list)
    ocf_series: list[Optional[float]] = field(default_factory=list)
    fcf_series: list[Optional[float]] = field(default_factory=list)
    sbc_series: list[Optional[float]] = field(default_factory=list)
    buyback_series: list[Optional[float]] = field(default_factory=list)
    dividends_series: list[Optional[float]] = field(default_factory=list)
    ar_series: list[Optional[float]] = field(default_factory=list)
    ap_series: list[Optional[float]] = field(default_factory=list)
    shares_outstanding_series: list[Optional[float]] = field(default_factory=list)
    net_debt_series: list[Optional[float]] = field(default_factory=list)

    # ── Latest-FY point values (convenience — last element of the series) ───
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_debt: Optional[float] = None
    net_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    shareholders_equity: Optional[float] = None
    goodwill: Optional[float] = None
    intangible_assets: Optional[float] = None
    interest_expense: Optional[float] = None
    ebitda_latest: Optional[float] = None

    # ── Financial scores ─────────────────────────────────────────────────
    altman_z: Optional[float] = None
    piotroski: Optional[int] = None

    # ── Insider activity (SEC EDGAR, free) ──────────────────────────────────
    insider_ad_ratio_12mo: Optional[float] = None   # acquired / (acquired+disposed) shares

    # ── Earnings surprises ──────────────────────────────────────────────────
    guidance_beats_last8: Optional[int] = None
    guidance_quarters_reported: Optional[int] = None

    # ── Short interest / liquidity ──────────────────────────────────────────
    short_percent_float: Optional[float] = None
    avg_dollar_volume: Optional[float] = None

    error: Optional[str] = None


def _fetch_insider_ad_ratio_12mo(ticker: str) -> Optional[float]:
    """
    Acquired/(acquired+disposed) share ratio over the trailing 12 months,
    via free SEC EDGAR Form 4 data (same source + sign convention as
    src/triggers/detectors.py::fresh_form4 — positive transaction_shares
    is treated as an open-market buy elsewhere in this codebase).
    """
    today = date.today()
    start = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    try:
        trades = get_insider_trades_edgar(ticker, start, end)
    except Exception:
        return None
    if not trades:
        return None
    acquired = 0.0
    disposed = 0.0
    for t in trades:
        shares = t.transaction_shares or 0
        if shares > 0:
            acquired += shares
        elif shares < 0:
            disposed += abs(shares)
    total = acquired + disposed
    if total == 0:
        return None
    return acquired / total


def _series(items: list, fld: str) -> list[Optional[float]]:
    return [getattr(item, fld, None) for item in items]


def fetch_ticker_bundle(ticker: str, meta: dict) -> Optional[HundredQBundle]:
    """Pull everything the Phase-0 quant scorer needs for one ticker."""
    ticker = ticker.upper()
    bundle = HundredQBundle(
        ticker=ticker,
        name=meta.get("name", ticker),
        sector=meta.get("sector"),
        industry=meta.get("industry"),
    )
    today = date.today().isoformat()

    # ── TTM ratios — read through the Knowledge Graph first ────────────────
    kg_cached = get_ttm_metrics_cached([ticker]).get(ticker)
    if kg_cached:
        ttm = kg_cached
    else:
        try:
            fm_list = get_financial_metrics(ticker, end_date=today, period="ttm", limit=1)
        except Exception as exc:
            bundle.error = f"get_financial_metrics(ttm) failed: {exc}"
            return bundle
        if not fm_list:
            bundle.error = "no ttm financial metrics"
            return bundle
        ttm = fm_list[0].model_dump()
        try:
            set_ttm_metrics({ticker: ttm}, source="hundred_q")
        except Exception:
            pass

    bundle.market_cap = ttm.get("market_cap")
    bundle.enterprise_value = ttm.get("enterprise_value")
    bundle.return_on_equity = ttm.get("return_on_equity")
    bundle.return_on_assets = ttm.get("return_on_assets")
    bundle.return_on_invested_capital = ttm.get("return_on_invested_capital")
    bundle.gross_margin = ttm.get("gross_margin")
    bundle.operating_margin = ttm.get("operating_margin")
    bundle.net_margin = ttm.get("net_margin")
    bundle.current_ratio = ttm.get("current_ratio")
    bundle.quick_ratio = ttm.get("quick_ratio")
    bundle.interest_coverage = ttm.get("interest_coverage")
    bundle.debt_to_equity = ttm.get("debt_to_equity")
    bundle.payout_ratio = ttm.get("payout_ratio")
    bundle.asset_turnover = ttm.get("asset_turnover")
    bundle.inventory_turnover = ttm.get("inventory_turnover")
    bundle.price_to_earnings_ratio = ttm.get("price_to_earnings_ratio")
    bundle.price_to_book_ratio = ttm.get("price_to_book_ratio")
    bundle.price_to_sales_ratio = ttm.get("price_to_sales_ratio")
    bundle.enterprise_value_to_ebitda_ratio = ttm.get("enterprise_value_to_ebitda_ratio")
    bundle.enterprise_value_to_revenue_ratio = ttm.get("enterprise_value_to_revenue_ratio")
    bundle.free_cash_flow_yield = ttm.get("free_cash_flow_yield")
    bundle.peg_ratio = ttm.get("peg_ratio")

    # ── 5yr annual P/E history (for "current PE vs 5yr avg") ───────────────
    try:
        fm_annual = get_financial_metrics(ticker, end_date=today, period="annual", limit=6)
        fm_annual = sorted(fm_annual, key=lambda m: m.report_period)
        bundle.annual_pe_series = [m.price_to_earnings_ratio for m in fm_annual]
        bundle.annual_gross_margin_series = [m.gross_margin for m in fm_annual]
        bundle.annual_operating_margin_series = [m.operating_margin for m in fm_annual]
        bundle.annual_asset_turnover_series = [m.asset_turnover for m in fm_annual]
        bundle.annual_inventory_turnover_series = [m.inventory_turnover for m in fm_annual]
    except Exception:
        bundle.annual_pe_series = []

    # ── Annual line items (5-6 FYs, income/balance/cash-flow) ──────────────
    try:
        items = search_line_items(ticker, _ANNUAL_FIELDS, end_date=today, period="annual", limit=6)
        items = sorted(items, key=lambda li: li.report_period)
    except Exception:
        items = []

    if items:
        bundle.annual_periods = [li.report_period for li in items]
        bundle.revenue_series = _series(items, "revenue")
        bundle.gross_profit_series = _series(items, "gross_profit")
        bundle.operating_income_series = _series(items, "operating_income")
        bundle.net_income_series = _series(items, "net_income")
        bundle.ebitda_series = _series(items, "ebitda")
        bundle.rd_series = _series(items, "research_and_development")
        bundle.capex_series = _series(items, "capital_expenditure")
        bundle.ocf_series = _series(items, "operating_cash_flow")
        bundle.fcf_series = _series(items, "free_cash_flow")
        bundle.sbc_series = _series(items, "stock_based_compensation")
        bundle.buyback_series = _series(items, "share_buyback")
        bundle.dividends_series = _series(items, "dividends_and_distributions")
        bundle.ar_series = _series(items, "accounts_receivable")
        bundle.ap_series = _series(items, "accounts_payable")
        bundle.shares_outstanding_series = _series(items, "shares_outstanding")
        bundle.net_debt_series = _series(items, "net_debt")

        latest = items[-1]
        bundle.total_assets = getattr(latest, "total_assets", None)
        bundle.total_liabilities = getattr(latest, "total_liabilities", None)
        bundle.total_debt = getattr(latest, "total_debt", None)
        bundle.net_debt = getattr(latest, "net_debt", None)
        bundle.cash_and_equivalents = getattr(latest, "cash_and_equivalents", None)
        bundle.shareholders_equity = getattr(latest, "shareholders_equity", None)
        bundle.goodwill = getattr(latest, "goodwill", None)
        bundle.intangible_assets = getattr(latest, "intangible_assets", None)
        bundle.interest_expense = getattr(latest, "interest_expense", None)
        bundle.ebitda_latest = getattr(latest, "ebitda", None)

    # ── Financial scores (Altman Z, Piotroski) ──────────────────────────────
    try:
        fs = get_financial_scores(ticker)
        if fs:
            bundle.altman_z = fs.get("altmanZScore")
            pio = fs.get("piotroskiScore")
            bundle.piotroski = int(pio) if pio is not None else None
    except Exception:
        pass

    # ── Insider A/D ratio (free EDGAR) ──────────────────────────────────────
    try:
        bundle.insider_ad_ratio_12mo = _fetch_insider_ad_ratio_12mo(ticker)
    except Exception:
        bundle.insider_ad_ratio_12mo = None

    # ── Earnings-surprise / guidance-beat streak ────────────────────────────
    try:
        surprises = get_earnings_surprises(ticker, end_date=today, limit=8)
        bundle.guidance_quarters_reported = len(surprises)
        bundle.guidance_beats_last8 = sum(1 for s in surprises if s.get("beat"))
    except Exception:
        pass

    # ── Short interest ───────────────────────────────────────────────────────
    try:
        si = get_short_interest(ticker)
        if si:
            bundle.short_percent_float = si[0].get("short_percent")
    except Exception:
        pass

    # ── Trading liquidity (avg $ volume, 30d) ───────────────────────────────
    try:
        adv = get_adv(ticker, days=30)
        bundle.avg_dollar_volume = adv.get("adv_dollars")
        if not bundle.price:
            bundle.price = adv.get("last_price")
    except Exception:
        pass

    return bundle
