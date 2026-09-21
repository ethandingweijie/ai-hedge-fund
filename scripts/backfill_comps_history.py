"""Reconstruct yearly basket medians -- command-line wrapper.

The logic lives in src/data/comps_history_backfill.py, which the production
worker also runs quarterly. This wrapper is for manual and dry runs.

    python scripts/backfill_comps_history.py                        # dry run, US
    python scripts/backfill_comps_history.py --exchange HKSE --commit
    python scripts/backfill_comps_history.py --preset energy --commit
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=False)

from src.data import comps_history_backfill as bf  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--exchange", default="US")
    ap.add_argument("--preset", choices=["energy"], default=None)
    ap.add_argument("--quiet", action="store_true", help="skip the per-series print")
    a = ap.parse_args()
    if a.commit:
        print(bf.run_market(a.exchange, a.preset))
        return 0
    rows = bf.build(a.exchange, a.preset)
    by = defaultdict(list)
    for r in rows:
        by[(r["key"], r["cohort"], r["field"])].append(r)
    for (key, cohort, field), rs in sorted(by.items()):
        if a.quiet or field not in ("ev_ebitda", "ev_ebitda_norm", "pe_norm", "roic"):
            continue
        series = "  ".join(f"{r['as_of'][:4]}:{r['value']:.3f}(n={r['peer_count']})" for r in rs)
        print(f"{key[:34]:36s}{cohort:6s}{field:15s}{series}")
    print(f"{len(rows)} basket-year medians -- dry run, pass --commit to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
