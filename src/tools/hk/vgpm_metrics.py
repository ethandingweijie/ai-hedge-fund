"""
src/tools/hk/vgpm_metrics.py
============================
Multi-source VGPM raw metric fetcher — HK (HKEX) tickers only.

Sources (priority order — later sources only fill None gaps):
  1. AKShare          → P/E, P/B, Div Yield, ROE, ROA, Net Margin, Rev YoY, EPS Growth
  2. yfinance         → EV/EBITDA, EV/Sales, FCF Yield/Growth/Conversion, Gross Margin,
                         Net Inc Growth, Asset Turnover, Fwd P/E, PEG, Rev CAGR 3Y,
                         Price 1Y/6M/3M, ROIC (computed), Piotroski (computed), Short Ratio
  3. Alpha Spread     → ROIC, EV/EBITDA, Gross Margin, FCF data, analyst upgrades,
                         Fwd estimates  (alphaspread.com — free, no auth)
  4. Stock Analysis   → Supplementary gap-fill  (stockanalysis.com — free, no auth)
  5. FinanceToolkit   → Earnings Surprise, Fwd EPS/Revenue Growth
                         (pip install financetoolkit; uses FMP free tier)

Output schema is identical to _fetch_ticker_metrics() so it plugs directly
into the screener VGPM computation pipeline without any other changes.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

log = logging.getLogger(__name__)

# ── Full output schema (must match _fetch_ticker_metrics return dict) ─────────
_SCHEMA_KEYS = (
    "pe", "pb", "ev_ebitda", "ev_sales", "peg", "fcf_yield", "div_yield", "fwd_pe",
    "rev_growth", "rev_cagr_3y", "eps_growth", "fcf_growth", "net_inc_growth",
    "earnings_surprise", "fwd_eps_growth", "fwd_rev_growth",
    "roe", "roa", "roic", "net_margin", "gross_margin", "fcf_conversion",
    "piotroski", "asset_turnover",
    "price_1y", "price_6m", "price_3m",
    "earnings_revision", "analyst_upgrade", "rec_score", "short_ratio",
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _sf(v) -> Optional[float]:
    """Safe float: coerce to float, return None on NaN / Inf / None."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _fill(base: dict, supplement: dict) -> None:
    """Fill None gaps in *base* from *supplement*. Never overwrites non-None."""
    for k, v in supplement.items():
        if k in _SCHEMA_KEYS and base.get(k) is None and v is not None:
            base[k] = v


def _pct_to_dec(v: Optional[float]) -> Optional[float]:
    """Convert a value that might be in percent (e.g. 15.2) to decimal (0.152)."""
    if v is None:
        return None
    return v / 100 if abs(v) > 1 else v


# ─────────────────────────────────────────────────────────────────────────────
# Source 1 — AKShare
# P/E, P/B, Div Yield, ROE, ROA, Net Margin, Revenue YoY, EPS Growth
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_akshare(ak_code: str) -> dict:
    out: dict = {}
    try:
        import akshare as ak
        from src.tools.hk.financial_metrics import (
            _fetch_indicator, _fetch_growth, _fetch_valuation,
        )
        from src.tools.hk._utils import _parse_float

        ind = _fetch_indicator(ak, ak_code)
        grw = _fetch_growth(ak, ak_code)
        val = _fetch_valuation(ak, ak_code)

        def _get(d: dict, key: str, as_pct: bool = False) -> Optional[float]:
            v = _sf(_parse_float(d.get(key)))
            return _pct_to_dec(v) if as_pct else v

        # Valuation multiples — prefer TTM from valuation endpoint, fall back to indicator
        out["pe"] = _sf(_parse_float(val.get("price_to_earnings_ratio") or ind.get("price_to_earnings_ratio")))
        out["pb"] = _sf(_parse_float(val.get("price_to_book_ratio")     or ind.get("price_to_book_ratio")))
        # P/S TTM → use as ev_sales proxy (P/S ≈ EV/Sales for most non-leveraged HK companies)
        ps = _sf(_parse_float(val.get("price_to_sales_ratio") or ind.get("price_to_sales_ratio")))
        if ps is not None:
            out["ev_sales"] = ps
        # P/Cash Flow TTM → use as FCF yield approximation (1 / P/CF = CF yield)
        pcf = _sf(_parse_float(val.get("price_to_cash_flow_ratio")))
        if pcf is not None and pcf > 0:
            out.setdefault("fcf_yield", (1.0 / pcf) * 100)  # store as %

        # Dividend yield — AKShare returns as % (e.g. 3.2 = 3.2%)
        out["div_yield"]  = _get(ind, "dividend_yield",           as_pct=True)
        out["roe"]        = _get(ind, "return_on_equity",         as_pct=True)
        out["roa"]        = _get(ind, "return_on_assets",         as_pct=True)
        out["net_margin"] = _get(ind, "net_margin",               as_pct=True)
        out["rev_growth"] = _get(grw, "revenue_growth",           as_pct=True)
        out["eps_growth"] = _get(grw, "earnings_per_share_growth", as_pct=True)

        # Market cap: _fetch_indicator maps 总市值(港元) → "market_cap_raw"
        # Use _extract_market_cap which handles both key variants + unit scaling.
        from src.tools.hk.financial_metrics import _extract_market_cap
        mc = _extract_market_cap(ind)
        if mc is not None:
            out["market_cap_hkd"] = mc

    except Exception as exc:
        log.debug("AKShare vgpm fetch failed (%s): %s", ak_code, exc)

    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Source 2 — yfinance
