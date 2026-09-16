"""Backward tests for the valuation-engine hardening items (plan: Backward tests).

The golden suite answers **"did the numbers move?"** This answers **"were the
moves right?"** Both are needed: the reinvestment charge passed the suite and
failed its backward test at 19.1pp of error against 4.4pp for legacy.

Each item in Phases 1-2 gets a row here. Running an item scores the SHIPPED
gate against ground truth that is already on file, and the acceptance result
decides whether the gate goes live (`applied: True`) or ships observation-only
(`applied: False`) and waits for the forward test.

    python scripts/backtest_valuation_fixes.py                 # every item
    python scripts/backtest_valuation_fixes.py --item 1.2A
    python scripts/backtest_valuation_fixes.py --item 1.2A --show-rows
    python scripts/backtest_valuation_fixes.py --as-of 2026-09-17

Results are written to `scratchpad/backtest_<item>.json` (gitignored: they
embed live-feed figures). The summary table and the accept/reject line are what
go into the commit message and the golden CHANGELOG entry.

**On `--as-of`.** Only items whose ground truth is a REPORTED figure can be
replayed at a past date, and only once the history exists: the live feed caps at
5 annual rows, so an as-of date more than ~4 years back has nothing to score
against. That is what Phase 3's EDGAR back-fill buys. Items that need it are
marked below rather than quietly scoring on a two-date sample and reporting a
pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "scratchpad"


# ── Item registry ───────────────────────────────────────────────────────────

def _run_1_2a(as_of: str, **_kw) -> dict[str, Any]:
    from src.memory.gate_backtest import backtest_balance_sheet_financial
    return backtest_balance_sheet_financial(as_of=as_of)


def _run_1_1(as_of: str, **_kw) -> dict[str, Any]:
    """Growth CAGR divergence + convergence fade.

    PENDING. The plan's row scores the faded schedule against realised revenue
    CAGR over the FOLLOWING years, which needs 8+ as-of dates per ticker to mean
    anything; the live feed supplies 1-2. Phase 3's EDGAR back-fill is the
    prerequisite, and until it lands this reports pending rather than passing on
    a two-date sample.

    Recorded here, and not merely omitted, because Phase 1.1 shipped
    `applied: True` on the owner's explicit specification of the bound — the
    gate is live and its backward test is not done. A registry that silently
    lacked the row would let that stay invisible.
    """
    return {
        "gate_id": "GATE_GROWTH_CAGR_DIVERGENCE",
        "as_of": as_of, "metric": "realised_revenue_cagr",
        "status": "pending",
        "accepted": None,
        "shipped_applied": True,
        "pending_reason": (
            "needs Phase 3 (scripts/ingest_edgar_history.py): the live feed "
            "caps history at 5 annual rows, giving 1-2 as-of dates per ticker "
            "against the 8+ the plan's sample column asks for. The gate is "
            "LIVE with applied=True on the owner's specification of the bound; "
            "this row is the outstanding obligation."),
    }


def _run_1_2b(as_of: str, **_kw) -> dict[str, Any]:
    """Cyclical peak-consensus routing to mid-cycle legs and P/B-ROE.

    PENDING, and shipped observation-only (`applied: False`) *because* it is
    pending — unlike 1.1, which shipped live on the owner's specification of the
    bound and carries its backward test as an outstanding obligation.

    Two things block scoring. The plan's row is MU FY2018 and FY2022, FCX 2021,
    NUE 2021, scored against realised price twelve months on; the live feed caps
    at five annual rows so none of those dates exist yet, and Phase 3's EDGAR
    back-fill is the prerequisite. Separately, historical CONSENSUS is not
    archived anywhere, so even with the history the trigger's input has to be
    proxied by realised next-year EPS — a perfect-foresight stand-in that
    *favours* path A, which is what makes the eventual test conservative rather
    than merely approximate.

    What could be measured now is recorded, because "the trigger discriminates"
    is a different claim from "the trigger is right" and only the first is
    available: on the golden fixtures MU fires both arms and FCX fires neither,
    separated by a factor of twenty on the same line rather than by a threshold
    tuned to land between them. MU's forward EPS of $156.08 is 10.1x its
    2x-max line ($15.48, from a five-year diluted max of $7.74) and 11.0x its
    mean-plus-two-sigma line ($14.25); FCX's $2.95 is 0.51x and 0.88x of the
    equivalent lines ($5.80 and $3.33). One name at a cycle top and one that is
    not.

    The figures are DILUTED EPS, because that is what the engine computes:
    `shares_outstanding` on a LineItem maps to FMP's `weightedAverageShsOutDil`.
    That is the consistent side to compare against - analyst consensus EPS is
    diluted too - but it means the basic-share numbers (MU's FY22 $7.81, FY25
    $7.65) give a 2x-max line of $15.62, not the $15.48 the run publishes.
    `tests/test_cyclical_peak_consensus.py` pins the published lines so the two
    cannot drift apart unnoticed.

    None of this says whether the substitution improves the estimate, which is
    what scoring is for.
    """
    return {
        "gate_id": "GATE_CYCLICAL_PEAK_CONSENSUS",
        "as_of": as_of, "metric": "realised_price_12m",
        "status": "pending",
        "accepted": None,
        "shipped_applied": False,
        "pending_reason": (
            "needs Phase 3 (scripts/ingest_edgar_history.py) for the as-of dates "
            "the plan names - MU FY2018/FY2022, FCX 2021, NUE 2021 - and needs a "
            "proxy for historical consensus, which is not archived at all. "
            "Shipped OBSERVATION-ONLY (applied=False) rather than live: the "
            "shipping rule requires >=10 scoreable firings and this row has zero. "
            "Both paths are recorded per run so the forward test can score it "
            "without a replay."),
        "discrimination_observed": {
            "MU": {"fired": True, "arms": ["multiple-of-max", "mean-plus-sigma"],
                   "eps_forward": 156.08, "max_line": 15.48, "sigma_line": 14.25,
                   "eps_max_5y": 7.74},
            "FCX": {"fired": False, "arms": [],
                    "eps_forward": 2.95, "max_line": 5.80, "sigma_line": 3.33,
                    "eps_max_5y": 2.90},
            "note": ("golden-fixture inputs, not live feed; DILUTED EPS, matching "
                     "what the engine computes and what consensus EPS is quoted "
                     "on. Shows the two arms separate a cycle top from an "
                     "ordinary year by a wide margin; says nothing about whether "
                     "the substitution improves the estimate, which is what "
                     "scoring is for."),
        },
    }


#: item key -> (description, runner, plan's backward-test row)
ITEMS: dict[str, tuple[str, Callable[..., dict], str]] = {
    "1.1": (
        "Growth: flat g_ntm -> gated + faded schedule",
        _run_1_1,
        "realised revenue CAGR over following years; alpha profiles plus a "
        "non-alpha control; 1-2 dates/ticker now, 8+ after Phase 3",
    ),
    "1.2A": (
        "Balance-sheet financials lose EV/DCF/FCF legs",
        _run_1_2a,
        "classifier precision/recall on a hand-labelled set (~20 "
        "deposit/float-funded vs ~20 not)",
    ),
    "1.2B": (
        "Cyclicals: peak consensus -> mid-cycle legs / P/B-ROE",
        _run_1_2b,
        "realised price 12m later; MU FY2018 and FY2022, FCX 2021, NUE 2021; "
        "needs Phase 3, and realised next-year EPS stands in for consensus "
        "(a perfect-foresight proxy that favours path A)",
    ),
}


# ── Reporting ───────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], pct: bool = True) -> str:
    if v is None:
        return "-"
    return f"{v:.1%}" if pct else f"{v:.4f}"


def print_summary(results: dict[str, dict], show_rows: bool) -> int:
    print()
    print("=" * 100)
    print(f"{'item':<6} {'gate':<34} {'metric':<28} {'result':<10} verdict")
    print("-" * 100)
    exit_code = 0
    for item, res in results.items():
        gid = res.get("gate_id", "?")
        metric = res.get("metric", "?")
        if res.get("status") == "pending":
            verdict = "PENDING"
            detail = {True: "LIVE - backward test owed",
                      False: "observation-only"}.get(
                          res.get("shipped_applied"), "ship state not recorded")
            exit_code = 1
        elif res.get("accepted"):
            verdict = "ACCEPT"
            detail = ""
        elif res.get("accepted") is None:
            verdict = "NO DATA"
            detail = "; ".join(res.get("reject_reasons") or [])
            exit_code = 1
        else:
            verdict = "REJECT"
            detail = "; ".join(res.get("reject_reasons") or [])
            exit_code = 1
        print(f"{item:<6} {gid:<34} {metric:<28} {verdict:<10} {detail}")
    print("=" * 100)

    for item, res in results.items():
        if res.get("status") == "pending":
            # `shipped_applied` distinguishes the two kinds of pending, and the
            # distinction is the whole point of the row: an item that shipped
            # LIVE with its test owed is an open risk in production, while an
            # item that shipped observation-only is inert until someone promotes
            # it. Reading both as "not done yet" hides which one needs chasing.
            shipped = res.get("shipped_applied")
            state = {True: "shipped LIVE (applied=True)",
                     False: "shipped OBSERVATION-ONLY (applied=False)"}.get(
                         shipped, "ship state not recorded")
            print(f"\n[{item}] PENDING — {state}")
            print(f"    {res.get('pending_reason')}")
            if res.get("discrimination_observed"):
                print("    measured while pending (not a score):")
                for t, d in res["discrimination_observed"].items():
                    if not isinstance(d, dict):
                        print(f"      note: {d}")
                        continue
                    print(f"      {t:<6} fired={str(d['fired']):<5} "
                          f"arms={'+'.join(d['arms']) or '-':<32} "
                          f"fwd EPS {d['eps_forward']:.2f} vs 2x-max "
                          f"{d['max_line']:.2f} / mean+2sig {d['sigma_line']:.2f}")
            continue
        if res.get("metric") != "classifier_precision_recall":
            continue
        print(f"\n[{item}] confusion matrix over {res['scored']} scored labels "
              f"({res['no_data']} could not be measured):")
        print(f"  TP={res['tp']}  FP={res['fp']}  TN={res['tn']}  FN={res['fn']}"
              f"   firings={res['firings']}")
        print(f"  precision={_fmt(res['precision'])} (bar "
              f"{_fmt(res['bar']['precision'])})   "
              f"recall={_fmt(res['recall'])} (bar {_fmt(res['bar']['recall'])})")

        if show_rows:
            print(f"\n  {'ticker':<10} {'profile':<32} {'label':<6} {'fired':<6} "
                  f"{'ratio':>7}  outcome")
            for r in res["rows"]:
                ratio = r.get("customer_balance_ratio")
                ratio_s = "-" if ratio is None else f"{ratio:.4f}"
                print(f"  {r['ticker']:<10} {r['profile']:<32} "
                      f"{str(r['label']):<6} {str(r['fired']):<6} "
                      f"{ratio_s:>7}  {r['outcome']}")

        for r in res["rows"]:
            if r["outcome"] in ("FP", "FN"):
                print(f"\n  {r['outcome']} {r['ticker']} ({r['profile']}): "
                      f"label={r['label']} fired={r['fired']} "
                      f"ratio={r.get('customer_balance_ratio')}")
                if r.get("note"):
                    print(f"      note: {r['note']}")
            if r.get("routing_drift"):
                print(f"\n  ROUTING DRIFT {r['ticker']}: {r['routing_drift']}")
    return exit_code


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", action="append", choices=sorted(ITEMS),
                    help="run only these items (default: all)")
    ap.add_argument("--as-of", default="2026-09-17",
                    help="as-of date for the balance-sheet readings")
    ap.add_argument("--show-rows", action="store_true",
                    help="print every labelled row, not just the errors")
    ap.add_argument("--no-write", action="store_true",
                    help="do not write scratchpad/backtest_<item>.json")
    args = ap.parse_args(argv)

    wanted = args.item or sorted(ITEMS)
    results: dict[str, dict] = {}
    for item in wanted:
        desc, runner, _row = ITEMS[item]
        print(f"\n── {item}: {desc} " + "─" * max(0, 60 - len(desc)))
        res = runner(as_of=args.as_of)
        results[item] = res
        if not args.no_write:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUT_DIR / f"backtest_{item.replace('.', '_')}.json"
            path.write_text(json.dumps(res, indent=2, default=str),
                            encoding="utf-8")
            print(f"   wrote {path.relative_to(ROOT)}")

    return print_summary(results, args.show_rows)


if __name__ == "__main__":
    raise SystemExit(main())
