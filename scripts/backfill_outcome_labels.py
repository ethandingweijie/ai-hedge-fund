"""Label every matured run_features row with its realised price outcome.

DRY RUN BY DEFAULT: prints what would be labelled (the plan, from the database alone)
and then the sweep's report computed without writing. Pass --write to write.

    python scripts/backfill_outcome_labels.py                 # plan + dry-run sweep
    python scripts/backfill_outcome_labels.py --plan-only     # no price fetches at all
    python scripts/backfill_outcome_labels.py --write         # label for real

It is the daily sweep (valuation_outcomes.score_matured) run once by hand: the
prediction is read from run_features, so runs whose archive rows (runs / ticker_signals)
are gone are labelled too; one label per ticker per day; names that stopped trading are
recorded as terminal. Idempotent -- a second --write adds nothing.

The database is whatever src.data.db resolves to (DATABASE_URL set = PostgreSQL). Writing
to PostgreSQL also needs --i-mean-production, so a stray --write never reaches prod.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: Optional[list[str]] = None, *, closes_fn: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="write labels (default: dry run)")
    ap.add_argument("--plan-only", action="store_true", help="print the plan; fetch no prices")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--i-mean-production", action="store_true",
                    help="required with --write when DATABASE_URL is set")
    args = ap.parse_args(argv)

    from src.data import db as _db
    from src.memory import valuation_outcomes as vo

    if args.write and _db.is_postgres() and not args.i_mean_production:
        print("Refusing: DATABASE_URL is set (PostgreSQL). Add --i-mean-production to write.")
        return 2
    today = date.fromisoformat(args.today) if args.today else None

    plan = vo.label_plan(today=today)
    print("PLAN " + json.dumps(plan, indent=2, sort_keys=True))
    if args.plan_only:
        return 0
    report = vo.score_matured(today=today, closes_fn=closes_fn, write=args.write)
    print(("WRITTEN " if args.write else "DRY RUN (nothing written) ")
          + json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
