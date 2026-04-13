"""
SGX financial metrics snapshot — yfinance .info as primary source.

Returns a dict of ~30 metrics matching the schema used by the VGPM scoring
engine and the analysis pipeline.
"""

import math
from typing import Optional
from src.tools.sg.ticker import to_yfinance_code
from src.tools.sg._utils import _parse_float, _safe_div


def get_sg_financial_metrics(ticker: str) -> dict:
    """Fetch a snapshot of financial metrics for an SGX ticker.

    Returns a dict with keys matching the VGPM scoring schema:
    pe, pb, ev_ebitda, ev_sales, fwd_pe, peg, roe, roa, net_margin,
    gross_margin, div_yield, market_cap, beta, etc.
    """
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)

    result: dict = {
        "pe": None, "pb": None, "ev_ebitda": None, "ev_sales": None,
        "peg": None, "fwd_pe": None, "fcf_yield": None, "div_yield": None,
        "roe": None, "roa": None, "roic": None, "net_margin": None,
        "gross_margin": None, "fcf_conversion": None, "asset_turnover": None,
        "market_cap": None, "beta": None, "rec_score": None,
        "revenue": None, "net_income": None, "eps": None,
        "price": None, "currency": "SGD",
    }

    try:
        t = yf.Ticker(yf_code)
        info = t.info

        result["pe"] = _parse_float(info.get("trailingPE"))
        result["pb"] = _parse_float(info.get("priceToBook"))
        result["ev_ebitda"] = _parse_float(info.get("enterpriseToEbitda"))
        result["ev_sales"] = _parse_float(info.get("enterpriseToRevenue"))
        result["fwd_pe"] = _parse_float(info.get("forwardPE"))
        result["peg"] = _parse_float(info.get("pegRatio"))
        result["div_yield"] = _parse_float(info.get("dividendYield"))
        result["roe"] = _parse_float(info.get("returnOnEquity"))
        result["roa"] = _parse_float(info.get("returnOnAssets"))
        result["net_margin"] = _parse_float(info.get("profitMargins"))
        result["gross_margin"] = _parse_float(info.get("grossMargins"))
        result["market_cap"] = _parse_float(info.get("marketCap"))
        result["beta"] = _parse_float(info.get("beta"))
        result["revenue"] = _parse_float(info.get("totalRevenue"))
        result["net_income"] = _parse_float(info.get("netIncomeToCommon"))
        result["eps"] = _parse_float(info.get("trailingEps"))
        result["price"] = _parse_float(info.get("currentPrice") or info.get("regularMarketPrice"))

        # FCF yield: freeCashflow / marketCap
        fcf = _parse_float(info.get("freeCashflow"))
        mcap = result["market_cap"]
        if fcf and mcap and mcap > 0:
            result["fcf_yield"] = fcf / mcap

        # FCF conversion: FCF / Net Income
        ni = result["net_income"]
        if fcf and ni and ni > 0:
            result["fcf_conversion"] = fcf / ni

        # Recommendation score: invert yfinance 1-5 scale to 0-1 (1=strong buy → 1.0)
        rec = _parse_float(info.get("recommendationMean"))
        if rec:
            result["rec_score"] = max(0, (5 - rec) / 4)

        # ROIC: compute from statements if not in .info
        # ROIC = NOPAT / Invested Capital
        # NOPAT = Operating Income × (1 - tax rate)
        # Invested Capital = Total Equity + Long Term Debt - Cash
        try:
            inc = t.income_stmt
            bs = t.balance_sheet
            if inc is not None and bs is not None and not inc.empty and not bs.empty:
                latest_inc = inc.iloc[:, 0]
                latest_bs = bs.iloc[:, 0]

                op_inc = _parse_float(latest_inc.get("Operating Income") or latest_inc.get("EBIT"))
                tax_prov = _parse_float(latest_inc.get("Tax Provision"))
                pretax = _parse_float(latest_inc.get("Pretax Income"))
                equity = _parse_float(latest_bs.get("Stockholders Equity") or latest_bs.get("Total Equity Gross Minority Interest"))
                ltd = _parse_float(latest_bs.get("Long Term Debt"))
                cash = _parse_float(latest_bs.get("Cash And Cash Equivalents"))

                if op_inc and pretax and tax_prov is not None:
                    tax_rate = tax_prov / pretax if pretax != 0 else 0.17
                    nopat = op_inc * (1 - tax_rate)
                    ic = (equity or 0) + (ltd or 0) - (cash or 0)
                    if ic > 0:
                        result["roic"] = nopat / ic

                # Asset turnover
                total_assets = _parse_float(latest_bs.get("Total Assets"))
                rev = _parse_float(latest_inc.get("Total Revenue"))
                if rev and total_assets and total_assets > 0:
                    result["asset_turnover"] = rev / total_assets
        except Exception:
            pass

    except Exception as e:
        print(f"  [sg/financial_metrics] Error fetching {yf_code}: {e}")

    return result
