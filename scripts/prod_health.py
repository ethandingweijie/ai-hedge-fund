"""Run the production health report from a shell (read-only).

    export DATABASE_URL=...            # the prod Postgres, to read production
    python scripts/prod_health.py      # prints every check
    python scripts/prod_health.py --json

No Redis here, so the worker/queue checks read "unknown"; /admin/diag and the daily job include them.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitoring.prod_health import compute_health, format_text  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    rep = compute_health(hours=a.hours)
    print(json.dumps(rep, indent=1, default=str) if a.json else format_text(rep, only_problems=False))
    return 0 if rep["status"] in ("ok", "unknown") else 1


if __name__ == "__main__":
    sys.exit(main())
