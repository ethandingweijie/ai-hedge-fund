"""
scripts/backfill_reit_breakdown.py
===================================
One-off migration that re-derives dcf_range.reit_breakdown for archived REIT
runs created before commit 2d4843b ("feat(reit): emit reit_breakdown on
dcf_range for REIT-specific UI panels").

Why
---
Before 2d4843b the DCF agent did not emit reit_breakdown. Historic REIT
run archives therefore lack the field, and the frontend's REIT panel
gate (``dcfRange?.reit_breakdown``) falls through to the generic ladder
even after the frontend is deployed. This script patches archived runs
in place so historic run_ids keep working.

How
---
Iterates ``ticker_signals`` rows where the associated run's sector is
"RealEstate" or "REIT" and the stored ``dcf_range_json`` has no
``reit_breakdown`` key. For each row:

  1. Re-fetch the last 7 annual line items for the ticker as of the
     run's ``analysis_date`` (same args as dcf_agent.py line 2699).
  2. Build the ``most_recent`` dict via ``_extract_annual_series``.
  3. Classify the REIT sub-type via ``_classify_reit_subtype``.
  4. Compute the REIT metrics via ``_compute_reit_metrics``.
  5. Reconstruct the ``reit_breakdown`` dict using the exact same
     logic as dcf_agent.py:3913–3991.
  6. UPDATE ``ticker_signals.dcf_range_json`` with the patched JSON.

Skips rows where the required line-item fields can't be fetched (e.g.
deprecated tickers, API outages). Logs a summary.

Usage
-----
    # Dry run — show what would change, make no DB writes
    python -m scripts.backfill_reit_breakdown --dry-run

    # Backfill one ticker
    python -m scripts.backfill_reit_breakdown --ticker DLR

    # Backfill all REITs
    python -m scripts.backfill_reit_breakdown

    # Force re-derive even when reit_breakdown already exists
    python -m scripts.backfill_reit_breakdown --force

Requires
--------
    FMP_API_KEY set in env (reads the same envvar as the live pipeline).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from typing import Any

# Make top-level `src` imports resolve when run as `python -m scripts.backfill_reit_breakdown`
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.run_archive import DB_PATH                              # noqa: E402
from src.tools.api import search_line_items                             # noqa: E402
from src.agents.analysis.dcf_agent import (                             # noqa: E402
    _REIT_SUBTYPE_MULTIPLES,
    _classify_reit_subtype,
    _compute_reit_metrics,
    _extract_annual_series,
)


# ── Logging ────────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

def _log(msg: str) -> None:
    print(msg, flush=True)


# ── Backfill logic — mirrors dcf_agent.py:3913–3991 exactly ───────────────

def _build_reit_breakdown(
    ticker: str,
    end_date: str,
    shares: float,
    api_key: str,
) -> dict | None:
    """
    Re-derive the reit_breakdown dict for one historic run.

    Returns None when line items can't be fetched or the series is empty.
    """
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
             "cash_and_equivalents"],
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

    # Classify sub-type — uses ticker + any notes attached (notes not
    # available on historic runs, so we fall back to ticker-only keyword match)
    subtype = _classify_reit_subtype(ticker, "")
    mults = _REIT_SUBTYPE_MULTIPLES.get(subtype, _REIT_SUBTYPE_MULTIPLES["default"])
    rm = _compute_reit_metrics(most_recent, subtype=subtype)

    total_debt = most_recent.get("total_debt")
    cash       = most_recent.get("cash_and_equivalents")
    dps_direct = most_recent.get("dividends_per_share")
    cap_rate_used = mults["cap_rate"]  # no research override in backfill path

    ffo_ps  = (rm["ffo"] / shares)  if (rm.get("ffo")  and shares and shares > 0) else None
    affo_ps = (rm["affo"] / shares) if (rm.get("affo") and shares and shares > 0) else None

    breakdown: dict[str, Any] = {
        "subtype":                      subtype,
        "ffo":                          rm.get("ffo"),
        "affo":                         rm.get("affo"),
        "noi":                          rm.get("noi"),
        "normalized_maintenance_capex": rm.get("normalized_maintenance_capex"),
        "maint_capex_pct":              rm.get("maint_capex_pct_used"),
        "total_debt":                   total_debt,
        "cash":                         cash,
        "shares":                       shares,
        "ffo_per_share":                round(ffo_ps, 4) if ffo_ps else None,
        "affo_per_share":               round(affo_ps, 4) if affo_ps else None,
        "dps":                          dps_direct,
        "cap_rate_used":                round(cap_rate_used, 5),
        "cap_rate_peer":                round(mults["cap_rate"], 5),
        "p_ffo_peer":                   mults["p_ffo"],
        "p_affo_peer":                  mults["p_affo"],
        "occupancy_rate":               None,  # research data not preserved in archive
        "wale_years":                   None,
        "leverage_ratio_research":      None,
        "subtype_mix":                  None,
        "geographic_mix":               None,
        "research_evidence":            None,
        "gross_asset_value":            None,
        "nav_total":                    None,
        "nav_per_share":                None,
    }

    # Bridge derivation — same math as dcf_agent.py
    if rm.get("noi") and rm["noi"] > 0 and cap_rate_used > 0:
        gav = rm["noi"] / cap_rate_used
        breakdown["gross_asset_value"] = round(gav, 0)
        if total_debt is not None and cash is not None and shares and shares > 0:
            nav = gav - total_debt + cash
            breakdown["nav_total"] = round(nav, 0)
            breakdown["nav_per_share"] = round(nav / shares, 2)

    # Historical series
    breakdown["npi_history"] = [
        {"period": (row.get("period") or "")[:4],
         "value":  round(row["ebitda"], 0) if row.get("ebitda") else None}
        for row in series
    ]
    breakdown["dpu_history"] = [
        {"period": (row.get("period") or "")[:4],
         "value":  round(row["dividends_per_share"], 4) if row.get("dividends_per_share") else None}
        for row in series
    ]
    return breakdown


# ── Main loop ──────────────────────────────────────────────────────────────

def backfill(
    dry_run: bool = True,
    target_ticker: str | None = None,
    force: bool = False,
) -> dict:
    """
    Core backfill logic. Returns a dict with per-row results + summary counts.
    Callable from the admin HTTP endpoint (app/backend/routes/admin.py) OR
    from the CLI (``python -m scripts.backfill_reit_breakdown``).

    Returns
    -------
    {
      "db_path":        str,
      "dry_run":        bool,
      "target_ticker":  str | None,
      "force":          bool,
      "rows_examined":  int,
      "patched":        int,
      "skipped_has_field": int,
      "skipped_fetch_fail": int,
      "skipped_no_shares": int,
      "details":        [{"ticker", "run_id", "analysis_date", "status",
                          "subtype"?, "nav_per_share"?, "reason"?}, ...],
    }
    """
    api_key = os.environ.get("FMP_API_KEY") or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    if not api_key:
        return {
            "error": "FMP_API_KEY not set",
            "db_path": DB_PATH,
        }

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
        "details":             [],
    }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find candidate rows. Join ticker_signals → runs so we get the sector and
    # analysis_date (= end_date used by the original pipeline run). Filter on
    # REIT/RealEstate.
    query = """
        SELECT ts.id, ts.run_id, ts.ticker, ts.dcf_range_json,
               r.sector, r.analysis_date
        FROM ticker_signals ts
        JOIN runs r ON r.run_id = ts.run_id
        WHERE (r.sector = 'RealEstate' OR r.sector = 'REIT')
          AND ts.dcf_range_json IS NOT NULL
    """
    params: list[Any] = []
    if target_ticker:
        query += " AND ts.ticker = ?"
        params.append(target_ticker.upper())
    query += " ORDER BY r.run_at DESC"

    rows = list(conn.execute(query, params).fetchall())
    result["rows_examined"] = len(rows)
    if not rows:
        conn.close()
        return result

    for row in rows:
        tkr = row["ticker"]
        run_id = row["run_id"]
        analysis_date = row["analysis_date"] or date.today().strftime("%Y-%m-%d")
        detail: dict[str, Any] = {
            "ticker": tkr,
            "run_id": run_id,
            "analysis_date": analysis_date,
        }

        try:
            dcf_dict = json.loads(row["dcf_range_json"])
        except (TypeError, ValueError) as exc:
            detail["status"] = "skip"
            detail["reason"] = f"corrupt JSON: {exc}"
            result["details"].append(detail)
            continue

        if not force and dcf_dict.get("reit_breakdown"):
            detail["status"] = "skip_has_field"
            result["skipped_has_field"] += 1
            result["details"].append(detail)
            continue

        # Pull shares from archived dcf_range (we need the same shares count
        # the original pipeline used to compute scenario_results).
        shares = dcf_dict.get("shares_outstanding")
        if not shares or shares <= 0:
            for scen_key in ("base", "bull", "bear"):
                scen = dcf_dict.get(scen_key) or {}
                if isinstance(scen, dict) and scen.get("shares_outstanding"):
                    shares = scen["shares_outstanding"]
                    break
        if not shares or shares <= 0:
            detail["status"] = "skip_no_shares"
            result["skipped_no_shares"] += 1
            result["details"].append(detail)
            continue

        breakdown = _build_reit_breakdown(tkr, analysis_date, float(shares), api_key)
        if breakdown is None:
            detail["status"] = "skip_fetch_fail"
            result["skipped_fetch_fail"] += 1
            result["details"].append(detail)
            continue

        dcf_dict["reit_breakdown"] = breakdown
        new_json = json.dumps(dcf_dict)

        detail["subtype"] = breakdown.get("subtype")
        detail["nav_per_share"] = breakdown.get("nav_per_share")

        if dry_run:
            detail["status"] = "would_patch"
        else:
            conn.execute(
                "UPDATE ticker_signals SET dcf_range_json = ? WHERE id = ?",
                (new_json, row["id"]),
            )
            detail["status"] = "patched"
        result["patched"] += 1
        result["details"].append(detail)

    if not dry_run:
        conn.commit()
    conn.close()
    return result


# ── CLI wrapper ────────────────────────────────────────────────────────────

def _cli_print(result: dict) -> int:
    """Pretty-print the backfill result for CLI consumers."""
    if "error" in result:
        _log(f"{_RED}{result['error']}{_RESET}")
        return 2

    _log(f"{_BOLD}REIT breakdown backfill{_RESET}  (db={result['db_path']})")
    _log(f"  mode        : {'DRY RUN - no writes' if result['dry_run'] else 'LIVE - will UPDATE ticker_signals'}")
    _log(f"  ticker      : {result['target_ticker'] or '(all REITs)'}")
    _log(f"  force       : {'yes - re-derive even when field exists' if result['force'] else 'no - skip already-backfilled rows'}")
    _log("")
    _log(f"{_DIM}Found {result['rows_examined']} candidate ticker_signals row(s){_RESET}")
    if result["rows_examined"] == 0:
        _log(f"{_YELLOW}Nothing to backfill.{_RESET}")
        return 0

    for d in result["details"]:
        tag = f"{d['ticker']:6} @{d['analysis_date']} run={d['run_id'][:8]}"
        status = d["status"]
        if status == "patched":
            nav = f"${d['nav_per_share']:.2f}" if d.get('nav_per_share') else 'n/a'
            _log(f"  {_GREEN}[ OK ]{_RESET} {tag}  patched      subtype={str(d.get('subtype','')):12}  NAV/sh={nav:>10}")
        elif status == "would_patch":
            nav = f"${d['nav_per_share']:.2f}" if d.get('nav_per_share') else 'n/a'
            _log(f"  {_DIM}[dry ]{_RESET} {tag}  would patch  subtype={str(d.get('subtype','')):12}  NAV/sh={nav:>10}")
        elif status == "skip_has_field":
            pass   # quiet — already done
        elif status == "skip_no_shares":
            _log(f"  {_YELLOW}[SKIP]{_RESET} {tag}  no shares_outstanding in archive")
        elif status == "skip_fetch_fail":
            _log(f"  {_YELLOW}[SKIP]{_RESET} {tag}  line items fetch failed")
        else:
            _log(f"  {_RED}[SKIP]{_RESET} {tag}  {d.get('reason','')}")

    _log("")
    _log(f"{_BOLD}Summary{_RESET}")
    _log(f"  {_GREEN}patched          {result['patched']:4d}{_RESET}  "
         f"{'(dry run - no DB changes)' if result['dry_run'] else ''}")
    _log(f"  skipped (already had reit_breakdown)   {result['skipped_has_field']:4d}")
    _log(f"  skipped (line-item fetch failed)       {result['skipped_fetch_fail']:4d}")
    _log(f"  skipped (no shares_outstanding)        {result['skipped_no_shares']:4d}")
    return 0 if result["patched"] > 0 or result["skipped_has_field"] > 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change; make no DB writes")
    p.add_argument("--ticker", type=str, default=None,
                   help="Backfill only this ticker (default: all REIT runs)")
    p.add_argument("--force", action="store_true",
                   help="Re-derive reit_breakdown even when the field already exists")
    args = p.parse_args()
    result = backfill(dry_run=args.dry_run, target_ticker=args.ticker, force=args.force)
    sys.exit(_cli_print(result))
