"""
SGX VGPM raw metrics fetcher — multi-source merge.

Data source priority:
  1. yfinance .info (primary — P/E, P/B, ROE, ROA, margins, div yield)
  2. yfinance .financials (computed — growth rates, Piotroski, ROIC, FCF conversion)
  3. stockanalysis.com (gap-filler — scrape ratios, analyst data)

Returns the same 23-key schema expected by the VGPM scoring engine in
screener_service.py.
"""

import math
from typing import Optional
from src.tools.sg.ticker import to_yfinance_code, to_stockanalysis_code
from src.tools.sg._utils import _parse_float, _safe_div, _cap_growth


def _fetch_yfinance_metrics(ticker: str) -> dict:
    """Source 1+2: yfinance .info + computed metrics from statements."""
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)
    m: dict = {}

    try:
        t = yf.Ticker(yf_code)
        info = t.info

        # ── Source 1: .info snapshot ─────────────────────────────────────
        m["pe"] = _parse_float(info.get("trailingPE"))
        m["pb"] = _parse_float(info.get("priceToBook"))
        m["ev_ebitda"] = _parse_float(info.get("enterpriseToEbitda"))
        m["ev_sales"] = _parse_float(info.get("enterpriseToRevenue"))
        m["fwd_pe"] = _parse_float(info.get("forwardPE"))
        m["peg"] = _parse_float(info.get("pegRatio"))
        m["div_yield"] = _parse_float(info.get("dividendYield"))
        m["roe"] = _parse_float(info.get("returnOnEquity"))
        m["roa"] = _parse_float(info.get("returnOnAssets"))
        m["net_margin"] = _parse_float(info.get("profitMargins"))
        m["gross_margin"] = _parse_float(info.get("grossMargins"))
        m["market_cap_sgd"] = _parse_float(info.get("marketCap"))

        # FCF yield
        fcf = _parse_float(info.get("freeCashflow"))
        mcap = m.get("market_cap_sgd")
        if fcf and mcap and mcap > 0:
            m["fcf_yield"] = fcf / mcap

        # Recommendation score (invert 1-5 → 0-1)
        rec = _parse_float(info.get("recommendationMean"))
        if rec:
            m["rec_score"] = max(0, (5 - rec) / 4)

        # ── Source 2: Computed from statements ───────────────────────────
        inc = t.income_stmt
        bs = t.balance_sheet
        cf = t.cashflow

        if inc is not None and not inc.empty:
            cols = list(inc.columns)

            # Revenue growth (latest year vs previous)
            if len(cols) >= 2:
                rev_cur = _parse_float(inc.loc["Total Revenue", cols[0]] if "Total Revenue" in inc.index else None)
                rev_prev = _parse_float(inc.loc["Total Revenue", cols[1]] if "Total Revenue" in inc.index else None)
                if rev_cur and rev_prev and rev_prev != 0:
                    m["rev_growth"] = _cap_growth((rev_cur - rev_prev) / abs(rev_prev))

                # EPS growth
                eps_cur = _parse_float(inc.loc["Basic EPS", cols[0]] if "Basic EPS" in inc.index else None)
                eps_prev = _parse_float(inc.loc["Basic EPS", cols[1]] if "Basic EPS" in inc.index else None)
                if eps_cur and eps_prev and eps_prev != 0:
                    m["eps_growth"] = _cap_growth((eps_cur - eps_prev) / abs(eps_prev))

                # Net income growth
                ni_cur = _parse_float(inc.loc["Net Income", cols[0]] if "Net Income" in inc.index else None)
                ni_prev = _parse_float(inc.loc["Net Income", cols[1]] if "Net Income" in inc.index else None)
                if ni_cur and ni_prev and ni_prev != 0:
                    m["net_inc_growth"] = _cap_growth((ni_cur - ni_prev) / abs(ni_prev))

            # Revenue CAGR 3Y
            if len(cols) >= 4:
                rev_now = _parse_float(inc.loc["Total Revenue", cols[0]] if "Total Revenue" in inc.index else None)
                rev_3y = _parse_float(inc.loc["Total Revenue", cols[3]] if "Total Revenue" in inc.index else None)
                if rev_now and rev_3y and rev_3y > 0:
                    m["rev_cagr_3y"] = _cap_growth((rev_now / rev_3y) ** (1 / 3) - 1)

        # FCF growth
        if cf is not None and not cf.empty and len(cf.columns) >= 2:
            fcf_cur = _parse_float(cf.loc["Free Cash Flow", cf.columns[0]] if "Free Cash Flow" in cf.index else None)
            fcf_prev = _parse_float(cf.loc["Free Cash Flow", cf.columns[1]] if "Free Cash Flow" in cf.index else None)
            if fcf_cur and fcf_prev and fcf_prev != 0:
                m["fcf_growth"] = _cap_growth((fcf_cur - fcf_prev) / abs(fcf_prev))

        # FCF conversion: FCF / Net Income
        if cf is not None and inc is not None and not cf.empty and not inc.empty:
            fcf_val = _parse_float(cf.loc["Free Cash Flow", cf.columns[0]] if "Free Cash Flow" in cf.index else None)
            ni_val = _parse_float(inc.loc["Net Income", inc.columns[0]] if "Net Income" in inc.index else None)
            if fcf_val is not None and ni_val and ni_val > 0:
                m["fcf_conversion"] = fcf_val / ni_val

        # ROIC: NOPAT / Invested Capital
        if inc is not None and bs is not None and not inc.empty and not bs.empty:
            op_inc = _parse_float(inc.loc["Operating Income", inc.columns[0]] if "Operating Income" in inc.index else None)
            tax = _parse_float(inc.loc["Tax Provision", inc.columns[0]] if "Tax Provision" in inc.index else None)
            pretax = _parse_float(inc.loc["Pretax Income", inc.columns[0]] if "Pretax Income" in inc.index else None)
            equity_key = "Stockholders Equity" if "Stockholders Equity" in bs.index else "Total Equity Gross Minority Interest"
            equity = _parse_float(bs.loc[equity_key, bs.columns[0]] if equity_key in bs.index else None)
            ltd = _parse_float(bs.loc["Long Term Debt", bs.columns[0]] if "Long Term Debt" in bs.index else None)
            cash = _parse_float(bs.loc["Cash And Cash Equivalents", bs.columns[0]] if "Cash And Cash Equivalents" in bs.index else None)

            if op_inc and pretax and tax is not None:
                tax_rate = tax / pretax if pretax != 0 else 0.17
                nopat = op_inc * (1 - tax_rate)
                ic = (equity or 0) + (ltd or 0) - (cash or 0)
                if ic > 0:
                    m["roic"] = nopat / ic

        # Asset turnover
        if inc is not None and bs is not None and not inc.empty and not bs.empty:
            rev = _parse_float(inc.loc["Total Revenue", inc.columns[0]] if "Total Revenue" in inc.index else None)
            ta = _parse_float(bs.loc["Total Assets", bs.columns[0]] if "Total Assets" in bs.index else None)
            if rev and ta and ta > 0:
                m["asset_turnover"] = rev / ta

        # Piotroski F-score (9-point, computed from statements)
        m["piotroski"] = _compute_piotroski(t)

        # Price momentum from historical data
        try:
            hist = t.history(period="1y")
            if hist is not None and len(hist) > 0:
                prices = hist["Close"]
                current = prices.iloc[-1]
                if len(prices) > 240:
                    m["price_1y"] = (current / prices.iloc[0] - 1)
                if len(prices) > 120:
                    m["price_6m"] = (current / prices.iloc[-min(126, len(prices))] - 1)
                if len(prices) > 60:
                    m["price_3m"] = (current / prices.iloc[-min(63, len(prices))] - 1)
        except Exception:
            pass

    except Exception as e:
        print(f"  [sg/vgpm] yfinance error for {ticker}: {e}")

    return m


