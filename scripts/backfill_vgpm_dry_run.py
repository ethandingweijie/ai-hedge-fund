"""
backfill_vgpm_dry_run.py — local invocation of the VGPM backfill.

Two modes:
  python scripts/backfill_vgpm_dry_run.py                   # dry-run only
  python scripts/backfill_vgpm_dry_run.py --apply           # actually write
  python scripts/backfill_vgpm_dry_run.py --since 2026-05-01

Operates on the LOCAL src/data/run_archive.db. To backfill production data
on Railway, use the admin endpoint:

  curl -X POST "https://your-app/admin/vgpm-backfill?secret=...&dry_run=false"

Prints a per-run summary plus per-ticker grade transitions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make src importable when invoked from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.backfill_vgpm import (   # noqa: E402
    DEFAULT_SINCE_ISO,
    backfill_vgpm_for_runs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="VGPM backfill (local)")
    parser.add_argument("--db", default=None,
                        help="Path to run_archive.db; defaults to module-resolved location")
    parser.add_argument("--since", default=DEFAULT_SINCE_ISO,
                        help=f"ISO-8601 cutoff (default: {DEFAULT_SINCE_ISO})")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write the changes back (default: dry-run only)")
    parser.add_argument("--samples", type=int, default=50,
                        help="Max grade-change samples to display (default 50)")
    args = parser.parse_args()

    result = backfill_vgpm_for_runs(
        db_path=args.db,
        since_iso=args.since,
        dry_run=not args.apply,
        sample_limit=args.samples,
    )

    if not result.get("ok"):
        print(f"FAILED: {result.get('error', '?')}")
        return 1

    mode = "DRY RUN" if result["dry_run"] else "APPLIED"
    print(f"=== VGPM backfill — {mode} ===")
    print(f"  DB: {result['db_path']}")
    print(f"  since: {result['since_iso']}")
    print()
    print(f"  Runs examined:        {result['runs_examined']:>6}")
    print(f"  Runs with VGPM:       {result['runs_with_vgpm']:>6}")
    print(f"  Runs that would update: {result['runs_updated']:>6}")
    print(f"  Tickers updated:      {result['tickers_updated']:>6}")
    print(f"  Tickers skipped:      {result['tickers_skipped']:>6}")
    print(f"  Errors:               {len(result['errors']):>6}")
    print()

    if result["grade_changes"]:
        print(f"=== Grade transitions (showing up to {len(result['grade_changes'])}) ===")
        for chg in result["grade_changes"]:
            print(f"  {chg['ticker']:<8} ({chg['run_at'][:10]})")
            for dim in ("valuation", "growth", "profitability", "momentum"):
                before = chg["before"].get(dim, "—")
                after  = chg["after"].get(dim,  "—")
                if before != after:
                    print(f"    {dim:<14} {before:>4}  →  {after:<4}")
        print()

    if result["errors"]:
        print("=== Errors ===")
        for e in result["errors"][:10]:
            print(f"  {e}")

    if not args.apply and result["runs_updated"]:
        print("Re-run with --apply to persist these changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
