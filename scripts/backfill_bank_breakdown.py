"""
scripts/backfill_bank_breakdown.py
===================================
Re-derives dcf_range.bank_breakdown for archived bank runs created before
the Bank Valuation panel shipped. Mirrors scripts/backfill_reit_breakdown.py
— same database (web_runs.full_result_json), same patch semantics.

Usage
-----
    python -m scripts.backfill_bank_breakdown --dry-run
    python -m scripts.backfill_bank_breakdown --ticker JPM
    python -m scripts.backfill_bank_breakdown
    python -m scripts.backfill_bank_breakdown --force

Requires FMP_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.run_archive import DB_PATH, _get_conn as _get_archive_conn  # noqa: E402
from src.tools.api import search_line_items                                  # noqa: E402
from src.agents.analysis.dcf_agent import (                                  # noqa: E402
    _compute_bank_metrics,
    _compute_ppop,
    _extract_annual_series,
    _bank_profile_calibration,
    _BANK_PROFILE_CALIBRATION,
)


_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

def _log(msg: str) -> None:
    print(msg, flush=True)


def _build_bank_breakdown(
    ticker: str,
    end_date: str,
    shares: float,
    profile_name: str,
    market_cap: float | None,
    api_key: str,
) -> dict | None:
    """Re-derive bank_breakdown from FMP data. Mirrors dcf_agent.py gate."""
    try:
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
             "interest_income", "provision_for_loan_losses",
             "goodwill", "intangible_assets", "total_liabilities",
             "operating_expense", "operating_income",
             "share_buyback", "common_stock_repurchased",
             "loans_receivable", "loans_held_for_investment", "total_deposits"],
            end_date=end_date, period="annual", limit=7, api_key=api_key,
        )
    except Exception as exc:
        _log(f"  {_YELLOW}! line items fetch failed for {ticker}@{end_date}: {exc}{_RESET}")
        return None

    series, _ccy = _extract_annual_series(line_items)
    if not series:
        _log(f"  {_YELLOW}! empty series for {ticker}@{end_date}{_RESET}")
        return None
    most_recent = series[-1]

    bank_m = _compute_bank_metrics(most_recent, profile_name=profile_name)
    cfg    = _bank_profile_calibration(profile_name)

    ni       = most_recent.get("net_income")
    equity   = most_recent.get("total_equity")
    assets   = most_recent.get("total_assets")
    bvps     = most_recent.get("book_value_per_share")
    tbv_ps   = bank_m.get("tbv_per_share")
    roe      = bank_m.get("roe")
    coe      = cfg["coe"]
    target_roe  = cfg["target_roe"]
    target_cet1 = cfg["target_cet1"]
    dps      = most_recent.get("dividends_per_share")
    buybacks = most_recent.get("share_buyback") or most_recent.get("common_stock_repurchased")
    buybacks = abs(buybacks) if buybacks else None

    fair_ptbv = None
    fair_value = None
    if roe is not None and coe > 0:
        fair_ptbv = max(0.3, min(3.0, 1.0 + (roe - coe) / coe))
        if tbv_ps and tbv_ps > 0:
            fair_value = round(tbv_ps * fair_ptbv, 2)

    cet1 = bank_m.get("cet1_implied")   # research override not available in backfill path
    cet1_buffer_bps = None
    cet1_surplus = None
    if cet1 is not None:
        cet1_buffer_bps = round((cet1 - target_cet1) * 10000, 0)
        rwa = bank_m.get("rwa_estimate")
        if rwa and rwa > 0:
            cet1_surplus = round(max(0, cet1 - target_cet1) * rwa, 0)

    roa = (ni / assets) if (ni and assets and assets > 0) else None

    div_yield = None
    buyback_yield = None
    if market_cap and market_cap > 0:
        if dps and shares and shares > 0:
            div_yield = (dps * shares) / market_cap
        if buybacks:
            buyback_yield = buybacks / market_cap
    payout_ratio = None
    if ni and ni > 0:
        total_payout = 0.0
        if dps and shares: total_payout += dps * shares
        if buybacks:       total_payout += buybacks
        payout_ratio = total_payout / ni

    roe_hist, nim_hist, bvps_hist, ppop_hist, cir_hist, loans_hist = [], [], [], [], [], []
    for row in series:
        lbl = (row.get("period") or "")[:4]
        ri_ni = row.get("net_income"); ri_eq = row.get("total_equity")
        ri_at = row.get("total_assets"); ri_rev = row.get("revenue")
        ri_bvps = row.get("book_value_per_share")
        ri_ii = row.get("interest_income"); ri_ie = row.get("interest_expense")
        ri_oe = row.get("operating_expense")
        ri_loans = row.get("loans_receivable") or row.get("loans_held_for_investment")

        roe_hist.append({"period": lbl,
                         "value": (ri_ni / ri_eq) if (ri_ni and ri_eq and ri_eq > 0) else None})
        nim_val = (ri_ii - abs(ri_ie)) / ri_at if (ri_ii is not None and ri_ie is not None and ri_at and ri_at > 0) else None
        nim_hist.append({"period": lbl, "value": nim_val})
        bvps_val = ri_bvps or ((ri_eq / shares) if (ri_eq and shares and shares > 0) else None)
        bvps_hist.append({"period": lbl, "value": round(bvps_val, 4) if bvps_val else None})
        ppop_val = _compute_ppop(row)
        ppop_hist.append({"period": lbl, "value": round(ppop_val, 0) if ppop_val else None})
        cir_val = (abs(ri_oe) / ri_rev) if (ri_oe and ri_rev and ri_rev > 0) else None
        cir_hist.append({"period": lbl, "value": round(cir_val, 4) if cir_val else None})
        loans_hist.append({"period": lbl, "value": round(ri_loans, 0) if ri_loans else None})

    return {
        "profile":              profile_name,
        "coe":                  coe,
        "target_roe":           target_roe,
        "target_cet1":          target_cet1,
        "fade_years":           cfg.get("fade_years"),
        "roe":                  roe,
        "roa":                  roa,
        "nim":                  bank_m.get("nim"),
        "efficiency_ratio":     bank_m.get("efficiency_ratio"),
        "credit_cost_ratio":    bank_m.get("credit_cost_ratio"),
        "tbv_per_share":        tbv_ps,
        "bvps":                 bvps,
        "total_equity":         equity,
        "total_assets":         assets,
        "fair_p_tbv":           round(fair_ptbv, 4) if fair_ptbv else None,
        "fair_value_per_share": fair_value,
        "cet1_ratio":           cet1,
        "cet1_buffer_bps":      cet1_buffer_bps,
        "cet1_surplus_usd":     cet1_surplus,
        "dividend_yield":       round(div_yield, 5) if div_yield else None,
        "buyback_yield":        round(buyback_yield, 5) if buyback_yield else None,
        "total_payout_ratio":   round(payout_ratio, 4) if payout_ratio else None,
        "dps":                  dps,
        "buybacks_usd":         buybacks,
        # Research-sourced — all null in backfill path (extractor not re-run)
        "npl_ratio":             None,
        "npl_coverage_ratio":    None,
        "net_charge_offs_pct":   None,
        "management_overlays_bn": None,
        "nim_rate_sensitivity_bps": None,
        "loan_growth_yoy":       None,
        "deposit_growth_yoy":    None,
        "loan_to_deposit_ratio": None,
        "forward_loan_growth_guidance": None,
        "forward_nim_guidance":  None,
        "research_evidence":     None,
        "roe_history":           roe_hist,
        "nim_history":           nim_hist,
        "bvps_history":          bvps_hist,
        "ppop_history":          ppop_hist,
        "cir_history":           cir_hist,
        "loans_history":         loans_hist,
    }


def backfill(
    dry_run: bool = True,
    target_ticker: str | None = None,
    force: bool = False,
) -> dict:
    api_key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if not api_key:
        return {"error": "FMP_API_KEY not set", "db_path": DB_PATH}

    result: dict = {
        "db_path":             DB_PATH,
        "dry_run":             dry_run,
        "target_ticker":       target_ticker,
        "force":               force,
        "rows_examined":       0,
        "patched":             0,
        "skipped_has_field":   0,
        "skipped_fetch_fail":  0,
        "skipped_no_shares":   0,
        "skipped_non_bank":    0,
        "details":             [],
    }

    conn = _get_archive_conn()

    # Bank ticker whitelist — pull every ticker from TICKER_SECTOR_LOOKUP +
    # SGX + HK lookups whose profile is in _BANK_PROFILE_CALIBRATION or whose
    # profile_name contains "Bank".
    from src.data.sector_profiles import (
        TICKER_SECTOR_LOOKUP, SGX_TICKER_SECTOR_LOOKUP,
    )
    try:
        from src.data.sector_profiles import HK_TICKER_SECTOR_LOOKUP as _HK_LOOKUP
    except ImportError:
        _HK_LOOKUP = {}

    def _is_bank_profile(profile: str) -> bool:
        return (profile in _BANK_PROFILE_CALIBRATION) or ("Bank" in profile) or (profile == "Mortgage/GSE")

    bank_whitelist: list[str] = []
    for lookup in (TICKER_SECTOR_LOOKUP, SGX_TICKER_SECTOR_LOOKUP, _HK_LOOKUP):
        for tkr, info in lookup.items():
            if isinstance(info, tuple) and len(info) >= 2 and info[0] == "Financials":
                if _is_bank_profile(info[1] or ""):
                    bank_whitelist.append(tkr)

    base_query = """
        SELECT run_id, ticker, run_at, sector, full_result_json
        FROM web_runs
        WHERE full_result_json IS NOT NULL
    """
    params: list[Any] = []
    if target_ticker:
        base_query += " AND ticker = ?"
        params.append(target_ticker.upper())
    else:
        placeholders = ",".join("?" * len(bank_whitelist)) or "NULL"
        base_query += f" AND (sector = 'Financials' OR ticker IN ({placeholders}))"
        params.extend(bank_whitelist)
    base_query += " ORDER BY run_at DESC"

    rows = list(conn.execute(base_query, params).fetchall())
    result["rows_examined"] = len(rows)
    if not rows:
        conn.close()
        return result

    # Lookup profile_name per ticker from TICKER_SECTOR_LOOKUP / SGX / HK
    def _profile_for_ticker(tkr: str) -> str | None:
        for lookup in (TICKER_SECTOR_LOOKUP, SGX_TICKER_SECTOR_LOOKUP, _HK_LOOKUP):
            info = lookup.get(tkr.upper())
            if isinstance(info, tuple) and len(info) >= 2 and info[0] == "Financials":
                profile = info[1] or ""
                if _is_bank_profile(profile):
                    return profile
        return None

    for row in rows:
        tkr = row["ticker"]
        run_id = row["run_id"]
        analysis_date = (row["run_at"] or "")[:10] or date.today().strftime("%Y-%m-%d")
        detail: dict[str, Any] = {
            "ticker": tkr,
            "run_id": run_id,
            "analysis_date": analysis_date,
            "archived_sector": row["sector"],
        }

        try:
            full_result = json.loads(row["full_result_json"])
        except (TypeError, ValueError) as exc:
            detail["status"] = "skip"; detail["reason"] = f"corrupt JSON: {exc}"
            result["details"].append(detail); continue

        data = full_result.get("data") or {}
        dcf_range = data.get("dcf_range") or {}
        dcf_dict = dcf_range.get(tkr) or dcf_range.get(tkr.upper())
        if not isinstance(dcf_dict, dict):
            detail["status"] = "skip"; detail["reason"] = "no data.dcf_range[ticker]"
            result["details"].append(detail); continue

        # Confirm this is actually a bank via profile lookup
        profile = _profile_for_ticker(tkr)
        if not profile:
            detail["status"] = "skip_non_bank"; detail["reason"] = f"not a bank (no profile match for {tkr})"
            result["skipped_non_bank"] += 1
            result["details"].append(detail); continue

        if not force and dcf_dict.get("bank_breakdown"):
            detail["status"] = "skip_has_field"
            result["skipped_has_field"] += 1
            result["details"].append(detail); continue

        shares = dcf_dict.get("shares_outstanding")
        if not shares or shares <= 0:
            for scen_key in ("base", "bull", "bear"):
                scen = dcf_dict.get(scen_key) or {}
                if isinstance(scen, dict) and scen.get("shares_outstanding"):
                    shares = scen["shares_outstanding"]; break
        if not shares or shares <= 0:
            detail["status"] = "skip_no_shares"
            result["skipped_no_shares"] += 1
            result["details"].append(detail); continue

        # Market cap isn't stored in the archive — estimate from shares × price
        # (price data not available in backfill path; set to None, capital return
        # yields will be null). Fresh runs capture live mcap; this backfill
        # exists purely to unblock UI rendering on archived runs.
        mcap = None

        breakdown = _build_bank_breakdown(tkr, analysis_date, float(shares), profile, mcap, api_key)
        if breakdown is None:
            detail["status"] = "skip_fetch_fail"
            result["skipped_fetch_fail"] += 1
            result["details"].append(detail); continue

        dcf_dict["bank_breakdown"] = breakdown
        dcf_range[tkr] = dcf_dict
        data["dcf_range"] = dcf_range
        full_result["data"] = data
        new_json = json.dumps(full_result)

        detail["profile"] = breakdown.get("profile")
        detail["fair_value"] = breakdown.get("fair_value_per_share")
        if dry_run:
            detail["status"] = "would_patch"
        else:
            conn.execute("UPDATE web_runs SET full_result_json = ? WHERE run_id = ?", (new_json, run_id))
            if row["sector"] != "Financials":
                conn.execute("UPDATE web_runs SET sector = 'Financials' WHERE run_id = ?", (run_id,))
                detail["sector_updated"] = True
            detail["status"] = "patched"
        result["patched"] += 1
        result["details"].append(detail)

    if not dry_run: conn.commit()
    conn.close()
    return result


def _cli_print(result: dict) -> int:
    if "error" in result:
        _log(f"{_RED}{result['error']}{_RESET}")
        return 2
    _log(f"{_BOLD}Bank breakdown backfill{_RESET}  (db={result['db_path']})")
    _log(f"  mode        : {'DRY RUN' if result['dry_run'] else 'LIVE'}")
    _log(f"  ticker      : {result['target_ticker'] or '(all banks)'}")
    _log(f"  force       : {'yes' if result['force'] else 'no'}")
    _log(f"  rows found  : {result['rows_examined']}")
    if result["rows_examined"] == 0:
        return 0
    for d in result["details"]:
        tag = f"{d['ticker']:10} @{d['analysis_date']} run={d['run_id'][:8]}"
        status = d["status"]
        if status in ("patched", "would_patch"):
            fv = f"${d['fair_value']:.2f}" if d.get('fair_value') else 'n/a'
            prefix = "[ OK ]" if status == "patched" else "[dry ]"
            _log(f"  {_GREEN if status == 'patched' else _DIM}{prefix}{_RESET} {tag}  {d.get('profile','?'):24s}  fair={fv}")
        elif status == "skip_has_field":
            continue
        else:
            _log(f"  {_YELLOW}[SKIP]{_RESET} {tag}  {d.get('reason', status)}")
    _log("")
    _log(f"{_BOLD}Summary{_RESET}")
    _log(f"  {_GREEN}patched          {result['patched']:4d}{_RESET}  "
         f"{'(dry run)' if result['dry_run'] else ''}")
    _log(f"  skipped (already had bank_breakdown) {result['skipped_has_field']:4d}")
    _log(f"  skipped (line-item fetch failed)     {result['skipped_fetch_fail']:4d}")
    _log(f"  skipped (no shares_outstanding)      {result['skipped_no_shares']:4d}")
    _log(f"  skipped (non-bank ticker)            {result['skipped_non_bank']:4d}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--ticker", type=str, default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    result = backfill(dry_run=args.dry_run, target_ticker=args.ticker, force=args.force)
    sys.exit(_cli_print(result))
