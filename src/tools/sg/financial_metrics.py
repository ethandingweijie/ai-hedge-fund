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

        # Additional .info fields available for banks and other sectors
        result["operating_margin"] = _parse_float(info.get("operatingMargins"))
        result["payout_ratio"] = _parse_float(info.get("payoutRatio"))
        result["debt_to_equity"] = _parse_float(info.get("debtToEquity"))
        result["current_ratio"] = _parse_float(info.get("currentRatio"))
        result["enterprise_value"] = _parse_float(info.get("enterpriseValue"))

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

        # ── Compute derived metrics from financial statements ─────────────
        try:
            inc = t.income_stmt
            bs = t.balance_sheet
            cf = t.cashflow

            if inc is not None and bs is not None and not inc.empty and not bs.empty:
                latest_inc = inc.iloc[:, 0]
                latest_bs = bs.iloc[:, 0]

                op_inc = _parse_float(latest_inc.get("Operating Income") or latest_inc.get("EBIT"))
                tax_prov = _parse_float(latest_inc.get("Tax Provision"))
                pretax = _parse_float(latest_inc.get("Pretax Income"))
                equity = _parse_float(latest_bs.get("Stockholders Equity") or latest_bs.get("Total Equity Gross Minority Interest"))
                ltd = _parse_float(latest_bs.get("Long Term Debt"))
                cash = _parse_float(latest_bs.get("Cash And Cash Equivalents"))
                total_assets = _parse_float(latest_bs.get("Total Assets"))
                total_debt = _parse_float(latest_bs.get("Total Debt"))
                rev = _parse_float(latest_inc.get("Total Revenue"))
                ni = _parse_float(latest_inc.get("Net Income"))
                shares = _parse_float(info.get("sharesOutstanding"))

                # ROIC = NOPAT / Invested Capital
                if op_inc and pretax and tax_prov is not None:
                    tax_rate = tax_prov / pretax if pretax != 0 else 0.17
                    nopat = op_inc * (1 - tax_rate)
                    ic = (equity or 0) + (ltd or 0) - (cash or 0)
                    if ic > 0:
                        result["roic"] = nopat / ic

                # Asset turnover
                if rev and total_assets and total_assets > 0:
                    result["asset_turnover"] = rev / total_assets

                # Enterprise value
                ev = _parse_float(info.get("enterpriseValue"))
                result["enterprise_value"] = ev

                # Operating margin
                if op_inc is not None and rev and rev > 0:
                    result["operating_margin"] = op_inc / rev

                # Price-to-sales
                if result["market_cap"] and rev and rev > 0:
                    result["price_to_sales"] = result["market_cap"] / rev

                # Debt metrics
                if total_debt is not None and total_assets and total_assets > 0:
                    result["debt_to_assets"] = total_debt / total_assets
                # Debt-to-equity: compute from balance sheet if not from .info
                if not result.get("debt_to_equity") and total_debt and equity and equity > 0:
                    result["debt_to_equity"] = (total_debt / equity) * 100  # yfinance returns as percentage

                # FCF yield: compute if not already set
                if not result.get("fcf_yield"):
                    fcf_for_yield = _parse_float(info.get("freeCashflow"))
                    if fcf_for_yield and result["market_cap"] and result["market_cap"] > 0:
                        result["fcf_yield"] = fcf_for_yield / result["market_cap"]

                # ROIC for banks: use ROE as proxy (bank ROIC ~ ROE)
                if not result.get("roic") and result.get("roe"):
                    result["roic"] = result["roe"]

                # Book value per share
                if equity and shares and shares > 0:
                    result["book_value_per_share"] = equity / shares

                # Free cash flow per share (fallback to operatingCashflow for banks)
                fcf_val = _parse_float(info.get("freeCashflow")) or _parse_float(info.get("operatingCashflow"))
                if fcf_val is not None and shares and shares > 0:
                    result["free_cash_flow_per_share"] = fcf_val / shares

                # Interest coverage from statements (for banks: Net Interest Income / Operating Expense)
                if not result.get("interest_coverage"):
                    int_exp = _parse_float(latest_inc.get("Interest Expense"))
                    if op_inc and int_exp and abs(int_exp) > 0:
                        result["interest_coverage"] = op_inc / abs(int_exp)
                    elif rev and rev > 0:
                        # Banks: use Net Interest Income / Operating Expense as proxy
                        nii = _parse_float(latest_inc.get("Net Interest Income"))
                        opex = _parse_float(latest_inc.get("Operating Expense"))
                        if nii and opex and opex > 0:
                            result["interest_coverage"] = nii / opex

                # Quick ratio & cash ratio from balance sheet
                current_assets = _parse_float(latest_bs.get("Current Assets"))
                current_liab = _parse_float(latest_bs.get("Current Liabilities"))
                inventory = _parse_float(latest_bs.get("Inventory"))
                if current_assets and current_liab and current_liab > 0:
                    if inventory:
                        result["quick_ratio"] = (current_assets - inventory) / current_liab
                    if cash:
                        result["cash_ratio"] = cash / current_liab

                # Operating cash flow ratio
                if cf is not None and not cf.empty:
                    latest_cf = cf.iloc[:, 0]
                    ocf = _parse_float(latest_cf.get("Operating Cash Flow"))
                    if ocf and current_liab and current_liab > 0:
                        result["operating_cash_flow_ratio"] = ocf / current_liab

                # ── Growth metrics (YoY from 2 years of statements) ──────
                if inc.shape[1] >= 2:
                    prev_inc = inc.iloc[:, 1]
                    prev_rev = _parse_float(prev_inc.get("Total Revenue"))
                    prev_ni = _parse_float(prev_inc.get("Net Income"))
                    prev_op = _parse_float(prev_inc.get("Operating Income") or prev_inc.get("EBIT"))
                    prev_ebitda = _parse_float(prev_inc.get("EBITDA"))
                    prev_eps_val = _parse_float(prev_inc.get("Basic EPS") or prev_inc.get("Diluted EPS"))
                    cur_ebitda = _parse_float(latest_inc.get("EBITDA"))
                    cur_eps_val = _parse_float(latest_inc.get("Basic EPS") or latest_inc.get("Diluted EPS"))

                    if rev and prev_rev and prev_rev != 0:
                        result["revenue_growth"] = (rev - prev_rev) / abs(prev_rev)
                    if ni and prev_ni and prev_ni != 0:
                        result["earnings_growth"] = (ni - prev_ni) / abs(prev_ni)
                    if op_inc and prev_op and prev_op != 0:
                        result["operating_income_growth"] = (op_inc - prev_op) / abs(prev_op)
                    if cur_ebitda and prev_ebitda and prev_ebitda != 0:
                        result["ebitda_growth"] = (cur_ebitda - prev_ebitda) / abs(prev_ebitda)
                    # EPS growth — try statement EPS first, fallback to NI/shares
                    if cur_eps_val and prev_eps_val and prev_eps_val != 0:
                        result["earnings_per_share_growth"] = (cur_eps_val - prev_eps_val) / abs(prev_eps_val)
                    elif ni and prev_ni and shares and shares > 0:
                        cur_eps_c = ni / shares
                        prev_eps_c = prev_ni / shares  # approximate
                        if prev_eps_c != 0:
                            result["earnings_per_share_growth"] = (cur_eps_c - prev_eps_c) / abs(prev_eps_c)

                    # For banks: operating_income_growth from Operating Expense if no Operating Income
                    if not result.get("operating_income_growth"):
                        cur_opex = _parse_float(latest_inc.get("Operating Expense"))
                        prev_opex = _parse_float(prev_inc.get("Operating Expense"))
                        if cur_opex and prev_opex and prev_opex != 0 and rev and prev_rev:
                            # Cost-to-income improvement as proxy
                            cur_oi = rev - cur_opex
                            prev_oi = prev_rev - prev_opex
                            if prev_oi != 0:
                                result["operating_income_growth"] = (cur_oi - prev_oi) / abs(prev_oi)

                if bs.shape[1] >= 2:
                    prev_bs = bs.iloc[:, 1]
                    prev_equity = _parse_float(prev_bs.get("Stockholders Equity") or prev_bs.get("Total Equity Gross Minority Interest"))
                    if equity and prev_equity and prev_equity != 0:
                        result["book_value_growth"] = (equity - prev_equity) / abs(prev_equity)

                if cf is not None and not cf.empty and cf.shape[1] >= 2:
                    latest_cf = cf.iloc[:, 0]
                    prev_cf = cf.iloc[:, 1]
                    cur_fcf = _parse_float(latest_cf.get("Free Cash Flow"))
                    prev_fcf = _parse_float(prev_cf.get("Free Cash Flow"))
                    if cur_fcf and prev_fcf and prev_fcf != 0:
                        result["free_cash_flow_growth"] = (cur_fcf - prev_fcf) / abs(prev_fcf)

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
