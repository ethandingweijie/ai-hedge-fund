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
        "pending_reason": (
            "needs Phase 3 (scripts/ingest_edgar_history.py): the live feed "
            "caps history at 5 annual rows, giving 1-2 as-of dates per ticker "
            "against the 8+ the plan's sample column asks for. The gate is "
            "LIVE with applied=True on the owner's specification of the bound; "
            "this row is the outstanding obligation."),
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
            detail = ""
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
            print(f"\n[{item}] PENDING: {res.get('pending_reason')}")
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
