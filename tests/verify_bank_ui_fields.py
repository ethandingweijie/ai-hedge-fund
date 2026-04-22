"""
verify_bank_ui_fields.py - backend-data availability smoke test for Bank UI.

Mirrors tests/verify_reit_ui_fields.py — walks every field the 8 proposed
bank valuation panels need and prints PASS / MISSING / RESEARCH-DEPENDENT.

Panels audited:
  1. P/TBV Fair Value Hero        (ROE / CoE / TBV)
  2. Bank Key Stats grid          (ROE, ROA, NIM, CIR, credit cost, NPL)
  3. ROE vs CoE Spread gauge
  4. Capital Return card          (div yield, buyback yield, payout)
  5. PPOP Growth 5y bars
  6. NIM History 5y bars
  7. Loan Growth History 5y bars
  8. Book Quality card            (NPL ratio, NPL coverage, overlays)

Run:
    python -m tests.verify_bank_ui_fields JPM
    python -m tests.verify_bank_ui_fields GS 2026-01-31
    python -m tests.verify_bank_ui_fields D05.SI      # DBS Bank (SGX)
    python -m tests.verify_bank_ui_fields 01398.HK    # ICBC Bank (HKEX)

Requires FMP_API_KEY.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.tools.api import search_line_items, get_market_cap, get_prices  # noqa: E402
from src.agents.analysis.dcf_agent import (                              # noqa: E402
    _compute_bank_metrics,
    _compute_ppop,
    _extract_annual_series,
    _bank_profile_calibration,
    _BANK_PROFILE_CALIBRATION,
)

_GREEN = "\033[32m"
_RED   = "\033[31m"
_YELLOW= "\033[33m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"
_RESET = "\033[0m"

def _ok(label: str, value, unit: str = "") -> bool:
    if value is None:
        print(f"  {_RED}[FAIL]{_RESET} {label:<46} {_RED}MISSING{_RESET}")
        return False
    if isinstance(value, (int, float)):
        print(f"  {_GREEN}[ OK ]{_RESET} {label:<46} {value:>18,.4f} {_DIM}{unit}{_RESET}")
    else:
        txt = str(value)[:60]
        print(f"  {_GREEN}[ OK ]{_RESET} {label:<46} {txt:>18} {_DIM}{unit}{_RESET}")
    return True

def _warn(label: str, reason: str) -> None:
    print(f"  {_YELLOW}[WARN]{_RESET} {label:<46} {_YELLOW}{reason}{_RESET}")

def _hdr(title: str) -> None:
    print(f"\n{_BOLD}-- {title} {'-' * (70 - len(title))}{_RESET}")


def _guess_profile(ticker: str) -> str:
    """Best-effort profile name inference — for harness only; real pipeline
    uses strategic_router classification via TICKER_SECTOR_LOOKUP."""
    t = ticker.upper()
    if t in {"JPM", "BAC", "C", "WFC"}:                    return "Money Center Bank"
    if t in {"HSBC", "00005.HK"}:                          return "Money Center Bank"
    if t in {"GS", "MS"}:                                  return "Investment Bank"
    if t in {"BLK", "BX", "KKR", "APO", "TROW", "AMP"}:    return "Asset Manager"
    if t in {"V", "MA", "PYPL", "SQ"}:                     return "FinTech"
    if t in {"ICBC", "01398.HK", "00939.HK"}:              return "EM Bank"
    if t in {"D05.SI", "U11.SI", "O39.SI"}:                return "Money Center Bank (SG)"
    if t.endswith(".HK") or t.endswith(".SI"):             return "EM Bank"
    return "Regional Bank"


def verify(ticker: str, end_date: str | None = None) -> int:
    end_date = end_date or date.today().strftime("%Y-%m-%d")
    api_key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if not api_key:
        print(f"{_RED}FMP_API_KEY not set - aborting{_RESET}")
        return 99

    profile = _guess_profile(ticker)
    cfg = _bank_profile_calibration(profile)
    print(f"{_BOLD}Bank UI Field Verification - {ticker} @ {end_date}{_RESET}")
    print(f"  Inferred sub-profile: {profile}")
    print(f"  CoE: {cfg['coe']:.1%} | Target ROE: {cfg['target_roe']:.1%} | Target CET1: {cfg['target_cet1']:.1%}")

    # ── Pull line items ──
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
         "cash_and_equivalents",
         # Bank-specific
         "interest_income", "provision_for_loan_losses",
         "goodwill", "intangible_assets", "total_liabilities",
         "operating_expense", "operating_income",
         "share_buyback", "common_stock_repurchased",
         "loans_receivable", "loans_held_for_investment", "total_deposits"],
        end_date=end_date, period="annual", limit=7, api_key=api_key,
    )
    print(f"  fetched {len(line_items)} annual rows")

    series, reported_ccy = _extract_annual_series(line_items)
    if not series:
        print(f"{_RED}Empty series - cannot verify{_RESET}")
        return 99
    mr = series[-1]
    shares = mr.get("shares_outstanding")
    print(f"  reported_currency = {reported_ccy}")
    print(f"  most recent period: {mr.get('period')}")

    try:
        mcap = get_market_cap(ticker, end_date, api_key=api_key)
    except Exception:
        mcap = None
    try:
        _e = datetime.strptime(end_date, "%Y-%m-%d")
        _s = (_e - timedelta(days=14)).strftime("%Y-%m-%d")
        prices = get_prices(ticker, _s, end_date, api_key=api_key)
        close = float(prices[-1].close) if prices else None
    except Exception:
        close = None

    bank_m = _compute_bank_metrics(mr, profile_name=profile)
    fail = 0

    # ── Panel 1: P/TBV Fair Value Hero ──
    _hdr("Panel 1. P/TBV Fair Value Hero (+ quad)")
    if not _ok("current price",       close,                   reported_ccy):       fail += 1
    if not _ok("market cap",          mcap,                    reported_ccy):       fail += 1
    if not _ok("TBV / share",         bank_m.get("tbv_per_share"), reported_ccy):   fail += 1
    if not _ok("BVPS",                mr.get("book_value_per_share"), reported_ccy): fail += 1
    if not _ok("Total Equity",        mr.get("total_equity"),  reported_ccy):       fail += 1
    if not _ok("ROE",                 bank_m.get("roe"),       "(decimal)"):        fail += 1
    # Fair P/TBV = 1 + (ROE - CoE) / CoE
    roe = bank_m.get("roe")
    if roe is not None and cfg["coe"] > 0:
        fair_ptbv = 1 + (roe - cfg["coe"]) / cfg["coe"]
        fair_value = (bank_m.get("tbv_per_share") or 0) * max(fair_ptbv, 0.3)
        if not _ok("fair P/TBV multiple (derived)",  fair_ptbv,  "x"):              fail += 1
        if not _ok("fair value per share (derived)", fair_value, reported_ccy):     fail += 1
    else:
        _warn("fair P/TBV (derived)", "needs ROE + CoE")
        fail += 1
    # CET1 buffer
    cet1 = mr.get("_bank_cet1_research") or bank_m.get("cet1_implied")
    if cet1 is not None:
        buffer_bps = (cet1 - cfg["target_cet1"]) * 10000
        if not _ok("CET1 buffer (bps vs target)", buffer_bps, "bps"): fail += 1
    else:
        _warn("CET1 buffer", "research-dependent (needs _extract_bank_metrics pass)")

    # ── Panel 2: Bank Key Stats grid ──
    _hdr("Panel 2. Bank Key Stats (8-tile 2x4 grid)")
    if not _ok("ROE",                bank_m.get("roe"),                "(decimal)"): fail += 1
    # ROA = NI / total assets
    ni = mr.get("net_income"); assets = mr.get("total_assets")
    roa = (ni / assets) if (ni and assets and assets > 0) else None
    if not _ok("ROA (derived)",      roa,                              "(decimal)"): fail += 1
    if not _ok("NIM",                bank_m.get("nim"),                "(decimal)"): fail += 1
    if not _ok("Efficiency ratio (CIR)", bank_m.get("efficiency_ratio"), "(decimal)"): fail += 1
    if not _ok("Credit cost ratio",  bank_m.get("credit_cost_ratio"),  "(decimal)"): fail += 1
    if not _ok("BVPS",               mr.get("book_value_per_share"),   reported_ccy): fail += 1
    # NPL ratio — research-dependent
    _warn("NPL ratio", "research-dependent (from _extract_bank_metrics.npl_ratio)")
    # CET1 — same
    _warn("CET1 ratio", "research-dependent (from _extract_bank_metrics.cet1_ratio)")

    # ── Panel 3: ROE vs CoE Spread gauge ──
    _hdr("Panel 3. ROE vs CoE Spread gauge")
    if roe is not None:
        spread_bps = (roe - cfg["coe"]) * 10000
        if not _ok("ROE - CoE spread",     spread_bps,        "bps"):                fail += 1
    if not _ok("Target ROE (profile)",     cfg["target_roe"], "(decimal)"):          fail += 1
    if not _ok("CoE (profile)",            cfg["coe"],        "(decimal)"):          fail += 1

    # ── Panel 4: Capital Return ──
    _hdr("Panel 4. Capital Return card")
    dps = mr.get("dividends_per_share")
    div_yield = (dps / close) if (dps and close and close > 0) else None
    if not _ok("Dividend yield (TTM)", div_yield, "(decimal)"):                     fail += 1
    # Buyback yield
    buybacks = mr.get("share_buyback") or mr.get("common_stock_repurchased")
    buybacks = abs(buybacks) if buybacks else None
    bb_yield = (buybacks / mcap) if (buybacks and mcap and mcap > 0) else None
    if not _ok("Buyback yield (LTM)",  bb_yield,  "(decimal)"):                     fail += 1
    # Total payout ratio
    total_payout = 0.0
    if dps and shares: total_payout += dps * shares
    if buybacks:       total_payout += buybacks
    payout_ratio = (total_payout / ni) if (ni and ni > 0) else None
    if not _ok("Total payout ratio",   payout_ratio, "(decimal)"):                  fail += 1
    # CET1 surplus $ (distributable capital)
    if cet1 is not None and mcap is not None:
        surplus_pct = max(0, cet1 - cfg["target_cet1"])
        surplus_usd = surplus_pct * (bank_m.get("rwa_estimate") or 0)
        if not _ok("CET1 surplus $ (implied buyback capacity)", surplus_usd, reported_ccy):
            fail += 1
    else:
        _warn("CET1 surplus $", "needs cet1_ratio (research-dependent)")

    # ── Panel 5: PPOP Growth (5y bars) ──
    # PPOP via _compute_ppop — uses 3-tier fallback:
    #   (1) operating_income + provisions  (cleanest)
    #   (2) NII + non-interest income − opex
    #   (3) revenue − interest_expense − opex  (legacy US-FMP, gated by positivity)
    _hdr("Panel 5. PPOP Growth 5y bar chart (smart-fallback via _compute_ppop)")
    ppop_count = 0
    for row in series:
        lbl = (row.get("period") or "")[:4]
        ppop = _compute_ppop(row)
        if ppop is not None:
            ppop_count += 1
            print(f"  {_GREEN}[ OK ]{_RESET} PPOP {lbl:<10}  {ppop:>18,.0f} {reported_ccy}")
        else:
            print(f"  {_RED}[FAIL]{_RESET} PPOP {lbl:<10}  MISSING (all 3 fallbacks failed)")
    if ppop_count < 3:
        print(f"  {_YELLOW}< 3 PPOP data points - panel will show placeholder on mobile{_RESET}")

    # ── Panel 6: NIM History (5y bars) ──
    _hdr("Panel 6. NIM History 5y bar chart")
    nim_count = 0
    for row in series:
        lbl = (row.get("period") or "")[:4]
        ii = row.get("interest_income"); ie = row.get("interest_expense"); at = row.get("total_assets")
        if ii is not None and ie is not None and at and at > 0:
            nim = (ii - abs(ie)) / at
            nim_count += 1
            print(f"  {_GREEN}[ OK ]{_RESET} NIM {lbl:<11}  {nim:>18.4%}")
        else:
            missing = [k for k,v in {"interest_income":ii,"interest_expense":ie,"total_assets":at}.items() if v is None]
            print(f"  {_RED}[FAIL]{_RESET} NIM {lbl:<11}  MISSING ({', '.join(missing)})")
    if nim_count < 3:
        print(f"  {_RED}< 3 NIM data points - chart would be too sparse{_RESET}")
        fail += 1

    # ── Panel 7: Loan Growth History (5y) ──
    _hdr("Panel 7. Loan Growth History 5y bar chart")
    # Check newly-added loan fields (netLoans, loansAndLeasesReceivables,
    # loansHeldForInvestment). If absent, fall back to research-extractor
    # loan_growth_yoy (gives single latest-year only, not 5-year series).
    loan_count = 0
    for row in series:
        lbl = (row.get("period") or "")[:4]
        loans = row.get("loans_receivable") or row.get("loans_held_for_investment")
        if loans is not None and loans > 0:
            loan_count += 1
            print(f"  {_GREEN}[ OK ]{_RESET} Loans {lbl:<10}  {loans:>18,.0f} {reported_ccy}")
        else:
            print(f"  {_RED}[----]{_RESET} Loans {lbl:<10}  absent in FMP ({profile})")
    if loan_count >= 3:
        print(f"  {_GREEN}5y loan history available — bar chart will render{_RESET}")
    elif loan_count > 0:
        print(f"  {_YELLOW}partial series — chart with {loan_count} years, rest show as gaps{_RESET}")
    else:
        print(f"  {_YELLOW}FMP has no loan data for {ticker} - fallback to research-extractor{_RESET}")
        _warn("Loan growth — fallback path", "research-extractor loan_growth_yoy (latest-year only, no 5y)")

    # ── Panel 8: Book Quality ──
    _hdr("Panel 8. Book Quality (NPL + coverage + overlays)")
    _warn("NPL ratio",         "research-dependent (in _extract_bank_metrics schema)")
    _warn("NPL coverage ratio", "NEW FIELD — needs added to _extract_bank_metrics schema")
    if not _ok("Credit cost (derived)", bank_m.get("credit_cost_ratio"), "(decimal)"): fail += 1
    _warn("Management overlays ($)", "NEW FIELD — needs added to _extract_bank_metrics schema")

    print()
    if fail == 0:
        print(f"{_BOLD}{_GREEN}[ PASS ] All computable fields present — research-dependent fields noted.{_RESET}")
    else:
        print(f"{_BOLD}{_RED}[ FAIL ] {fail} required field(s) missing — see failures above.{_RESET}")
    return fail


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "JPM"
    end_date = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(verify(ticker, end_date))
