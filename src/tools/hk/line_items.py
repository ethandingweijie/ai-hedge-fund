"""
HK financial statement line items via AKShare stock_financial_hk_report_em.

Returns list[LineItem] — same type as the US path.

AKShare orientation
-------------------
stock_financial_hk_report_em returns a WIDE DataFrame:
  rows  = financial line item names (Chinese)
  cols  = reporting period dates

We transpose it so rows = periods, cols = line items, then rename cols to English.

Cross-statement derived fields are computed after merging the three statements.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.data.models import LineItem
from src.tools.hk._utils import _parse_float, _safe_div, _safe_sub, _safe_add
from src.tools.hk.mappings import HK_INCOME_COLS, HK_BALANCE_COLS, HK_CASHFLOW_COLS
from src.tools.hk.currency import get_reporting_currency
from src.tools.hk.ticker import to_akshare_code, to_canonical

_log = logging.getLogger(__name__)

# Which report_type to fetch for each requested line item name
_INCOME_FIELDS = frozenset({
    "revenue", "cost_of_goods_sold", "gross_profit", "operating_income",
    "ebit", "net_income", "earnings_per_share", "research_and_development",
    "selling_expense", "general_and_administrative_expense", "interest_expense",
    "depreciation_and_amortization",
    # derived from income
    "gross_margin", "operating_margin", "ebitda",
})

_BALANCE_FIELDS = frozenset({
    "cash_and_equivalents", "accounts_receivable", "inventory",
    "current_assets", "non_current_assets", "total_assets",
    "short_term_debt", "current_liabilities", "long_term_debt",
    "non_current_liabilities", "total_liabilities", "shareholders_equity",
    "goodwill", "intangible_assets",
    # derived from balance
    "total_debt", "net_debt", "working_capital",
    "goodwill_and_intangible_assets", "current_ratio", "debt_to_equity",
    "book_value_per_share",
})

_CASHFLOW_FIELDS = frozenset({
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "capital_expenditure_raw", "stock_based_compensation",
    # derived from cashflow
    "capital_expenditure", "free_cash_flow",
})


def search_hk_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """
    Fetch financial statement line items for an HK-listed stock.

    Only the report_types needed to satisfy the requested `line_items` are
    fetched (lazy loading).

    Parameters
    ----------
    ticker     : any valid HK ticker format
    line_items : field names in Financial Datasets / system convention
    end_date   : "YYYY-MM-DD" — only include periods on or before this date
    period     : "ttm" | "annual" | "quarterly"
    limit      : max number of periods to return

    Returns
    -------
    list[LineItem] — empty on error
    """
    try:
        import akshare as ak
    except ImportError:
        _log.error("akshare not installed")
        return []

    symbol = to_akshare_code(ticker)
    canonical = to_canonical(ticker)
    requested = set(line_items)

    # Determine which statements are needed
    need_income = bool(requested & _INCOME_FIELDS)
    need_balance = bool(requested & _BALANCE_FIELDS)
    need_cashflow = bool(requested & _CASHFLOW_FIELDS)

    # Also fetch all three if any derived cross-statement field is requested
    cross = {"net_debt", "ebitda", "free_cash_flow"}
    if requested & cross:
        need_income = True
        need_balance = True
        need_cashflow = True

    # Fetch all required statements in parallel — each is an independent HTTP call
    # to EastMoney (~0.6 s each). Sequential → ~1.7 s; parallel → ~0.6 s.
    _to_fetch = {
        name: (ak, symbol, name, period)
        for name, needed in [("income", need_income), ("balance", need_balance), ("cashflow", need_cashflow)]
        if needed
    }
    _results: dict[str, Any] = {}
    if _to_fetch:
        with ThreadPoolExecutor(max_workers=len(_to_fetch)) as _pool:
            _futures = {
                _pool.submit(_fetch_statement, *args): name
                for name, args in _to_fetch.items()
            }
            for _fut in as_completed(_futures):
                _results[_futures[_fut]] = _fut.result()

    income_df   = _results.get("income")
    balance_df  = _results.get("balance")
    cashflow_df = _results.get("cashflow")

    # Parse each into {period: {field: value}} dicts
    income_data   = _parse_statement(income_df,   HK_INCOME_COLS)   if income_df   is not None else {}
    balance_data  = _parse_statement(balance_df,  HK_BALANCE_COLS)  if balance_df  is not None else {}
    cashflow_data = _parse_statement(cashflow_df, HK_CASHFLOW_COLS) if cashflow_df is not None else {}

    # ── Fetch per-period shares_outstanding ─────────────────────────────────
    # Primary source: AKShare stock_hk_financial_indicator_em → 已发行股本(股)
    #   This is the ISSUED share capital (H-shares actually in issue), which is
    #   the correct denominator for per-share calculations.
    #
    # Secondary source: yfinance get_shares_full()
    #   yfinance sometimes inflates the count for recently-IPO'd mainland China
    #   companies by including unvested employee options, warrants, and pre-IPO
    #   convertible preference shares that are NOT yet issued H-shares.  We keep
    #   yfinance as a fallback but sanity-check it against the AKShare value:
    #   if yfinance > 2× AKShare we discard the yfinance figure.
    _shares_by_period: dict[str, float] = {}   # report_period → shares
    _shares_fallback: float | None = None

    # Step 1 — AKShare indicator (most accurate for HK H-share count)
    _akshare_shares: float | None = None
    try:
        _ind_df = ak.stock_hk_financial_indicator_em(symbol=symbol)
        if _ind_df is not None and not _ind_df.empty:
            _ind_raw: dict = dict(zip(_ind_df.columns, _ind_df.iloc[0]))
            _akshare_shares = _parse_float(_ind_raw.get("已发行股本(股)"))
            if _akshare_shares and _akshare_shares > 0:
                _shares_fallback = _akshare_shares
                _log.debug("shares from AKShare indicator for %s: %g", symbol, _akshare_shares)
    except Exception as _exc:
        _log.debug("AKShare indicator shares fetch failed for %s: %s", symbol, _exc)

    # Step 2 — yfinance (fills gaps; cross-checked against AKShare)
    try:
        import yfinance as _yf
        from src.tools.hk.ticker import to_yfinance_code as _to_yf
        _yf_sym = _to_yf(symbol)
        _yf_tk  = _yf.Ticker(_yf_sym)

        # Try full history first (per-period matching)
        _sh_series = _yf_tk.get_shares_full(start="2010-01-01", end=end_date)
        if _sh_series is not None and not _sh_series.empty:
            _sh_dates = [str(d)[:10] for d in _sh_series.index]
            _sh_vals  = list(_sh_series.values)
            for _rp in (set(income_data) | set(balance_data) | set(cashflow_data)):
                _candidates = [(d, v) for d, v in zip(_sh_dates, _sh_vals) if d <= _rp]
                if _candidates:
                    _yf_rp_shares = float(_candidates[-1][1])
                    # Sanity-check: if yfinance is >2× AKShare, it likely includes
                    # unissued options/warrants — use AKShare value instead.
                    if _akshare_shares and _yf_rp_shares > _akshare_shares * 2:
                        _log.info(
                            "shares sanity override for %s @ %s: yfinance %g → AKShare %g",
                            symbol, _rp, _yf_rp_shares, _akshare_shares,
                        )
                        _shares_by_period[_rp] = _akshare_shares
                    else:
                        _shares_by_period[_rp] = _yf_rp_shares
        else:
            # History unavailable — use .info["sharesOutstanding"]
            _info = _yf_tk.info or {}
            _yf_fallback = _parse_float(
                _info.get("sharesOutstanding") or _info.get("impliedSharesOutstanding")
            )
            if _yf_fallback and _yf_fallback > 0:
                # Prefer AKShare if available and yfinance is inflated
                if _akshare_shares and _yf_fallback > _akshare_shares * 2:
                    _log.info(
                        "shares fallback sanity override for %s: yfinance %g → AKShare %g",
                        symbol, _yf_fallback, _akshare_shares,
                    )
                    # keep _shares_fallback = _akshare_shares (already set)
                elif not _shares_fallback:
                    _shares_fallback = _yf_fallback
    except Exception as _exc:
        _log.debug("yfinance shares fetch failed for %s: %s", symbol, _exc)

    # ── yfinance R&D fallback ──────────────────────────────────────────────
    # AKShare HK income statement does not break out research_and_development.
    # yfinance's .financials DataFrame has "Research Development" for many
    # HK-listed companies (sourced from Yahoo Finance "Financials" tab).
    # We fetch it once and inject into income_data periods that lack it.
    _rd_by_period: dict[str, float] = {}
    if need_income and "research_and_development" in requested:
        try:
            import yfinance as _yf_rd
            from src.tools.hk.ticker import to_yfinance_code as _to_yf_rd
            _yf_rd_sym = _to_yf_rd(symbol)
            _yf_rd_tk  = _yf_rd.Ticker(_yf_rd_sym)
            _rd_df = _yf_rd_tk.financials  # annual income statement
            if _rd_df is not None and not _rd_df.empty:
                _rd_row_name = None
                for _candidate in ("Research Development", "Research And Development", "ResearchAndDevelopment"):
                    if _candidate in _rd_df.index:
                        _rd_row_name = _candidate
                        break
                if _rd_row_name:
                    _rd_series = _rd_df.loc[_rd_row_name]
                    for _col_ts, _val in _rd_series.items():
                        _prd = str(_col_ts)[:10]
                        _rd_val = _parse_float(_val)
                        if _rd_val is not None and _rd_val > 0:
                            _rd_by_period[_prd] = _rd_val
                    if _rd_by_period:
                        _log.debug("yfinance R&D for %s: %s", symbol,
                                   {k: f"{v/1e6:.0f}M" for k, v in _rd_by_period.items()})
        except Exception as _exc:
            _log.debug("yfinance R&D fetch failed for %s: %s", symbol, _exc)

    # ── yfinance SBC fallback ─────────────────────────────────────────────
    # AKShare HK cashflow statement does not report stock_based_compensation.
    # yfinance .cashflow has "Stock Based Compensation" for many HK-listed cos.
    # Used by SBC Dilution Override in dcf_agent.py (SBC/Rev > 20% → P/E -15%).
    _sbc_by_period: dict[str, float] = {}
    if need_cashflow and "stock_based_compensation" in requested:
        try:
            import yfinance as _yf_sbc
            from src.tools.hk.ticker import to_yfinance_code as _to_yf_sbc
            _yf_sbc_sym = _to_yf_sbc(symbol)
            _yf_sbc_tk  = _yf_sbc.Ticker(_yf_sbc_sym)
            _sbc_df = _yf_sbc_tk.cashflow
            if _sbc_df is not None and not _sbc_df.empty:
                _sbc_row_name = None
                for _candidate in ("Stock Based Compensation", "StockBasedCompensation"):
                    if _candidate in _sbc_df.index:
                        _sbc_row_name = _candidate
                        break
                if _sbc_row_name:
                    _sbc_series = _sbc_df.loc[_sbc_row_name]
                    for _col_ts, _val in _sbc_series.items():
                        _prd = str(_col_ts)[:10]
                        _sbc_val = _parse_float(_val)
                        if _sbc_val is not None and _sbc_val > 0:
                            _sbc_by_period[_prd] = _sbc_val
                    if _sbc_by_period:
                        _log.debug("yfinance SBC for %s: %s", symbol,
                                   {k: f"{v/1e6:.0f}M" for k, v in _sbc_by_period.items()})
        except Exception as _exc:
            _log.debug("yfinance SBC fetch failed for %s: %s", symbol, _exc)

    # Union of all periods across statements
    all_periods = sorted(
        set(income_data) | set(balance_data) | set(cashflow_data),
        reverse=True,
    )

    # Filter by end_date and period type
    filtered_periods = _filter_periods(all_periods, end_date, period)

    result: list[LineItem] = []
    for rp in filtered_periods[:limit]:
        staging: dict[str, Any] = {}
        staging.update(income_data.get(rp, {}))
        staging.update(balance_data.get(rp, {}))
        staging.update(cashflow_data.get(rp, {}))

        # Inject yfinance SBC if AKShare didn't provide it
        if "stock_based_compensation" not in staging and _sbc_by_period:
            _sbc_v = _sbc_by_period.get(rp)
            if _sbc_v is None:
                _prior_sbc = [v for k, v in sorted(_sbc_by_period.items(), reverse=True) if k <= rp]
                _sbc_v = _prior_sbc[0] if _prior_sbc else None
            if _sbc_v is not None:
                staging["stock_based_compensation"] = _sbc_v

        # Inject yfinance R&D if AKShare didn't provide it
        if "research_and_development" not in staging and _rd_by_period:
            # Exact period match first, then closest prior period
            _rd_val = _rd_by_period.get(rp)
            if _rd_val is None:
                _prior = [v for k, v in sorted(_rd_by_period.items(), reverse=True) if k <= rp]
                _rd_val = _prior[0] if _prior else None
            if _rd_val is not None:
                staging["research_and_development"] = _rd_val

        # Inject historically matched shares_outstanding for this period
        if "shares_outstanding" not in staging:
            _so = _shares_by_period.get(rp) or _shares_fallback
            if _so:
                staging["shares_outstanding"] = _so

        # Alias shareholders_equity → total_equity for DCF compatibility
        if "shareholders_equity" in staging and "total_equity" not in staging:
            staging["total_equity"] = staging["shareholders_equity"]

        # Compute derived fields
        _compute_derived(staging)

        # Build LineItem — only pass requested fields (+ mandatory ones)
        li_fields: dict[str, Any] = {
            "ticker": canonical,
            "report_period": rp,
            "period": period,
            "currency": get_reporting_currency(symbol),
        }
        for field in requested:
            if field in staging:
                li_fields[field] = staging[field]

        result.append(LineItem(**li_fields))

    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Map internal statement type names → (Chinese symbol name, AKShare param)
_STMT_SYMBOL: dict[str, str] = {
    "income":   "利润表",
    "balance":  "资产负债表",
    "cashflow": "现金流量表",
}
_PERIOD_INDICATOR: dict[str, str] = {
    "annual":    "年度",
    "ttm":       "年度",   # HK reports are annual/semi-annual; use annual for ttm
    "quarterly": "季度",
}


def _fetch_statement(ak, stock: str, report_type: str, period_type: str = "annual"):
    """
    Call AKShare stock_financial_hk_report_em and return the raw long DataFrame,
    or None on failure.

    Parameters
    ----------
    stock       : 5-digit AKShare code, e.g. "00700"
    report_type : "income" | "balance" | "cashflow"
    period_type : "annual" | "ttm" | "quarterly"
    """
    symbol_cn = _STMT_SYMBOL.get(report_type)
    indicator  = _PERIOD_INDICATOR.get(period_type, "年度")
    if symbol_cn is None:
        _log.debug("Unknown report_type: %s", report_type)
        return None
    try:
        df = ak.stock_financial_hk_report_em(stock=stock, symbol=symbol_cn, indicator=indicator)
        if df is None or df.empty:
            return None
        return df
    except Exception as exc:
        _log.debug("stock_financial_hk_report_em(%s, %s) failed: %s", stock, symbol_cn, exc)
        return None


def _parse_statement(df, col_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    """
    Parse a long-format AKShare financial DataFrame into {period: {field: value}}.

    AKShare long format (stock_financial_hk_report_em):
      Each row = one (period, line_item) pair.
      Relevant columns: REPORT_DATE, STD_ITEM_NAME, AMOUNT

    For fields where two STD_ITEM_NAME values map to the same English field
    (e.g. both 除税后溢利 and 股东应占溢利 → net_income*), the first value
    encountered is kept and later duplicates are ignored.  The col_map is
    ordered so that the preferred (more specific) name appears first.
    """
    try:
        result: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            # Period: REPORT_DATE is a Timestamp or string; truncate to YYYY-MM-DD
            period_str = str(row.get("REPORT_DATE", ""))[:10].strip()
            if not period_str or period_str in ("None", "nan", "NaT"):
                continue

            item_cn = str(row.get("STD_ITEM_NAME", "")).strip()
            field   = col_map.get(item_cn)
            if field is None:
                continue   # unmapped item — skip

            amount = row.get("AMOUNT")
            parsed = _parse_float(amount)
            if parsed is None:
                continue

            if period_str not in result:
                result[period_str] = {}

            # First value wins — preserves priority order of col_map entries
            if field not in result[period_str]:
                result[period_str][field] = parsed

        return result
    except Exception as exc:
        _log.debug("_parse_statement failed: %s", exc)
        return {}


def _normalise_period(s: str) -> str:
    """Attempt to normalise a period string to YYYY-MM-DD."""
    s = s.strip()
    # Already YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # YYYYMMDD
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    # YYYY/MM/DD
    if len(s) == 10 and s[4] == "/" and s[7] == "/":
        return s.replace("/", "-")
    return s


def _filter_periods(periods: list[str], end_date: str, period_type: str) -> list[str]:
    """Filter and optionally reduce periods list based on period_type."""
    filtered = [p for p in periods if p <= end_date]

    if period_type == "annual":
        # Keep one period per calendar year — the latest one in that year.
        # This handles any fiscal year-end (Dec, Mar, Jun, Sep, etc.).
        # e.g. Tencent: 2024-12-31; Alibaba HK: 2025-03-31
        by_year: dict[str, str] = {}
        for p in filtered:
            year = p[:4]
            if year not in by_year or p > by_year[year]:
                by_year[year] = p
        filtered = sorted(by_year.values(), reverse=True)  # most-recent first
    elif period_type == "ttm":
        # Return all available (AKShare HK reports are typically semi-annual or annual)
        pass
    # "quarterly" — return all

    return filtered


def _compute_derived(s: dict[str, Any]) -> None:
    """Compute derived fields in-place on the staging dict."""

    # ── net_income fallback ───────────────────────────────────────────────────
    # 股东应占溢利 (attributable) is preferred; fall back to 除税后溢利 (total)
    if "net_income" not in s and "net_income_total" in s:
        s["net_income"] = s["net_income_total"]

    # ── revenue fallback ──────────────────────────────────────────────────────
    # 营业额 (primary turnover) is preferred; fall back to 营运收入 (total incl. other)
    if "revenue" not in s and "revenue_total" in s:
        s["revenue"] = s["revenue_total"]

    # ── capital_expenditure (sum PP&E + intangibles outflows, negate to convention) ──
    # capex_ppe_raw and capex_intangibles_raw are already negative (outflows) in
    # AKShare data; negate the sum so capital_expenditure is negative as per convention.
    ppe_raw  = s.get("capex_ppe_raw")
    inta_raw = s.get("capex_intangibles_raw")
    if ppe_raw is not None or inta_raw is not None:
        total_raw = (ppe_raw or 0.0) + (inta_raw or 0.0)
        s["capital_expenditure"] = -abs(total_raw)
    elif s.get("capital_expenditure_raw") is not None:
        # legacy fallback
        s["capital_expenditure"] = -abs(s["capital_expenditure_raw"])

    # ── total_debt ────────────────────────────────────────────────────────────
    st_debt = s.get("short_term_debt") or 0.0
    lt_debt = s.get("long_term_debt") or 0.0
    if st_debt or lt_debt:
        s["total_debt"] = st_debt + lt_debt

    # ── working_capital ───────────────────────────────────────────────────────
    ca = s.get("current_assets")
    cl = s.get("current_liabilities")
    if ca is not None and cl is not None:
        s["working_capital"] = ca - cl

    # ── net_debt ──────────────────────────────────────────────────────────────
    total_debt = s.get("total_debt")
    cash = s.get("cash_and_equivalents")
    if total_debt is not None and cash is not None:
        s["net_debt"] = total_debt - cash

    # ── free_cash_flow ────────────────────────────────────────────────────────
    ocf = s.get("operating_cash_flow")
    capex = s.get("capital_expenditure")   # already negative
    if ocf is not None and capex is not None:
        s["free_cash_flow"] = ocf + capex  # e.g. 5000 + (-1000) = 4000

    # ── ebitda ────────────────────────────────────────────────────────────────
    oi = s.get("operating_income")
    da = s.get("depreciation_and_amortization")
    if oi is not None and da is not None:
        s["ebitda"] = oi + da

    # ── gross_margin ──────────────────────────────────────────────────────────
    gp = s.get("gross_profit")
    rev = s.get("revenue")
    if gp is not None and rev:
        s["gross_margin"] = gp / rev

    # ── operating_margin ──────────────────────────────────────────────────────
    if oi is not None and rev:
        s["operating_margin"] = oi / rev

    # ── goodwill_and_intangible_assets ────────────────────────────────────────
    gw = s.get("goodwill") or 0.0
    ia = s.get("intangible_assets") or 0.0
    if gw or ia:
        s["goodwill_and_intangible_assets"] = gw + ia

    # ── current_ratio ─────────────────────────────────────────────────────────
    if ca is not None and cl:
        s["current_ratio"] = ca / cl

    # ── debt_to_equity ────────────────────────────────────────────────────────
    equity = s.get("shareholders_equity")
    total_debt = s.get("total_debt")
    if total_debt is not None and equity:
        s["debt_to_equity"] = total_debt / equity
