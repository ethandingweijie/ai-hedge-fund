"""Run the dynamic multiples update now, against whichever database DATABASE_URL
points at (production when set, the local store when not).

The quarterly worker task runs this automatically after the comps history
backfill (2nd of Jan/Apr/Jul/Oct, 05:00 UTC). This script is for the first
population and for any manual re-run. Dry run by default: it prints what each
basket WOULD get and writes nothing.

    python scripts/run_dynamic_multiples_update.py                  # dry run, local
    python scripts/run_dynamic_multiples_update.py --commit         # write, local
    # production (proxy host; see docs/dynamic_multiples_handoff.md):
    DATABASE_URL=<prod proxy url> python scripts/run_dynamic_multiples_update.py --commit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=False)

from src.data import dynamic_multiples as dm  # noqa: E402
from src.data import db as _db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--markets", default="US,HKSE,SES")
    ap.add_argument("--trigger", default="manual")
    a = ap.parse_args()
    markets = tuple(m.strip() for m in a.markets.split(",") if m.strip())
    where = "PRODUCTION (postgres)" if _db.is_postgres() else f"local sqlite {_db.get_db_path()}"
    print(f"database: {where}")
    if not a.commit:
        for ex in markets:
            n = 0
            for level, key, field in dm._basket_fields(ex):
                if not field:
                    continue
                p = dm.propose_industry(ex, level, key, field)
                if p:
                    n += 1
                    if n <= 15:
                        print(f"  {ex:5s} {level:8s} {key[:38]:40s} {field:15s} "
                              f"baseline {p['baseline']:6.2f} -> {p['proposed']:6.2f}  "
                              f"market {p['market_now']:6.2f}")
            print(f"{ex}: {n} baskets would get a multiple")
        print("dry run -- nothing written; pass --commit")
        return 0
    s = dm.update_all(markets, trigger=a.trigger)
    print(json.dumps(s["markets"], indent=1))
    print(json.dumps(dm.log_report()["coverage"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
