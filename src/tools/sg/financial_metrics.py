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

        # ── REIT-specific metrics ─────────────────────────────────────────
        # Compute FFO, AFFO, P/FFO, Cap Rate, NAV, P/NAV, payout ratio,
        # debt-to-equity, interest coverage, LTV for REITs
        from src.tools.sg.universe import get_sg_stock_info
        stock_info = get_sg_stock_info(ticker)
        is_reit = stock_info and stock_info.get("sector") == "REIT"

        if is_reit:
            try:
                inc = t.income_stmt if 'inc' not in dir() or inc is None else inc
                bs = t.balance_sheet if 'bs' not in dir() or bs is None else bs
                cf = t.cashflow if 'cf' not in dir() or cf is None else cf

                if inc is not None and not inc.empty:
                    col = inc.columns[0]
                    ni = _parse_float(inc.loc["Net Income", col] if "Net Income" in inc.index else None)
                    dep = _parse_float(inc.loc["Reconciled Depreciation", col] if "Reconciled Depreciation" in inc.index else None)

                    # FFO = Net Income + Depreciation
                    if ni is not None and dep is not None:
                        ffo = ni + dep
                        result["ffo"] = ffo
                        # P/FFO (like P/E for REITs)
                        shares = _parse_float(info.get("sharesOutstanding"))
                        price = result["price"]
                        if ffo > 0 and shares and shares > 0 and price:
                            result["price_to_ffo"] = price / (ffo / shares)

                if cf is not None and not cf.empty:
                    col = cf.columns[0]
                    ocf = _parse_float(cf.loc["Operating Cash Flow", col] if "Operating Cash Flow" in cf.index else None)
                    capex = _parse_float(cf.loc["Capital Expenditure", col] if "Capital Expenditure" in cf.index else None)

                    # AFFO = Operating CF - Maintenance CapEx
                    if ocf is not None:
                        affo = ocf + (capex or 0)  # capex is negative
                        result["affo"] = affo

                # NOI = Operating Income (proxy for REITs)
                result["noi"] = _parse_float(info.get("ebitda"))

                # NAV and P/NAV
                bv = _parse_float(info.get("bookValue"))
                if bv and bv > 0 and result["price"]:
                    result["price_to_nav"] = result["price"] / bv
                result["nav_per_unit"] = bv

                # Payout ratio
                result["payout_ratio"] = _parse_float(info.get("payoutRatio"))

                # Debt metrics
                result["debt_to_equity"] = _parse_float(info.get("debtToEquity"))
                total_debt = _parse_float(info.get("totalDebt"))
                ebitda = _parse_float(info.get("ebitda"))
                if total_debt and ebitda and ebitda > 0:
                    result["net_debt_to_ebitda"] = total_debt / ebitda

                # Interest coverage
                if ebitda:
                    int_exp = None
                    if inc is not None and not inc.empty:
                        int_exp = _parse_float(inc.loc["Interest Expense", inc.columns[0]] if "Interest Expense" in inc.index else None)
                    if int_exp and abs(int_exp) > 0:
                        result["interest_coverage"] = ebitda / abs(int_exp)

                # LTV = Total Debt / Enterprise Value (proxy for property value)
                ev = _parse_float(info.get("enterpriseValue"))
                if total_debt and ev and ev > 0:
                    result["ltv"] = total_debt / ev

                # Cap rate = NOI / Enterprise Value
                if ebitda and ev and ev > 0:
                    result["cap_rate"] = ebitda / ev

                # Current ratio
                result["current_ratio"] = _parse_float(info.get("currentRatio"))

            except Exception:
                pass

    except Exception as e:
        print(f"  [sg/financial_metrics] Error fetching {yf_code}: {e}")

    return result
