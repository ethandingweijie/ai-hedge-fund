"""Propose (and, on the owner's word, record) dynamic segment multiples.

    python scripts/calibrate_dynamic_multiples.py                   # propose all
    python scripts/calibrate_dynamic_multiples.py refining          # one type
    python scripts/calibrate_dynamic_multiples.py --backtest        # + leave-one-out
    python scripts/calibrate_dynamic_multiples.py refining --accept --reviewer owner

Proposing never changes a valuation. `--accept` is the owner's command: it writes
the proposal into valuation_constants.json under `segment_multiples`, and only
then does the segment SOTP use it. A multiple the engine picked without an owner
seeing its derivation would be exactly the silent number this exists to end.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=False)

from src.data import dynamic_multiples as dm  # noqa: E402


def show(p: dict) -> None:
    b, rr, q = p["betas"], p["real_rate"], p["roic"]
    band = p["band"]
    print(f"\n== {p['segment_type']}   [{p['mode']}]   basket: {p['basket']}"
          + ("  (PROXY -- no basket of its own)" if p["basket_is_proxy"] else ""))
    print(f"   baseline {p['baseline']:.2f}x"
          + (f"   owner band {band[0]:.1f}-{band[1]:.1f}x" if band else "   no owner band"))
    if p["market_multiple_now"] is not None:
        print(f"   market through-cycle multiple now: {p['market_multiple_now']:.2f}x")
    print(f"   real rate {rr['now']:.2%} vs 5y mean {rr['mean_5y']:.2%} "
          f"(delta {rr['delta']*1e4:+.0f}bp)  [{rr['source']}]"
          if rr["now"] is not None else "   real rate: unavailable")
    if q["now"] is not None and q["window_mean"] is not None:
        print(f"   basket ROIC {q['now']:.1%} vs window mean {q['window_mean']:.1%} "
              f"(delta {q['delta']*100:+.1f}pp)")
    ols = b.get("ols") or {}
    print(f"   b1 {b['b1']:+.2f}  b2 {b['b2']:+.2f}   (n={b['n']}, shrink weight "
          f"{b['shrink_weight']:.2f}, prior b1 {b['prior']['b1']:+.1f} b2 {b['prior']['b2']:+.1f}"
          + (f", OLS b1 {ols['b1']:+.2f} b2 {ols['b2']:+.2f} R2 {ols['r2']:.2f}"
             if ols and ols.get('r2') is not None else ", OLS: n/a")
          + f")  -- {b['note']}")
    print(f"   terms: rate {p['terms']['rate']:+.3f}  roic {p['terms']['roic']:+.3f}  "
          f"-> raw {p['raw']:.2f}x")
    if p.get("crack"):
        c = p["crack"]
        if c.get("now") is not None:
            print(f"   3-2-1 crack ${c['now']:.2f}/bbl vs long-run ${c['long_run_mean']:.2f} "
                  f"({c['spike']:+.0%})")
    for r in p["rules"]:
        print(f"   RULE  {r}")
    for f in p["flags"]:
        print(f"   FLAG  {f}")
    print(f"   PROPOSED {p['proposed']:.2f}x")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("types", nargs="*")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--reviewer", default="owner")
    ap.add_argument("--backtest", action="store_true")
    a = ap.parse_args()
    types = a.types or list(dm.SEGMENT_BASELINES)
    for t in types:
        p = dm.propose(t)
        show(p)
        if a.backtest:
            bt = dm.backtest(t)
            if bt["mae_static"] is not None:
                print(f"   backtest ({bt['n']} folds): static baseline error "
                      f"{bt['mae_static']:.1%} vs dynamic {bt['mae_dynamic']:.1%}  "
                      f"-- {bt['note']}")
        if a.accept:
            e = dm.accept(p, a.reviewer)
            print(f"   RECORDED {e['multiple']:.2f}x by {a.reviewer} on {e['accepted_at']}")
    if not a.accept:
        print("\n(proposals only -- pass --accept to record)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
