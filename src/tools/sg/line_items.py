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
    "EBIT": "ebit",
    "Basic EPS": "eps",
    "Interest Expense": "interest_expense",
    "Tax Provision": "tax_provision",
    "Pretax Income": "pretax_income",
    "Cost Of Revenue": "cost_of_revenue",
    "Operating Expense": "operating_expense",
    "Research Development": "research_development",
    # REIT-critical: D&A for FFO reconstruction. yfinance sometimes exposes
    # this under "Reconciled Depreciation" (on income_stmt) or in the cashflow
    # statement. The fetcher tries both statements so either match populates.
    "Reconciled Depreciation":             "depreciation_and_amortization",
    "Depreciation And Amortization":       "depreciation_and_amortization",
    "Depreciation Amortization Depletion": "depreciation_and_amortization",
    "Depreciation":                        "depreciation_and_amortization",
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


# ── Balance-sheet lines yfinance publishes under names the engine does not use ──
# Copied VERBATIM out of a live payload (scratchpad/sg_sti_labels.json):
# yfinance mangles multi-word labels -- `Investmentsin Associatesat Cost`,
# `Designatedas`, `Profitor Loss` -- so these strings cannot be typed from memory
# or tidied into readable English without silently matching nothing.
#: A COMBINED line: cash + equivalents + short-term investments. Present on 8/8
#: allowlist names. Mapping it straight to `cash_and_equivalents` OR to
#: `short_term_investments` double-counts; STI has to be DERIVED from it.
_YF_CASH_AND_STI = "Cash Cash Equivalents And Short Term Investments"
_YF_CASH = "Cash And Cash Equivalents"
#: The direct path, but only 6/8 -- absent on C38U.SI and P15.SI, where the
#: combined line equals cash exactly and STI is genuinely zero.
_YF_OTHER_STI = "Other Short Term Investments"


def _bs_value(bs, label: str, col):
    """One balance-sheet cell as a float, or None when absent/NaN."""
    if bs is None or getattr(bs, "empty", True) or label not in bs.index:
        return None
    if col not in bs.columns:
        return None
    return _parse_float(bs.loc[label, col])


