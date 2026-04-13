"""
SGX financial statement line items — yfinance primary.

Returns statements in the same schema as the US/HK paths:
  [{date, period_label, revenue, net_income, operating_income, ...}]

yfinance provides English column names natively — no translation layer needed.
"""

from typing import Optional
from src.tools.sg.ticker import to_yfinance_code
from src.tools.sg._utils import _parse_float


# yfinance → standard field name mapping
_YF_INCOME_MAP = {
    "Total Revenue": "revenue",
    "Net Income": "net_income",
    "Operating Income": "operating_income",
    "Gross Profit": "gross_profit",
    "EBITDA": "ebitda",
    "Basic EPS": "eps",
    "Interest Expense": "interest_expense",
    "Tax Provision": "tax_provision",
    "Pretax Income": "pretax_income",
    "Cost Of Revenue": "cost_of_revenue",
    "Operating Expense": "operating_expense",
    "Research Development": "research_development",
}

_YF_BALANCE_MAP = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liabilities",
    "Stockholders Equity": "total_equity",
    "Cash And Cash Equivalents": "cash",
    "Long Term Debt": "long_term_debt",
    "Current Debt": "short_term_debt",
    "Net Debt": "net_debt",
    "Total Debt": "total_debt",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Inventory": "inventory",
    "Accounts Receivable": "accounts_receivable",
    "Goodwill And Other Intangible Assets": "goodwill",
    "Ordinary Shares Number": "shares_outstanding",
}

_YF_CASHFLOW_MAP = {
    "Operating Cash Flow": "operating_cash_flow",
    "Capital Expenditure": "capital_expenditure",
    "Free Cash Flow": "free_cash_flow",
    "Repurchase Of Capital Stock": "share_buyback",
    "Common Stock Dividend Paid": "dividends_paid",
    "Issuance Of Debt": "debt_issuance",
    "Repayment Of Debt": "debt_repayment",
}


def search_sg_line_items(
    ticker: str,
    line_items: list[str],
    period: str = "annual",
    limit: int = 5,
) -> list[dict]:
    """Fetch financial statement line items for an SGX ticker.

    Parameters
    ----------
    ticker : str
    line_items : list[str] — requested field names (e.g. ["revenue", "net_income"])
    period : "annual" or "quarterly"
    limit : max number of periods to return

    Returns
    -------
    List of dicts, each representing one period, sorted oldest → newest.
    """
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)

    try:
        t = yf.Ticker(yf_code)

        if period == "quarterly":
            inc = t.quarterly_income_stmt
            bs = t.quarterly_balance_sheet
            cf = t.quarterly_cashflow
        else:
            inc = t.income_stmt
            bs = t.balance_sheet
            cf = t.cashflow

        if inc is None or inc.empty:
            return []

        # Build all mappings
        all_maps = {}
        all_maps.update(_YF_INCOME_MAP)
        all_maps.update(_YF_BALANCE_MAP)
        all_maps.update(_YF_CASHFLOW_MAP)

        # Reverse map: standard → yfinance name
        rev_map = {v: k for k, v in all_maps.items()}

        # Collect periods from income statement columns (they are Timestamps)
        periods = list(inc.columns[:limit])

        results = []
        for col in reversed(periods):  # oldest → newest
            date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
            year = col.year if hasattr(col, "year") else ""
            quarter = f"Q{(col.month - 1) // 3 + 1}" if period == "quarterly" and hasattr(col, "month") else ""
            period_label = f"{quarter} {year}" if quarter else f"FY{year}"

            row = {"date": date_str, "period_label": period_label.strip()}

            for field in line_items:
                yf_name = rev_map.get(field, field)
                val = None

                # Search across all three statements
                for stmt in [inc, bs, cf]:
                    if stmt is not None and not stmt.empty and col in stmt.columns:
                        if yf_name in stmt.index:
                            val = _parse_float(stmt.loc[yf_name, col])
                            break
                        # Try CamelCase variations
                        for idx_name in stmt.index:
                            if all_maps.get(idx_name) == field:
                                val = _parse_float(stmt.loc[idx_name, col])
                                break
                        if val is not None:
                            break

                row[field] = val

            results.append(row)

        return results

    except Exception as e:
        print(f"  [sg/line_items] Error fetching {yf_code}: {e}")
        return []
