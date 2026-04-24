"""
scripts/backfill_profile_name.py
=================================
Backfills the profile_name column (and inner full_result_json.data.profile_name)
on existing web_runs rows. Unblocks re-extract for historic runs archived
before the strategic_router profile pre-classification feature landed (v2.0).

Why
---
Pre-v2.0 runs stored `data.sector` but not `data.profile_name`. This meant:
  1. Admin table UI has no sub-sector column to filter by.
  2. Re-extract helper's sector-extractor gate rejects the row even though
     the ticker IS in TICKER_SECTOR_LOOKUP as e.g. Growth SaaS.

What it does
------------
For each non-checkpoint web_runs row with NULL profile_name:
  1. Parse full_result_json.
  2. Extract profile_name via the same resolution tree _save_web_run uses:
       state.data.profile_name → profile_names[ticker] → TICKER_SECTOR_LOOKUP
  3. If resolved, UPDATE two things in one transaction:
       a) web_runs.profile_name         — column (for fast filtering)
       b) full_result_json.data.profile_name — JSON blob (for extractors)
     This keeps the column and the JSON consistent so downstream consumers
     (re-extract helper, frontend, reports) all see the same value.

Usage
-----
    # Dry run — show what would change, make no DB writes
    python -m scripts.backfill_profile_name --dry-run

    # Backfill one ticker
    python -m scripts.backfill_profile_name --ticker DDOG

    # Backfill all rows with NULL profile_name
    python -m scripts.backfill_profile_name

    # Force re-derive even when profile_name is already set (uses latest
    # TICKER_SECTOR_LOOKUP values)
    python -m scripts.backfill_profile_name --force
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Optional

# Make top-level `src` imports resolve
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.backend.services.analysis_service import (       # noqa: E402
    _get_db_path,
    _ensure_web_runs_table,
    _extract_web_run_summary,
)


_RESET = "\033[0m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED = "\033[31m"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_profile_for_row(row: sqlite3.Row) -> tuple[Optional[str], str]:
    """Derive the profile_name for one row. Returns (profile, source).

    source ∈ {"state", "profile_names_map", "ticker_lookup", ""}
    """
    ticker = row["ticker"]
    try:
        full = json.loads(row["full_result_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return (None, "")

    # Reuse the same resolver that _save_web_run uses — single source of truth.
    _, _, _, profile = _extract_web_run_summary(full, ticker)

    if not profile:
        return (None, "")

    # Identify which path resolved it (for visibility in the backfill log)
    data = full.get("data", {})
    if (data.get("profile_name") or "").strip() == profile:
        return (profile, "state")
    pmap = data.get("profile_names") or {}
    if isinstance(pmap, dict) and any((v or "").strip() == profile for v in pmap.values()):
        return (profile, "profile_names_map")
    return (profile, "ticker_lookup")


def backfill(
    dry_run: bool = True,
    target_ticker: Optional[str] = None,
    force: bool = False,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Backfill profile_name column + inner JSON field. Returns summary.

    Params:
      dry_run       — True (default): inspect without writing
      target_ticker — limit to one ticker (case-insensitive)
      force         — also re-derive rows where profile_name is already set
      db_path       — optional DB path override
    """
    # Ensure the profile_name column exists before querying / writing — the
    # ALTER TABLE migration may not have run yet on a freshly-deployed cloud
    # DB because _ensure_web_runs_table() is only called from _save_web_run.
    # Explicit call here is idempotent (ALTER is gated on PRAGMA table_info).
    _ensure_web_runs_table()

    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # Base filter: non-checkpoint rows
        where = "(is_checkpoint = 0 OR is_checkpoint IS NULL)"
        params: list = []

        if target_ticker:
            where += " AND UPPER(ticker) = ?"
            params.append(target_ticker.upper())

        if not force:
            where += " AND (profile_name IS NULL OR profile_name = '')"

        rows = conn.execute(
            f"SELECT run_id, run_at, ticker, sector, profile_name, full_result_json "
            f"FROM web_runs WHERE {where} ORDER BY run_at DESC",
            params,
        ).fetchall()

        _log(f"{_CYAN}Scanning {len(rows)} rows{_RESET}"
             f"{' (forced)' if force else ' with NULL profile_name'}...")

        summary: dict[str, Any] = {
            "scanned": len(rows),
            "would_update": 0,
            "updated": 0,
            "unresolved": 0,
            "by_source": {"state": 0, "profile_names_map": 0, "ticker_lookup": 0},
            "by_profile": {},
            "dry_run": dry_run,
            "rows": [],
        }

        for row in rows:
            resolved, source = _resolve_profile_for_row(row)
            if not resolved:
                summary["unresolved"] += 1
                _log(f"  {_YELLOW}? {row['ticker']:6s}  {row['run_id']}  "
                     f"(no profile in state, not in lookup){_RESET}")
                summary["rows"].append({
                    "run_id": row["run_id"],
                    "ticker": row["ticker"],
                    "resolved": None,
                    "source": "",
                })
                continue

            summary["by_source"][source] = summary["by_source"].get(source, 0) + 1
            summary["by_profile"][resolved] = summary["by_profile"].get(resolved, 0) + 1

            was_empty = not (row["profile_name"] or "").strip()
            needs_update = force or was_empty

            if dry_run:
                summary["would_update"] += 1
                _log(
                    f"  {_GREEN}+ {row['ticker']:6s}{_RESET}  "
                    f"{row['run_id']}  "
                    f"{_CYAN}{resolved:35s}{_RESET} "
                    f"({source})"
                )
                summary["rows"].append({
                    "run_id": row["run_id"],
                    "ticker": row["ticker"],
                    "resolved": resolved,
                    "source": source,
                    "was_empty": was_empty,
                })
                continue

            if needs_update:
                # Patch both the column AND the inner JSON in one tx so the
                # re-extract helper's sector-gate and the admin UI display
                # stay consistent.
                try:
                    full = json.loads(row["full_result_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    full = {}
                data = full.get("data") or {}
                data["profile_name"] = resolved
                pnmap = data.get("profile_names") or {}
                if isinstance(pnmap, dict):
                    pnmap[row["ticker"]] = resolved
                    data["profile_names"] = pnmap
                full["data"] = data

                conn.execute(
                    "UPDATE web_runs SET profile_name = ?, full_result_json = ? "
                    "WHERE run_id = ?",
                    (resolved, json.dumps(full, default=str), row["run_id"]),
                )
                summary["updated"] += 1
                _log(
                    f"  {_GREEN}✓ {row['ticker']:6s}{_RESET}  "
                    f"{row['run_id']}  "
                    f"{_CYAN}{resolved:35s}{_RESET} "
                    f"({source})"
                )
                summary["rows"].append({
                    "run_id": row["run_id"],
                    "ticker": row["ticker"],
                    "resolved": resolved,
                    "source": source,
                    "was_empty": was_empty,
                })

        if not dry_run:
            conn.commit()

        return summary
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill profile_name on web_runs rows"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Inspect without writing (default when flag set)")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ap.add_argument("--ticker", type=str, default=None,
                    help="Limit to one ticker (case-insensitive)")
    ap.add_argument("--force", action="store_true",
                    help="Re-derive even when profile_name is already set")
    ap.set_defaults(dry_run=True)

    args = ap.parse_args()

    _log(f"{_CYAN}backfill_profile_name — dry_run={args.dry_run} "
         f"ticker={args.ticker or 'all'} force={args.force}{_RESET}")

    result = backfill(
        dry_run=args.dry_run,
        target_ticker=args.ticker,
        force=args.force,
    )

    _log("")
    _log("=" * 70)
    _log(f"Summary  scanned={result['scanned']}  "
         f"{'would_update' if args.dry_run else 'updated'}="
         f"{result['would_update'] if args.dry_run else result['updated']}  "
         f"unresolved={result['unresolved']}")
    _log(f"  By source: {dict(result['by_source'])}")
    _log(f"  By profile: {dict(result['by_profile'])}")
    _log("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
