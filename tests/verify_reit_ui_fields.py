"""
verify_reit_ui_fields.py - backend-data availability smoke test for the REIT UI.

Purpose
-------
Before we ship the REIT-specific frontend panels (REIT Header, NAV Bridge,
Method Breakdown, Portfolio Composition, Distribution Quality, Cap-Rate
Scenarios, NPI History, DPU History), verify that every field those panels
need has a real computed number on the backend - not ``None``, not a stub.

Run
---
    python -m tests.verify_reit_ui_fields DLR
    python -m tests.verify_reit_ui_fields O 2026-01-31        # (Realty Income)
    python -m tests.verify_reit_ui_fields PLD                  # (Prologis)

Requires
--------
    FMP_API_KEY (read from environment or .env.local)

Reads live FMP data - no mocks. Prints a PASS/FAIL matrix where every UI
field maps to the exact python object (method_iv_table key, reit_breakdown
field, scenario_results key) the React component will consume.
"""

from __future__ import annotations

import os
import sys
from datetime import date

# Make top-level imports resolve when run as `python -m tests.verify_reit_ui_fields`
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.tools.api import search_line_items, get_market_cap, get_prices  # noqa: E402
from src.agents.analysis.dcf_agent import (                                # noqa: E402
    _REIT_SUBTYPE_MULTIPLES,
    _classify_reit_subtype,
    _compute_reit_metrics,
    _extract_annual_series,
)


# -- ANSI helpers (ASCII-only for Windows cp1252) ---------------------------

_GREEN = "\033[32m"
_RED   = "\033[31m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"
_RESET = "\033[0m"

def _ok(label: str, value, unit: str = "") -> bool:
    if value is None:
        print(f"  {_RED}[FAIL]{_RESET} {label:<42} {_RED}MISSING{_RESET}")
        return False
    if isinstance(value, (int, float)):
        print(f"  {_GREEN}[ OK ]{_RESET} {label:<42} {value:>18,.4f} {_DIM}{unit}{_RESET}")
    else:
        txt = str(value)[:60]
        print(f"  {_GREEN}[ OK ]{_RESET} {label:<42} {txt:>18} {_DIM}{unit}{_RESET}")
    return True


def _hdr(title: str) -> None:
    print(f"\n{_BOLD}-- {title} {'-' * (70 - len(title))}{_RESET}")


# -- Main verification ------------------------------------------------------