def _derive_balance_fields(row: dict, bs, col, requested: set) -> None:
    """Fill the balance-sheet fields the engine asks for that yfinance does not
    publish under those names, in place on ``row``.

    Only fields the caller actually requested are written. Everything else on
    the row is copied verbatim into the LineItem by the caller, so adding an
    unrequested key would change the row's shape for every consumer.

    Three gaps, each measured on all eight allowlist .SI names rather than
    assumed:

    1. ``cash_and_equivalents``. The map keys cash as ``cash``, so the field the
       engine actually requests was always None here. That is not cosmetic:
       ``_refresh_balance_sheet_from_latest_quarter`` returns early unless the
       quarter "carries both a cash and a debt figure", so the quarterly overlay
       -- the whole point of preferring FMP over a stale year end -- COULD NOT
       FIRE ON ANY SG NAME that reached this fallback. Aliased, not renamed, so
       a caller that asks for ``cash`` still gets it.

    2. ``short_term_investments``. Taken from yfinance's own
       ``Other Short Term Investments`` where the issuer reports it, and derived
       as ``combined - cash`` only where it does not -- see the block below for
       the measured reason the precedence runs that way and not the other.

    3. ``net_debt``, derived ONLY when yfinance's own row is missing. yfinance
       returns NaN for S08.SI, which became None, which
       ``_net_debt_net_of_investments`` turns into 0.0 -- S08 read as debt-free
       with S$0.363bn of debt against S$0.604bn of cash and investments, and
       S08.SI is in the look-through promote allowlist. A reported figure is
       never overwritten: yfinance's ``Net Debt`` is already net of short-term
       investments (BN4.SI: 10.088 against `total_debt - cash` of 10.209, a
       S$0.120bn gap against S$0.112bn of STI), and replacing reported data with
       derived data is a judgement call this function does not get to make.

    Deliberately NOT netted here: see the matching note in
    ``src/tools/hk/line_items.py::_compute_derived``. Pre-subtracting STI from
    ``net_debt`` would trip the guard in ``_net_debt_net_of_investments`` and
    bypass the sector exclusion that keeps a bank's or an insurer's investment
    portfolio -- which backs its liabilities -- from being counted as spare cash.
    """
    cash = row.get("cash")
    if cash is None:
        cash = _bs_value(bs, _YF_CASH, col)

    # 1. the alias the engine's field name needs
    if ("cash_and_equivalents" in requested
            and row.get("cash_and_equivalents") is None and cash is not None):
        row["cash_and_equivalents"] = cash

    # 2. short-term investments: the DIRECT label first, ``combined - cash`` only
    #    as a fallback. This precedence is the opposite of the one I first
    #    shipped here, and measurement is why. ``sg_impl_check.py`` asserted the
    #    identity ``combined == cash + Other Short Term Investments`` on every
    #    period the provider returns; it holds EXACTLY at the newest period of
    #    all six names that report the direct label -- which is the only period
    #    the rule probe had looked at -- and it BREAKS at a prior period on two
    #    of eight:
    #
    #        BN4.SI 2024-12-31  combined 2.452615  cash 0.918909
    #                           other 0.151082  restricted 1.382624
    #                           residual +1.382624 == Restricted Cash EXACTLY
    #        VC2.SI 2024-12-31  combined 3.332245  cash 3.064681
    #                           other 3.329674    residual -3.062110
    #
    #    So the combined line is cash + STI + RESTRICTED CASH for some issuers
    #    in some years, and ``combined - cash`` then overstates STI by the whole
    #    restricted balance -- 1.533706 published against FMP's 0.151082 on BN4,
    #    a 10x error on the field. The direct label matched FMP to the dollar on
    #    every period of BN4 (2022/2023/2024/2025), VC2 (2022/2023), F34
    #    (2022/2023) and U96 where both were available, so it is the primary.
    #
    #    The fallback still earns its place: it is the only source for C38U.SI
    #    and P15.SI, which lack the direct label, and there the combined line
    #    EQUALS cash so it degrades to exactly 0.0 -- which is what FMP reports
    #    for both. And where the direct label is corrupt (VC2 2024, caught by
    #    the coherence test below) the fallback restores the right TOTAL
    #    liquidity: FMP's cash for that period is 3.329674, i.e. yfinance's
    #    combined line, so cash + (combined - cash) reconciles to FMP's net
    #    position even though the split between the two fields does not.
    #
    #    Cross-feed scoring against FMP is NOT a clean test of this mapping and
    #    was abandoned as the acceptance criterion: FMP folds Restricted Cash
    #    into ``cash_and_equivalents`` for BN4.SI (exactly, S$1.382624bn at
    #    2024-12-31) and folds STI into cash for VC2.SI, so "deviation vs FMP"
    #    measures the two feeds' classification differences, not this code.
    if "short_term_investments" in requested and row.get("short_term_investments") is None:
        direct = _bs_value(bs, _YF_OTHER_STI, col)
        combined = _bs_value(bs, _YF_CASH_AND_STI, col)
        derived = None
        # Coherence: a component cannot exceed the total it belongs to. VC2.SI at
        # 2024-12-31 is the measured counter-example -- cash 3.064681 + other
        # 3.329674 = 6.394 against a combined line of 3.332245, and FMP shows
        # that 3.329674 sitting in ITS cash field with STI = 0.0, i.e. yfinance
        # has the two labels crossed for that period alone. Refusing the
        # incoherent value hands the case to the fallback below instead of
        # publishing S$3.3bn of "investments" that are actually cash, which
        # `_net_debt_net_of_investments` would then subtract from net debt a
        # second time. The residual is EXACTLY zero on every coherent row
        # measured, so the tolerance is float noise, not a tuning knob.
        if direct is not None and (
                combined is None or cash is None
                or cash + direct <= combined + max(1.0, abs(combined) * 1e-6)):
            derived = direct
        if derived is None and combined is not None and cash is not None:
            candidate = combined - cash
            # A negative result means the two lines are not the pair they appear
            # to be for this issuer. Refuse rather than publish a negative
            # liquidity figure, which would ADD to net debt downstream.
            if candidate >= 0.0:
                derived = candidate
        if derived is not None:
            row["short_term_investments"] = derived

    # 3. net debt, only where yfinance reported nothing
    if "net_debt" in requested and row.get("net_debt") is None:
        td = row.get("total_debt")
        if td is not None and cash is not None:
            row["net_debt"] = td - cash


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

        # Build a dividend-per-share lookup by fiscal year by summing the
        # event-level .dividends Series. yfinance's .info["dividendRate"] is
        # a forward-looking annualized number; summing the actual dividend
        # events per year is the accurate historical DPS. Empty Series
        # (non-dividend-paying ticker) results in an empty dict; downstream
        # DPS remains None. Only relevant for REITs / high-yield SGX names.
        dps_by_year: dict[int, float] = {}
        try:
            div_series = t.dividends
            if div_series is not None and len(div_series) > 0:
                for ts, amt in div_series.items():
                    if hasattr(ts, "year"):
                        dps_by_year[ts.year] = dps_by_year.get(ts.year, 0.0) + float(amt)
        except Exception:
            dps_by_year = {}

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

                # REIT-critical: dividends_per_share derived from the annual
                # sum of .dividends event series (yfinance doesn't expose DPS
                # directly for SGX securities). Pulled out of the per-stmt
                # search so the event-series fallback runs even when the three
                # statements don't have a matching row.
                if field == "dividends_per_share" and val is None:
                    _yr = col.year if hasattr(col, "year") else None
                    if _yr and _yr in dps_by_year:
                        val = dps_by_year[_yr]

                row[field] = val

            # Derived balance-sheet lines (Phase 1.4). Runs AFTER the field loop
            # so it sees what the map resolved and never overwrites a value the
            # map already found -- it only fills a requested field that is still
            # None. The balance sheet's columns are its own, so `col` may not be
            # one of them (the periods come from the income statement above);
            # `_bs_value` returns None in that case and the block is inert rather
            # than wrong.
            _derive_balance_fields(row, bs, col, set(line_items))

            results.append(row)

        return results

    except Exception as e:
        print(f"  [sg/line_items] Error fetching {yf_code}: {e}")
        return []
