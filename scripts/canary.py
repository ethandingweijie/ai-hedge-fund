"""Run the nightly valuation canary from a shell (read-only; never records a row).

    python scripts/canary.py                 # golden replay on THIS checkout + live drift on the DB in DATABASE_URL
    python scripts/canary.py --no-golden     # live drift only
    python scripts/canary.py --hours 168 --json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.monitoring.canary import run_canary  # noqa: E402
from src.monitoring.prod_health import format_text  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-golden", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    rep = run_canary(golden=not a.no_golden, hours=a.hours)
    if a.json:
        print(json.dumps(rep, indent=1, default=str))
    else:
        counts = {s: sum(1 for c in rep["checks"] if c["status"] == s) for s in ("ok", "warn", "fail", "unknown")}
        print(format_text(dict(rep, counts=counts), only_problems=False).replace("Prod health", "Canary", 1))
    return 0 if rep["status"] in ("ok", "unknown") else 1


if __name__ == "__main__":
    sys.exit(main())