def verify(ticker: str, end_date: str | None = None) -> int:
    """Returns number of failed checks (0 = all fields computable)."""
    end_date = end_date or date.today().strftime("%Y-%m-%d")
    api_key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if not api_key:
        print(f"{_RED}FMP_API_KEY not set - aborting{_RESET}")
        return 99

    print(f"{_BOLD}REIT UI Field Verification - {ticker} @ {end_date}{_RESET}")

    # -- Pull raw data --
    _hdr("1. Raw FMP line items")
    line_items = search_line_items(
        ticker,
        ["revenue", "free_cash_flow", "shares_outstanding",
         "debt_to_equity", "net_debt", "total_debt", "ebitda", "net_income",
         "total_equity", "total_assets", "dividends_per_share",
         "book_value_per_share", "capital_expenditure", "ebit",
         "interest_expense", "invested_capital",
         "research_and_development", "stock_based_compensation",
         "depreciation_and_amortization", "operating_cash_flow",
         "cash_and_equivalents"],
        end_date=end_date, period="annual", limit=7, api_key=api_key,
    )
    print(f"  fetched {len(line_items)} annual rows")

    series, reported_ccy = _extract_annual_series(line_items)
    if not series:
        print(f"{_RED}Empty series - cannot verify{_RESET}")
        return 99
    most_recent = series[-1]
    shares = most_recent.get("shares_outstanding")
    print(f"  reported_currency = {reported_ccy}")
    print(f"  most recent period: {most_recent.get('period')}")

    # -- Market cap (for implied cap-rate + distribution yield) --
    try:
        mcap = get_market_cap(ticker, end_date, api_key=api_key)
    except Exception as e:
        mcap = None
        print(f"  {_RED}market_cap fetch failed: {e}{_RESET}")
    try:
        # Fetch a 14-day window so we always catch the most recent trading
        # close even if today is a weekend / holiday.
        from datetime import datetime, timedelta
        _e = datetime.strptime(end_date, "%Y-%m-%d")
        _s = (_e - timedelta(days=14)).strftime("%Y-%m-%d")
        latest_prices = get_prices(ticker, _s, end_date, api_key=api_key)
        close = float(latest_prices[-1].close) if latest_prices else None
    except Exception:
        close = None

    # -- REIT sub-type classification + peer multiples --
    # Pull notes from TICKER_SECTOR_LOOKUP — matches what the live pipeline
    # does in dcf_agent.py (uses notes as keyword source for sub-type).
    try:
        from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
        _lookup = TICKER_SECTOR_LOOKUP.get(ticker.upper())
        _notes = _lookup[3] if (_lookup and len(_lookup) >= 4) else ""
    except Exception:
        _notes = ""
    subtype = _classify_reit_subtype(ticker, _notes)
    mults   = _REIT_SUBTYPE_MULTIPLES.get(subtype, _REIT_SUBTYPE_MULTIPLES["default"])

    # -- Compute REIT metrics --
    rm = _compute_reit_metrics(most_recent, subtype=subtype)

    # Simulate reit_breakdown construction (mirrors dcf_agent.py logic)
    total_debt = most_recent.get("total_debt")
    cash       = most_recent.get("cash_and_equivalents")
    dps        = most_recent.get("dividends_per_share")
    cap_rate   = mults["cap_rate"]  # no research override in this harness
    gav        = (rm["noi"] / cap_rate) if (rm.get("noi") and cap_rate > 0) else None
    nav_total  = (gav - (total_debt or 0) + (cash or 0)) if gav is not None else None
    nav_ps     = (nav_total / shares) if (nav_total and shares) else None
    ffo_ps     = (rm["ffo"] / shares) if (rm.get("ffo") and shares) else None
    affo_ps    = (rm["affo"] / shares) if (rm.get("affo") and shares) else None

    fail = 0

    _hdr("2. REIT Header Card (6-tile KPI strip)")
    if not _ok("current price",                   close,     "USD"):        fail += 1
    if not _ok("NAV per share",                   nav_ps,    "USD"):        fail += 1
    if not _ok("sub-type",                        subtype):                 fail += 1
    if not _ok("cap rate peer",                   mults["cap_rate"], "(decimal)"): fail += 1
    if not _ok("distribution yield = dps/price",
               (dps / close) if (dps and close) else None, "(derived)"):   fail += 1
    if not _ok("AFFO coverage = dps/affo_ps",
               (dps / affo_ps) if (dps and affo_ps) else None, "(derived)"): fail += 1
    if not _ok("leverage = debt/(mcap+debt-cash)",
               (total_debt / (mcap + total_debt - (cash or 0)))
               if (total_debt and mcap) else None, "(derived)"):            fail += 1

    _hdr("3. NAV Bridge (waterfall: NOI -> GAV -> NAV -> NAV/sh)")
    if not _ok("NOI (EBITDA proxy)",              rm.get("noi"),       "USD"): fail += 1
    if not _ok("cap rate used",                   cap_rate,            "(decimal)"): fail += 1
    if not _ok("Gross Asset Value",               gav,                 "USD"): fail += 1
    if not _ok("total debt",                      total_debt,          "USD"): fail += 1
    if not _ok("cash",                            cash,                "USD"): fail += 1
    if not _ok("NAV (absolute)",                  nav_total,           "USD"): fail += 1
    if not _ok("shares outstanding",              shares,              "count"): fail += 1
    if not _ok("NAV per share",                   nav_ps,              "USD"): fail += 1

    _hdr("4. Method Breakdown (per-method IVs)")
    if not _ok("NAV method implied price",        nav_ps,              "USD"): fail += 1
    if not _ok("P/FFO implied price = ffo_ps x p_ffo",
               (ffo_ps * mults["p_ffo"]) if ffo_ps else None,       "USD"):    fail += 1
    if not _ok("P/AFFO implied price = affo_ps x p_affo",
               (affo_ps * mults["p_affo"]) if affo_ps else None,    "USD"):    fail += 1
    if not _ok("peer p_ffo multiple",             mults["p_ffo"],      "x"):   fail += 1
    if not _ok("peer p_affo multiple",            mults["p_affo"],     "x"):   fail += 1

    _hdr("5. Distribution Quality")
    if not _ok("DPS (TTM)",                       dps,                 "USD"): fail += 1
    if not _ok("FFO per share",                   ffo_ps,              "USD"): fail += 1
    if not _ok("AFFO per share",                  affo_ps,             "USD"): fail += 1
    if not _ok("AFFO coverage ratio",
               (dps / affo_ps) if (dps and affo_ps) else None, "(ratio)"):   fail += 1

    _hdr("6. NPI History (7y bar chart - CLINT-style)")
    npi_count = 0
    for row in series:
        lbl = (row.get("period") or "")[:4]
        v = row.get("ebitda")
        if v is not None:
            npi_count += 1
            print(f"  {_GREEN}[ OK ]{_RESET} NPI {lbl:<10}  {v:>15,.0f} USD")
        else:
            print(f"  {_RED}[FAIL]{_RESET} NPI {lbl:<10}  MISSING")
    if npi_count < 3:
        print(f"  {_RED}< 3 NPI data points - chart would be too sparse{_RESET}")
        fail += 1

    _hdr("7. DPU History (7y bar chart - CLINT-style)")
    dpu_count = 0
    for row in series:
        lbl = (row.get("period") or "")[:4]
        v = row.get("dividends_per_share")
        if v is not None:
            dpu_count += 1
            print(f"  {_GREEN}[ OK ]{_RESET} DPU {lbl:<10}  {v:>15,.4f} USD/sh")
        else:
            print(f"  {_RED}[FAIL]{_RESET} DPU {lbl:<10}  MISSING")
    if dpu_count < 3:
        print(f"  {_RED}< 3 DPU data points - chart would be too sparse{_RESET}")
        fail += 1

    _hdr("8. Portfolio Composition - RESEARCH-SOURCED (optional)")
    print(f"  {_DIM}subtype_mix and geographic_mix come from _extract_reit_metrics(){_RESET}")
    print(f"  {_DIM}(LLM pass over deep-research; not fetched by this harness).{_RESET}")
    print(f"  {_DIM}Frontend must gracefully hide the pies when these are missing.{_RESET}")

    _hdr("9. Cap-Rate Scenarios - DERIVED FRONTEND")
    print(f"  {_DIM}bear/base/bull intrinsic_value comes from scenario_results.{_RESET}")
    print(f"  {_DIM}Cap-rate sensitivity (+/-50bp) is a frontend recomputation using{_RESET}")
    print(f"  {_DIM}noi + (cap_rate +/- 0.005) - already verified via NOI field above.{_RESET}")

    # -- Summary --
    print()
    if fail == 0:
        print(f"{_BOLD}{_GREEN}[ PASS ] ALL UI FIELDS COMPUTABLE - backend ready for frontend push.{_RESET}")
    else:
        print(f"{_BOLD}{_RED}[ FAIL ] {fail} field(s) missing - fix backend before frontend push.{_RESET}")

    return fail


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "DLR"
    end_date = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(verify(ticker, end_date))
