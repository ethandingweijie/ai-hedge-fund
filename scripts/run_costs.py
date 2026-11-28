"""Measured LLM + search cost per run type vs the credit weights (pricing 2026-11-28).

    python scripts/run_costs.py              # last 30 days, this machine's DB (DATABASE_URL for prod PG)
    python scripts/run_costs.py --days 7 --json

Prod without a DB connection: GET /admin/diag?costs=30 with the X-Admin-Secret header.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    from src.llm import cost_meter
    r = cost_meter.cost_report(a.days)
    if a.json:
        print(json.dumps(r, indent=2, default=str))
        return 0
    print(f"Run costs, last {r['days']} days: {r['runs']} completed runs"
          f" (usage reported on {r['usage_coverage'] if r['usage_coverage'] is not None else '-'} of calls)")
    print(f"{'run type':10s} {'credits':>7s} {'runs':>5s} {'median':>9s} {'p90':>9s} {'max':>9s} {'p90/credit':>11s}")
    for t, v in r["by_run_type"].items():
        print(f"{t:10s} {str(v['credits'] or '-'):>7s} {v['runs']:>5d} {v['median_usd']:>9.4f} {v['p90_usd']:>9.4f} "
              f"{v['max_usd']:>9.4f} {str(v['p90_usd_per_credit'] or '-'):>11s}")
    if r["plans_at_full_use"]:
        print("\nPlan at full use, every credit at the worst measured p90 cost per credit:")
        for pid, p in r["plans_at_full_use"].items():
            print(f"  {pid:13s} ${p['price']:>3} {p['credits']:>3} credits  COGS ${p['worst_case_cogs_usd']:>6.2f}"
                  f"  margin {p['worst_case_margin'] if p['worst_case_margin'] is not None else '-'}")
    print(f"\nOutside runs (screens, chat, what-if...): ${r['unattributed_usd_total']:.2f}")
    for m, usd in sorted(r["unattributed_usd_by_model"].items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {m:40s} ${usd:.4f}")
    if r["unpriced"]:
        print("\nUNPRICED (tokens recorded, no price in cost_meter.PRICES):", ", ".join(r["unpriced"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