# EV/EBITDA, EV/Sales, FCF Yield/Growth/Conversion, Gross Margin,
# Net Inc Growth, Asset Turnover, Fwd P/E, PEG, Rev CAGR 3Y, Price 1Y/6M/3M,
# ROIC (computed), Piotroski F-score (computed), Short Ratio
# ─────────────────────────────────────────────────────────────────────────────

def _find_row(df, *labels):
    """Return the first DataFrame row whose index label contains any of *labels*."""
    if df is None or df.empty:
        return None
    for label in labels:
        for idx in df.index:
            if label.lower() in str(idx).lower():
                return df.loc[idx]
    return None


def _col(series, pos: int = 0) -> Optional[float]:
    """Safely read position *pos* from a pandas Series."""
    try:
        if series is None:
            return None
        return _sf(float(series.iloc[pos]))
    except Exception:
        return None


def _fetch_yfinance(yf_sym: str) -> dict:
    out: dict = {}
    try:
        import yfinance as yf
        t    = yf.Ticker(yf_sym)
        info = t.info or {}

        # ── From info dict (fast, no extra HTTP calls) ────────────────────
        out["ev_ebitda"]   = _sf(info.get("enterpriseToEbitda"))
        out["ev_sales"]    = _sf(info.get("enterpriseToRevenue"))
        out["fwd_pe"]      = _sf(info.get("forwardPE"))
        out["peg"]         = _sf(info.get("pegRatio"))
        out["gross_margin"]= _sf(info.get("grossMargins"))  # already decimal
        out["short_ratio"] = _sf(info.get("shortRatio"))

        # roe / roa / net_margin / div_yield — yfinance returns decimal, no conversion needed.
        # These fill gaps if AKShare (Source 1) returned None for any of them.
        out["roe"]        = _sf(info.get("returnOnEquity"))
        out["roa"]        = _sf(info.get("returnOnAssets"))
        out["net_margin"] = _sf(info.get("profitMargins"))
        # Use trailingAnnualDividendYield (more reliable for HK stocks than dividendYield spot)
        out["div_yield"]  = _sf(info.get("trailingAnnualDividendYield"))

        # Forward EPS growth: derive from forwardEps vs trailingEps in info.
        # Guard: skip if |trailingEps| < 0.10 — near-zero base causes extreme ratios.
        fwd_eps_info = _sf(info.get("forwardEps"))
        ttm_eps_info = _sf(info.get("trailingEps"))
        if (fwd_eps_info is not None and ttm_eps_info is not None
                and abs(ttm_eps_info) >= 0.10):
            out["fwd_eps_growth"] = (fwd_eps_info - ttm_eps_info) / abs(ttm_eps_info)

        fcf_ann = _sf(info.get("freeCashflow"))
        mc      = _sf(info.get("marketCap"))
        if fcf_ann is not None and mc and mc > 0:
            out["fcf_yield"] = (fcf_ann / mc) * 100   # store as %

        rec = _sf(info.get("recommendationMean"))
        if rec is not None:
            out["rec_score"] = (5.0 - rec) / 4.0      # 1=Strong Buy→1.0, 5=Sell→0.0

        # ── Financial statements ──────────────────────────────────────────
        try:
            fin = t.financials    # annual income statement (cols = periods, newest first)
            cf  = t.cashflow      # annual cash-flow statement
            bs  = t.balance_sheet # annual balance sheet

            # Income statement rows
            rev_s  = _find_row(fin, "Total Revenue", "Revenue")
            ni_s   = _find_row(fin, "Net Income")
            gp_s   = _find_row(fin, "Gross Profit")
            ie_s   = _find_row(fin, "Interest Expense")
            tax_s  = _find_row(fin, "Tax Provision", "Income Tax Expense")

            rev0, rev1, rev3 = _col(rev_s, 0), _col(rev_s, 1), _col(rev_s, 3)
            ni0,  ni1        = _col(ni_s,  0), _col(ni_s,  1)
            gp0,  gp1        = _col(gp_s,  0), _col(gp_s,  1)
            ie0              = _col(ie_s,  0)
            tax0             = _col(tax_s, 0)

            # Net income growth
            if ni0 is not None and ni1 and ni1 != 0:
                out["net_inc_growth"] = (ni0 - ni1) / abs(ni1)

            # Revenue CAGR 3Y  (uses 4th column = 3 years ago)
            if rev0 and rev3 and rev3 > 0:
                out["rev_cagr_3y"] = (rev0 / rev3) ** (1 / 3) - 1

            # Gross margin from statements (fallback if info gave None)
            if out.get("gross_margin") is None and gp0 is not None and rev0 and rev0 != 0:
                out["gross_margin"] = gp0 / rev0

            # Net margin from statements (fallback when profitMargins and AKShare both None)
            if out.get("net_margin") is None and ni0 is not None and rev0 and rev0 != 0:
                out["net_margin"] = ni0 / rev0

            # Cash-flow rows
            ocf_s = _find_row(cf, "Operating Cash Flow", "Cash From Operations",
                               "Net Cash Provided By Operating Activities")
            cap_s = _find_row(cf, "Capital Expenditure", "Capital Expenditures",
                               "Purchase Of Ppe", "Purchase Of Property")
            fcf_s = _find_row(cf, "Free Cash Flow")

            ocf0, ocf1 = _col(ocf_s, 0), _col(ocf_s, 1)
            cap0, cap1 = _col(cap_s, 0), _col(cap_s, 1)
            fcf_direct0 = _col(fcf_s, 0)
            fcf_direct1 = _col(fcf_s, 1)

            # Best FCF = explicit row; fallback = OCF + CapEx (capex is negative)
            fcf0 = fcf_direct0 or ((ocf0 + cap0) if ocf0 is not None and cap0 is not None else None)
            fcf1 = fcf_direct1 or ((ocf1 + cap1) if ocf1 is not None and cap1 is not None else None)

            # FCF yield from statements (more accurate than info dict)
            if out.get("fcf_yield") is None and fcf0 is not None and mc and mc > 0:
                out["fcf_yield"] = (fcf0 / mc) * 100

            if fcf0 is not None and fcf1 and fcf1 != 0:
                out["fcf_growth"] = (fcf0 - fcf1) / abs(fcf1)

            if fcf0 is not None and ni0 and ni0 != 0:
                out["fcf_conversion"] = fcf0 / abs(ni0)

            # Balance-sheet rows
            ta_s   = _find_row(bs, "Total Assets")
            cl_s   = _find_row(bs, "Current Liabilities", "Total Current Liabilities")
            ca_s   = _find_row(bs, "Current Assets",      "Total Current Assets")
            ltd_s  = _find_row(bs, "Long Term Debt",      "Long-Term Debt")
            eq_s   = _find_row(bs, "Stockholders Equity", "Total Equity", "Common Stock Equity")
            cash_s = _find_row(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents",
                                "Cash And Short Term Investments")
            sh_s   = _find_row(bs, "Ordinary Shares Number", "Share Issued",
                                "Common Stock", "Shares Outstanding")

            ta0,  ta1   = _col(ta_s,   0), _col(ta_s,   1)
            cl0,  cl1   = _col(cl_s,   0), _col(cl_s,   1)
            ca0,  ca1   = _col(ca_s,   0), _col(ca_s,   1)
            ltd0, ltd1  = _col(ltd_s,  0), _col(ltd_s,  1)
            eq0,  eq1   = _col(eq_s,   0), _col(eq_s,   1)
            cash0       = _col(cash_s, 0)
            sh0,  sh1   = _col(sh_s,   0), _col(sh_s,   1)

            # Asset turnover = Revenue / Avg Total Assets
            if rev0 and ta0 and ta0 > 0:
                ta_avg = (ta0 + ta1) / 2 if ta1 else ta0
                out["asset_turnover"] = rev0 / ta_avg

            # ROE from statements (fallback when returnOnEquity and AKShare both None)
            if out.get("roe") is None and ni0 is not None and eq0 and eq0 > 0:
                eq_avg = (eq0 + eq1) / 2 if eq1 else eq0
                out["roe"] = ni0 / eq_avg if eq_avg != 0 else None

            # ROA from statements (fallback when returnOnAssets and AKShare both None)
            if out.get("roa") is None and ni0 is not None and ta0 and ta0 > 0:
                ta_avg2 = (ta0 + ta1) / 2 if ta1 else ta0
                out["roa"] = ni0 / ta_avg2

            # ROIC = NOPAT / Invested Capital
            # NOPAT = EBIT × (1 - effective tax rate)
            # Invested Capital = Equity + LT Debt − Cash
            if (ni0 is not None and tax0 is not None
                    and eq0 is not None and ta0 and ta0 > 0):
                ebt  = ni0 + abs(tax0 or 0)
                ebit = ni0 + abs(ie0  or 0) + abs(tax0 or 0)
                t_rate = (abs(tax0) / abs(ebt)) if ebt and ebt != 0 else 0.20
                t_rate = min(max(t_rate, 0.0), 0.50)
                nopat  = ebit * (1 - t_rate)
                ic     = (eq0 or 0) + (ltd0 or 0) - (cash0 or 0)
                if ic > 0:
                    out["roic"] = nopat / ic

            # ── Piotroski F-score (9-point) ───────────────────────────────
            pts, possible = 0, 0

            roa_curr = out.get("roa")
            if roa_curr is None and ni0 is not None and ta0 and ta0 > 0:
                roa_curr = ni0 / ta0
            roa_prev = (ni1 / ta1) if (ni1 is not None and ta1 and ta1 > 0) else None

            # 1. ROA > 0
            if roa_curr is not None:
                possible += 1
                if roa_curr > 0: pts += 1
            # 2. CFO > 0
            if ocf0 is not None:
                possible += 1
                if ocf0 > 0: pts += 1
            # 3. ΔROA > 0
            if roa_curr is not None and roa_prev is not None:
                possible += 1
                if roa_curr > roa_prev: pts += 1
            # 4. Accrual quality: CFO > Net Income
            if ocf0 is not None and ni0 is not None:
                possible += 1
                if ocf0 > ni0: pts += 1
            # 5. ΔLeverage (LTD/TA) decreased
            if (ltd0 is not None and ta0 and ta0 > 0
                    and ltd1 is not None and ta1 and ta1 > 0):
                possible += 1
                if (ltd0 / ta0) < (ltd1 / ta1): pts += 1
            # 6. Current ratio increased
            if (ca0 and cl0 and cl0 > 0 and ca1 and cl1 and cl1 > 0):
                possible += 1
                if (ca0 / cl0) > (ca1 / cl1): pts += 1
            # 7. No new share dilution
            if sh0 is not None and sh1 is not None:
                possible += 1
                if sh0 <= sh1 * 1.01: pts += 1
            # 8. ΔGross Margin > 0
            if rev0 and rev0 > 0 and gp0 is not None and rev1 and rev1 > 0 and gp1 is not None:
                possible += 1
                if (gp0 / rev0) > (gp1 / rev1): pts += 1
            # 9. ΔAsset Turnover > 0
            if rev0 and ta0 and ta0 > 0 and rev1 and ta1 and ta1 > 0:
                possible += 1
                if (rev0 / ta0) > (rev1 / ta1): pts += 1

            if possible >= 5:   # report only when we have enough data
                out["piotroski"] = pts / 9.0

        except Exception as exc:
            log.debug("yfinance statements failed (%s): %s", yf_sym, exc)

        # ── Price momentum from monthly history ───────────────────────────
        try:
            hist = t.history(period="2y", interval="1mo", auto_adjust=True)
            if hist is not None and not hist.empty and len(hist) >= 2:
                curr_p = float(hist["Close"].iloc[-1])
                n      = len(hist)

                def _price_chg(lookback: int) -> Optional[float]:
                    idx = n - lookback - 1
                    if idx < 0:
                        return None
                    p = float(hist["Close"].iloc[idx])
                    return ((curr_p - p) / p * 100) if p > 0 else None

                out["price_1y"] = _price_chg(12)
                out["price_6m"] = _price_chg(6)
                out["price_3m"] = _price_chg(3)
        except Exception as exc:
            log.debug("yfinance history failed (%s): %s", yf_sym, exc)

    except Exception as exc:
        log.debug("yfinance vgpm fetch failed (%s): %s", yf_sym, exc)

    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Source 3 — Alpha Spread  (alphaspread.com — free, no auth required)
