"""Quarterly review of the owner-set valuation constants.

Reads the benchmark feed, applies the formula, the inertia rule and the
tolerance band, and prints what it would change. It does not change anything
unless `--accept` is passed, and `--accept` is the owner's to run: a valuation
constant that moves on a schedule without a decision is not owner-set.

    # the quarterly check -- proposes only
    python scripts/calibrate_valuation_constants.py

    # after reading the proposal
    python scripts/calibrate_valuation_constants.py --accept --reviewer owner

Cadence: once per reporting cycle (mid-February, May, August, November), plus
out-of-cycle whenever the 10-year Treasury moves 75bps or the benchmark index
re-rates 100bps from the values recorded at the last review. Both triggers are
evaluated here and reported whether or not the quarter has ended.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)

from src.data import valuation_constants as vc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", action="store_true",
                    help="record the proposal (owner decision)")
    ap.add_argument("--reviewer", default="owner")
    ap.add_argument("--profile", default=None, help="one profile, default all")
    ap.add_argument("--index-yield", type=float, default=None,
                    help="override the feed, as a decimal (0.0749)")
    ap.add_argument("--coverage", type=float, default=None,
                    help="override the sector coverage factor")
    a = ap.parse_args()

    doc = vc.load()
    profiles = [a.profile] if a.profile else sorted((doc.get("profiles") or {}).keys())
    if not profiles:
        print("no profiles authored")
        return 1

    ten_year = vc.fetch_ten_year()
    rc = 0
    for profile in profiles:
        e = vc.entry(profile, doc)
        if not e:
            print(f"! {profile}: not authored")
            rc = 1
            continue
        bd = e.get("benchmark_detail") or {}
        symbol = bd.get("proxy_symbol") or "AMLP"

        if a.index_yield is not None:
            feed = {"symbol": symbol, "yield": a.index_yield, "annual": None,
                    "price": None, "latest_date": "override"}
        else:
            feed = vc.fetch_index_yield(symbol)
        if not feed:
            print(f"! {profile}: no benchmark feed for {symbol}")
            rc = 1
            continue

        p = vc.calibrate(profile, index_yield=feed["yield"], coverage_factor=a.coverage,
                         ten_year=ten_year, doc=doc)
        cov = p["inputs"]["coverage_factor"]
        print(f"\n== {profile}")
        print(f"   benchmark  {symbol} yield {feed['yield']:.2%}"
              + (f" (4 distributions = {feed['annual']:.2f} on {feed['price']:.2f},"
                 f" latest {feed['latest_date']})" if feed.get("annual") else " (override)"))
        print(f"   formula    {feed['yield']:.2%} x {cov:.2f} coverage = {p['raw_candidate']:.2%}")
        band = p["tolerance_band"]
        print(f"   band       {band[0]:.2%}-{band[1]:.2%}"
              + ("  ** RAW CANDIDATE OUTSIDE BAND -- the band needs a decision, "
                 "not the number **" if p["outside_band"] else ""))
        print(f"   current    {p['current']:.2%}" if p["current"] else "   current    none")
        if p["held_by_inertia"]:
            print(f"   proposed   {p['proposed']:.2%}  (held: move {p['move_bps']}bps "
                  f"< {p['inertia_bps']}bps inertia)")
        else:
            print(f"   proposed   {p['proposed']:.2%}  (move {p['move_bps']}bps)")
        if ten_year is not None:
            print(f"   10-year    {ten_year:.2%}")
        print(f"   review due {p['review_due']}"
              + (f" -- {'; '.join(p['review_reasons'])}" if p["review_reasons"] else ""))

        if a.accept:
            if not p["changed"]:
                print("   accepted   nothing to change; review clock reset")
            e2 = vc.apply_proposal(p, reviewer=a.reviewer, doc=doc)
            print(f"   RECORDED   {e2['target_dcf_yield']:.4%} by {a.reviewer}, "
                  f"next review {e2['effective_until']}")
        else:
            print("   (proposal only -- pass --accept to record)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