def _compute_piotroski(t) -> Optional[float]:
    """Compute Piotroski F-score (0-9) from yfinance statements. Returns None if insufficient data."""
    try:
        inc = t.income_stmt
        bs = t.balance_sheet
        cf = t.cashflow
        if any(x is None or x.empty for x in [inc, bs, cf]):
            return None
        if len(inc.columns) < 2 or len(bs.columns) < 2:
            return None

        score = 0
        c0, c1 = inc.columns[0], inc.columns[1]

        # Helper
        def _v(stmt, key, col):
            return _parse_float(stmt.loc[key, col]) if key in stmt.index else None

        ni = _v(inc, "Net Income", c0)
        ta_cur = _v(bs, "Total Assets", bs.columns[0])
        ta_prev = _v(bs, "Total Assets", bs.columns[1])
        cfo = _v(cf, "Operating Cash Flow", cf.columns[0])
        rev_cur = _v(inc, "Total Revenue", c0)
        rev_prev = _v(inc, "Total Revenue", c1)
        gp_cur = _v(inc, "Gross Profit", c0)
        gp_prev = _v(inc, "Gross Profit", c1)
        ltd_cur = _v(bs, "Long Term Debt", bs.columns[0])
        ltd_prev = _v(bs, "Long Term Debt", bs.columns[1])
        ca_cur = _v(bs, "Current Assets", bs.columns[0])
        cl_cur = _v(bs, "Current Liabilities", bs.columns[0])
        ca_prev = _v(bs, "Current Assets", bs.columns[1])
        cl_prev = _v(bs, "Current Liabilities", bs.columns[1])
        shares_cur = _v(bs, "Ordinary Shares Number", bs.columns[0])
        shares_prev = _v(bs, "Ordinary Shares Number", bs.columns[1])

        # 1. ROA > 0
        if ni and ta_cur and ta_cur > 0 and ni / ta_cur > 0:
            score += 1
        # 2. Operating CF > 0
        if cfo and cfo > 0:
            score += 1
        # 3. ROA improvement
        ni_prev = _v(inc, "Net Income", c1)
        if ni and ni_prev and ta_cur and ta_prev and ta_cur > 0 and ta_prev > 0:
            if ni / ta_cur > ni_prev / ta_prev:
                score += 1
        # 4. Accrual quality: CFO > NI
        if cfo and ni and cfo > ni:
            score += 1
        # 5. Leverage decrease
        if ltd_cur is not None and ltd_prev is not None and ltd_cur < ltd_prev:
            score += 1
        # 6. Liquidity: current ratio improvement
        if ca_cur and cl_cur and ca_prev and cl_prev and cl_cur > 0 and cl_prev > 0:
            if ca_cur / cl_cur > ca_prev / cl_prev:
                score += 1
        # 7. No dilution
        if shares_cur and shares_prev and shares_cur <= shares_prev:
            score += 1
        # 8. Gross margin improvement
        if gp_cur and gp_prev and rev_cur and rev_prev and rev_cur > 0 and rev_prev > 0:
            if gp_cur / rev_cur > gp_prev / rev_prev:
                score += 1
        # 9. Asset turnover improvement
        if rev_cur and rev_prev and ta_cur and ta_prev and ta_cur > 0 and ta_prev > 0:
            if rev_cur / ta_cur > rev_prev / ta_prev:
                score += 1

        return score / 9.0  # normalize to 0-1

    except Exception:
        return None


