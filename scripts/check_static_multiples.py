"""Re-peg trigger for the static peer multiples (owner rule, 2026-09-21).

A static multiple is a fallback for a field the live comps cannot resolve. It
is a dated reading, not a constant: "define an explicit re-pegging trigger --
re-benchmark if the live median shifts by >= +/-15% over a 30-day moving window
-- rather than locking a hard-coded static point indefinitely."

For every profile that an industry row routes to and that has a static row,
this compares each static field with the mean of the basket's live medians
over the last 30 days (`regional_comps_history`, source `refresh`, one row per
weekly refresh) and reports every breach. It PROPOSES and never writes: a
static multiple changes only when the owner has seen the derivation.

    python scripts/check_static_multiples.py                 # local store
    DATABASE_URL=<prod proxy url> python scripts/check_static_multiples.py
    python scripts/check_static_multiples.py --profile "IPP" --threshold 0.15

Weekly refresh rows accumulate in production; a local store usually holds only
the annual backfill, in which case the current median stands in and the row
says so (`window: current median only`). Exit code 1 when anything breaches.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=False)

from src.data import db as _db  # noqa: E402
from src.data.industry_profile_map import industry_map  # noqa: E402
from src.data.sector_profiles import HK_SECTOR_PEER_MULTIPLES, SECTOR_PEER_MULTIPLES  # noqa: E402

FIELDS = ("ev_ebitda", "pe", "ev_revenue", "pb", "fcf_yield")
TABLES = {"US": SECTOR_PEER_MULTIPLES, "HKSE": HK_SECTOR_PEER_MULTIPLES}


def live_window(exchange: str, industry: str, field: str, days: int) -> tuple[float | None, str]:
    """(mean of the refresh medians in the window, how it was read)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = _db.query(
        "SELECT value, peer_count FROM regional_comps_history WHERE exchange = ? AND level = 'industry' "
        "AND key = ? AND field = ? AND source = 'refresh' AND as_of >= ? ORDER BY peer_count DESC",
        [exchange, industry, field, since])
    vals = [float(r["value"]) for r in rows if r["value"] is not None]
    if vals:
        return sum(vals) / len(vals), f"{len(vals)} refresh median(s) in {days}d"
    cur = _db.query(
        "SELECT value FROM regional_comps WHERE exchange = ? AND level = 'industry' AND key = ? "
        "AND field = ? ORDER BY peer_count DESC LIMIT 1", [exchange, industry, field])
    if cur and cur[0]["value"] is not None:
        return float(cur[0]["value"]), "current median only"
    return None, "no basket"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.15)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--profile", default=None)
    a = ap.parse_args()
    by_profile: dict[str, list[str]] = {}
    for industry, (_sector, profile) in industry_map().items():
        by_profile.setdefault(profile, []).append(industry)
    print(f"database: {'postgres' if _db.is_postgres() else _db.get_db_path()}   "
          f"threshold +/-{a.threshold:.0%} over {a.days}d\n")
    print(f"{'market':6} {'profile':34} {'industry':32} {'field':11} {'static':>8} {'live':>8} {'move':>7}  window")
    breaches = 0
    for market, table in TABLES.items():
        for profile, static in table.items():
            if (a.profile and profile != a.profile) or profile not in by_profile:
                continue
            for industry in sorted(by_profile[profile]):
                for field in FIELDS:
                    s = static.get(field)
                    live, how = live_window(market, industry, field, a.days)
                    if not s or live is None:
                        continue
                    move = live / s - 1.0
                    flag = "  RE-PEG" if abs(move) >= a.threshold else ""
                    breaches += bool(flag)
                    if flag or a.profile:
                        print(f"{market:6} {profile[:34]:34} {industry[:32]:32} {field:11} {s:8.3f} {live:8.3f} "
                              f"{move:+7.1%}  {how}{flag}")
    print(f"\n{breaches} static field(s) past the trigger. Proposals only -- nothing was written.")
    return 1 if breaches else 0


if __name__ == "__main__":
    raise SystemExit(main())