# ROIC, EV/EBITDA, EV/Sales, Gross Margin, FCF, analyst upgrades, Fwd estimates
# URL: https://alphaspread.com/security/hkex/{4-digit-code}
# ─────────────────────────────────────────────────────────────────────────────

def _as_code(yf_sym: str) -> str:
    """'0700.HK' → '0700'  (Alpha Spread uses 4-digit HK code)."""
    return yf_sym.upper().replace(".HK", "")


def _fetch_html(url: str, timeout: int = 10) -> Optional[str]:
    try:
        import requests
        r = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout)
        return r.text if r.ok else None
    except Exception as exc:
        log.debug("HTTP GET failed (%s): %s", url, exc)
        return None


def _parse_metric(html: str, label: str, as_pct: bool = False) -> Optional[float]:
    """
    Find the first number that follows *label* within 200 chars of HTML.
    Handles values like '15.2%', '-3.4', '1,234.5', '12.3x'.
    """
    pattern = re.compile(
        re.escape(label) + r".{0,200}?([-]?\d[\d,]*\.?\d*)\s*[%x]?",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return None
    v = _sf(m.group(1).replace(",", ""))
    if v is None:
        return None
    return _pct_to_dec(v) if as_pct else v


def _parse_upgrade_ratio(html: str) -> Optional[float]:
    buys  = len(re.findall(r"\b(buy|outperform|overweight|upgrade|strong buy)\b", html, re.IGNORECASE))
    sells = len(re.findall(r"\b(sell|underperform|underweight|downgrade|strong sell)\b", html, re.IGNORECASE))
    total = buys + sells
    return (buys / total) if total > 0 else None


def _fetch_alpha_spread(yf_sym: str) -> dict:
    out: dict = {}
    code     = _as_code(yf_sym)
    base_url = f"https://alphaspread.com/security/hkex/{code}"

    # ── Main summary page ─────────────────────────────────────────────────
    html = _fetch_html(base_url)
    if html:
        for label, key, pct in [
            ("ROIC",         "roic",        True),
            ("EV/EBITDA",    "ev_ebitda",   False),
            ("EV/Revenue",   "ev_sales",    False),
            ("EV/Sales",     "ev_sales",    False),
            ("Gross Margin", "gross_margin", True),
            ("Forward P/E",  "fwd_pe",      False),
            ("Fwd P/E",      "fwd_pe",      False),
            ("FCF Conversion","fcf_conversion", True),
        ]:
            if out.get(key) is None:
                v = _parse_metric(html, label, as_pct=pct)
                if v is not None:
                    out[key] = v

    # ── Profitability / ROIC sub-page ─────────────────────────────────────
    if out.get("roic") is None:
        html_prof = _fetch_html(f"{base_url}/profitability/")
        if html_prof:
            v = _parse_metric(html_prof, "ROIC", as_pct=True)
            if v is not None:
                out["roic"] = v

    # ── Analyst / forecast sub-page ───────────────────────────────────────
    html_est = _fetch_html(f"{base_url}/forecast/") or _fetch_html(f"{base_url}/analyst-estimates/")
    if html_est:
        # Analyst upgrade ratio
        ratio = _parse_upgrade_ratio(html_est)
        if ratio is not None:
            out["analyst_upgrade"] = ratio

        # Forward EPS growth: look for two consecutive EPS numbers
        eps_matches = re.findall(
            r"(?:EPS|Earnings Per Share)[^0-9\-]{0,60}([-]?\d[\d,]*\.?\d*)",
            html_est, re.IGNORECASE | re.DOTALL,
        )
        eps_vals = [_sf(v.replace(",", "")) for v in eps_matches[:4] if _sf(v.replace(",", "")) is not None]
        if len(eps_vals) >= 2 and eps_vals[1] and eps_vals[1] != 0:
            out["fwd_eps_growth"] = (eps_vals[0] - eps_vals[1]) / abs(eps_vals[1])
            out["earnings_revision"] = out["fwd_eps_growth"]

        # Forward Revenue growth
        rev_matches = re.findall(
            r"(?:Revenue|Sales)[^0-9\-]{0,60}([-]?\d[\d,]*\.?\d*)",
            html_est, re.IGNORECASE | re.DOTALL,
        )
        rev_vals = [_sf(v.replace(",", "")) for v in rev_matches[:4] if _sf(v.replace(",", "")) is not None]
        if len(rev_vals) >= 2 and rev_vals[1] and rev_vals[1] != 0:
            out["fwd_rev_growth"] = (rev_vals[0] - rev_vals[1]) / abs(rev_vals[1])

    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Source 4 — Stock Analysis  (stockanalysis.com — free, no auth)
# EV/EBITDA, EV/FCF, ROIC, Gross Margin, Fwd P/E — supplementary gap-fill
# URL: https://stockanalysis.com/stocks/{code}-hk/
# ─────────────────────────────────────────────────────────────────────────────

def _sa_slug(yf_sym: str) -> str:
    """'0700.HK' → '0700-hk'."""
    return yf_sym.lower().replace(".", "-")


def _fetch_stock_analysis(yf_sym: str) -> dict:
    out: dict = {}
    slug     = _sa_slug(yf_sym)
    base_url = f"https://stockanalysis.com/stocks/{slug}/"

    # ── Main statistics / overview page ───────────────────────────────────
    html = _fetch_html(base_url)
    if html:
        for label, key, pct in [
            ("EV/EBITDA",    "ev_ebitda",    False),
            ("EV/FCF",       "ev_sales",     False),   # closest available proxy
            ("ROIC",         "roic",         True),
            ("Gross Margin", "gross_margin", True),
            ("Forward P/E",  "fwd_pe",       False),
            ("P/E Ratio",    "pe",           False),
            ("P/B Ratio",    "pb",           False),
            ("P/Book",       "pb",           False),
        ]:
            if out.get(key) is None:
                v = _parse_metric(html, label, as_pct=pct)
                if v is not None:
                    out[key] = v

    # ── Financials page — analyst estimates ───────────────────────────────
    html_fin = _fetch_html(f"{base_url}financials/")
    if html_fin:
        # Analyst upgrade ratio from recommendations text
        ratio = _parse_upgrade_ratio(html_fin)
        if ratio is not None and out.get("analyst_upgrade") is None:
            out["analyst_upgrade"] = ratio

    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Source 5 — FinanceToolkit  (pip install financetoolkit)
# Earnings Surprise, Fwd EPS Growth, Fwd Revenue Growth
# Uses FMP as backend (free tier); supports HK tickers.
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_finance_toolkit(yf_sym: str) -> dict:
    out: dict = {}
    try:
        from financetoolkit import Toolkit  # type: ignore
        import os

        api_key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY") or ""
        tk = Toolkit(
            tickers=[yf_sym],
            api_key=api_key,
            start_date="2021-01-01",
            progress_bar=False,
        )

        # ── Earnings surprise ─────────────────────────────────────────────
        try:
            surp_df = tk.get_earnings_surprises()
            if surp_df is not None and not surp_df.empty:
                # Multi-level columns: (ticker, metric) — unwrap if needed
                if hasattr(surp_df.columns, "levels"):
                    try:
                        surp_df = surp_df[yf_sym]
                    except KeyError:
                        surp_df = surp_df.droplevel(0, axis=1)

                actual_col = next((c for c in surp_df.columns if "actual" in str(c).lower()), None)
                est_col    = next(
                    (c for c in surp_df.columns if "estim" in str(c).lower() or "consensus" in str(c).lower()),
                    None,
                )
                if actual_col and est_col:
                    rows  = surp_df[[actual_col, est_col]].dropna().tail(4)
                    surps = []
                    for _, row in rows.iterrows():
                        a, e = _sf(row[actual_col]), _sf(row[est_col])
                        if a is not None and e and e != 0:
                            surps.append((a - e) / abs(e))
                    if surps:
                        out["earnings_surprise"] = sum(surps) / len(surps)
        except Exception as exc:
            log.debug("FinanceToolkit earnings_surprises failed (%s): %s", yf_sym, exc)

        # ── Forward estimates ─────────────────────────────────────────────
        try:
            est_df = tk.get_analyst_estimates()
            if est_df is not None and not est_df.empty:
                if hasattr(est_df.columns, "levels"):
                    try:
                        est_df = est_df[yf_sym]
                    except KeyError:
                        est_df = est_df.droplevel(0, axis=1)

                eps_col = next((c for c in est_df.columns if "eps" in str(c).lower()), None)
                rev_col = next((c for c in est_df.columns if "revenue" in str(c).lower()), None)

                if eps_col and len(est_df) >= 2:
                    fwd_eps  = _sf(est_df[eps_col].iloc[-1])
                    curr_eps = _sf(est_df[eps_col].iloc[-2])
                    if fwd_eps and curr_eps and curr_eps != 0:
                        out["fwd_eps_growth"] = (fwd_eps - curr_eps) / abs(curr_eps)
                        out.setdefault("earnings_revision", out["fwd_eps_growth"])

                if rev_col and len(est_df) >= 2:
                    fwd_rev  = _sf(est_df[rev_col].iloc[-1])
                    curr_rev = _sf(est_df[rev_col].iloc[-2])
                    if fwd_rev and curr_rev and curr_rev != 0:
                        out["fwd_rev_growth"] = (fwd_rev - curr_rev) / abs(curr_rev)
        except Exception as exc:
            log.debug("FinanceToolkit analyst_estimates failed (%s): %s", yf_sym, exc)

    except ImportError:
        log.debug("financetoolkit not installed — skipping for %s", yf_sym)
    except Exception as exc:
        log.debug("FinanceToolkit fetch failed (%s): %s", yf_sym, exc)

    return {k: v for k, v in out.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def fetch_hk_vgpm_metrics(ticker: str) -> Optional[dict]:
    """
    Fetch all VGPM sub-factor raw metrics for an HK ticker from 5 sources.

    Returns a dict matching _fetch_ticker_metrics() output schema exactly,
    ready to be passed directly into _compute_fast_vgpm_universe().
    Returns None if *ticker* is not an HK ticker.
    """
    from src.tools.hk.ticker import is_hk_ticker, to_akshare_code, to_yfinance_code

    if not is_hk_ticker(ticker):
        return None

    ak_code = to_akshare_code(ticker)
    yf_sym  = to_yfinance_code(ticker)

    log.info("HK VGPM fetch: %s  (ak=%s  yf=%s)", ticker, ak_code, yf_sym)

    merged: dict = {}

    # Priority 1 — AKShare (fastest, most reliable for HK snapshot metrics)
    _fill(merged, _fetch_akshare(ak_code))

    # Priority 2 — yfinance (richer ratios, statements, momentum, computed metrics)
    _fill(merged, _fetch_yfinance(yf_sym))

    # Priority 3 — Alpha Spread (ROIC authority, analyst coverage)
    _fill(merged, _fetch_alpha_spread(yf_sym))

    # Priority 4 — Stock Analysis (supplementary gap-fill)
    _fill(merged, _fetch_stock_analysis(yf_sym))

    # Priority 5 — FinanceToolkit (earnings surprise + fwd estimates via FMP)
    _fill(merged, _fetch_finance_toolkit(yf_sym))

    # ── Sanity caps for growth rates (prevent scraping / division-near-zero artifacts) ──
    # Growth rates beyond ±200% are almost always parse errors or near-zero base effects.
    # Cap to ±2.0 (i.e. ±200%) — extreme enough to still differentiate high-growth names.
    _GROWTH_KEYS = ("fwd_rev_growth", "fwd_eps_growth", "eps_growth", "net_inc_growth",
                    "fcf_growth", "rev_growth", "rev_cagr_3y")
    for gk in _GROWTH_KEYS:
        v = merged.get(gk)
        if v is not None:
            merged[gk] = max(-2.0, min(v, 2.0))

    # Ensure complete schema — all missing keys default to None
    for k in _SCHEMA_KEYS:
        merged.setdefault(k, None)

    populated = sum(1 for k in _SCHEMA_KEYS if merged.get(k) is not None)
    log.info("HK VGPM %s: %d / %d sub-factors populated", ticker, populated, len(_SCHEMA_KEYS))

    merged["ticker"] = ticker
    return merged
