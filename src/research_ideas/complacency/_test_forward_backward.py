"""
Forward + backward test for the qualitative + aggregate scoring.

Forward:   live-score CRWD via Qwen, verify qualitative populates
Backward:  re-score CRWD, verify cache HIT (no second Qwen call)

Usage:
    $env:FMP_API_KEY = "..."
    $env:DEEP_RESEARCH_API_KEY = "..."
    .\.venv\Scripts\python.exe -m src.research_ideas.complacency._test_forward_backward
"""
import logging
import sys
import time

from src.research_ideas.complacency.runner import score_one_ticker, _compute_aggregate
from src.research_ideas.complacency.schemas import ComplacencyTickerResult


def _print_result(r: ComplacencyTickerResult, label: str):
    print(f"\n{'=' * 78}")
    print(f"{label}: {r.ticker} ({r.name})")
    print(f"  Verdict       : {r.verdict}")
    print(f"  Quant         : {r.composite:.1f}/8   pillars V={r.val_score} B={r.beh_score} T={r.tech_score} Q={r.qual_score}")
    if r.qualitative:
        q = r.qualitative
        print(f"  Qual          : {q.composite}/{q.max_possible}   ({q.composite_normalized*100:.0f}%)")
        print(f"  Conviction    : {q.conviction_label}")
        print(f"  Indicators    : {len(q.indicators)}")
        for code, s in q.indicators.items():
            print(f"    - {code:<35s} {s.score}/5   conf {s.confidence:.0%}   '{s.summary[:80]}'")
        print(f"  Incomplete    : {q.incomplete}")
    else:
        print(f"  Qual          : <none>")
    print(f"  AGGREGATE     : {r.aggregate_score} / 100   "
          f"(quant {r.aggregate_quant_pts} + qual {r.aggregate_qual_pts})")
    print(f"  Put rec       : {'yes' if r.put_recommendation else 'no'}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ticker = sys.argv[1] if len(sys.argv) > 1 else "CRWD"

    # Invalidate the per-indicator cache so the forward test actually re-scores.
    from app.backend.services import qualitative_storage
    import sqlite3
    db_path = qualitative_storage._get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM complacency_qualitative WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
    finally:
        conn.close()
    print(f"[Cache cleared for {ticker} so forward test re-scores]")

    # ── FORWARD: cold run ────────────────────────────────────────────────
    print(f"\n[Forward test] live-scoring {ticker} (cold, will hit Qwen)…")
    t0 = time.time()
    r1 = score_one_ticker(ticker)
    dt1 = time.time() - t0
    if not isinstance(r1, ComplacencyTickerResult):
        print(f"FAILED forward: {r1}")
        return
    _print_result(r1, "FORWARD (live Qwen)")
    print(f"\n[Forward took {dt1:.1f}s]")

    # ── BACKWARD: warm run, should hit 7-day per-indicator cache ─────────
    print(f"\n[Backward test] re-scoring {ticker} (warm — Qwen should NOT be called)…")
    t0 = time.time()
    r2 = score_one_ticker(ticker)
    dt2 = time.time() - t0
    if not isinstance(r2, ComplacencyTickerResult):
        print(f"FAILED backward: {r2}")
        return
    _print_result(r2, "BACKWARD (cache hit)")
    print(f"\n[Backward took {dt2:.1f}s]")

    # ── Verify cache effectiveness ───────────────────────────────────────
    print(f"\n{'=' * 78}")
    print("VERIFICATION")
    print(f"  Forward duration  : {dt1:5.1f}s   (Qwen call expected)")
    print(f"  Backward duration : {dt2:5.1f}s   (cache hit expected — should be < forward by ~5-15s)")
    if r1.qualitative and r2.qualitative:
        same_scores = all(
            r1.qualitative.indicators[k].score == r2.qualitative.indicators[k].score
            for k in r1.qualitative.indicators
            if k in r2.qualitative.indicators
        )
        print(f"  Scores match      : {same_scores}")
        print(f"  Both aggregates   : forward={r1.aggregate_score}  backward={r2.aggregate_score}  match={r1.aggregate_score == r2.aggregate_score}")
    else:
        print("  ⚠ One run produced no qualitative — cache test inconclusive")


if __name__ == "__main__":
    main()