def _fill(base: dict, supplement: dict) -> dict:
    """Merge supplement into base — never overwrite non-None values."""
    for k, v in supplement.items():
        if v is not None and base.get(k) is None:
            base[k] = v
    return base


def fetch_sg_vgpm_metrics(ticker: str) -> dict:
    """Fetch all VGPM raw metrics for an SGX ticker.

    Returns a dict with ~25 keys matching the VGPM scoring schema:
    pe, pb, ev_ebitda, ev_sales, peg, fwd_pe, fcf_yield, div_yield,
    rev_growth, rev_cagr_3y, eps_growth, fcf_growth, net_inc_growth,
    earnings_surprise, fwd_eps_growth, fwd_rev_growth,
    roe, roa, roic, net_margin, gross_margin, fcf_conversion, piotroski,
    asset_turnover, price_1y, price_6m, price_3m,
    earnings_revision, analyst_upgrade, rec_score, short_ratio,
    market_cap_sgd
    """
    # Full schema with None defaults
    result = {
        "pe": None, "pb": None, "ev_ebitda": None, "ev_sales": None,
        "peg": None, "fwd_pe": None, "fcf_yield": None, "div_yield": None,
        "rev_growth": None, "rev_cagr_3y": None, "eps_growth": None,
        "fcf_growth": None, "net_inc_growth": None, "earnings_surprise": None,
        "fwd_eps_growth": None, "fwd_rev_growth": None,
        "roe": None, "roa": None, "roic": None, "net_margin": None,
        "gross_margin": None, "fcf_conversion": None, "piotroski": None,
        "asset_turnover": None, "price_1y": None, "price_6m": None,
        "price_3m": None, "earnings_revision": None, "analyst_upgrade": None,
        "rec_score": None, "short_ratio": None, "market_cap_sgd": None,
    }

    # Source 1+2: yfinance
    yf_metrics = _fetch_yfinance_metrics(ticker)
    _fill(result, yf_metrics)

    # Use forward EPS growth as earnings_revision proxy
    if result.get("fwd_pe") and result.get("pe") and result["pe"] > 0:
        result["earnings_revision"] = _cap_growth(result["pe"] / result["fwd_pe"] - 1)

    return result
