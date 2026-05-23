"""
Trigger a full SW46 cohort run, persist results, and print a summary.

Usage:
    $env:FMP_API_KEY = "<key>"
    .\.venv\Scripts\python.exe -m src.research_ideas.sw46._run_full_cohort
"""
import logging
import sys

from src.research_ideas.sw46.runner import run_sw46


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    r = run_sw46(history_years=7, max_workers=4, save=True)

    print()
    print("=" * 78)
    print(f"SW46 cohort run_id: {r.run_id}")
    print(
        "Pooled ΔE: "
        + (f"{r.pooled_delta_e * 100:.2f}%" if r.pooled_delta_e is not None else "N/A")
    )
    print(f"Tickers scored OK: {len(r.results)}   Failed: {len(r.failed_tickers)}")
    print()
    print("TOP 12 by composite score:")
    print(f"{'#':<4}{'TKR':<7}{'AICT':<10}{'TA':<10}{'Score':<8}{'IVB':<8}{'P/IV15':<8}{'Verdict':<12}")
    print("-" * 78)
    for i, t in enumerate(r.results[:12]):
        ivb_s = f"{t.ivb_pct * 100:.1f}%" if t.ivb_pct is not None else "  N/A"
        p15_s = f"{t.composite.p_iv15:.2f}x" if t.composite.p_iv15 is not None else "N/A"
        v = (
            "Fat Pitch" if t.composite.total >= 60
            else "Watch" if t.composite.total >= 45
            else "Not Close" if t.composite.total >= 25
            else "Avoid" if t.composite.total >= 10
            else "Stay Away"
        )
        print(
            f"{t.rank or (i + 1):<4}{t.ticker:<7}{t.aict.tier:<10}"
            f"{t.tragic_algebra.ta_tier:<10}{t.composite.total:<8.1f}"
            f"{ivb_s:<8}{p15_s:<8}{v:<12}"
        )

    print()
    print("BOTTOM 5:")
    for t in r.results[-5:]:
        ivb_s = f"{t.ivb_pct * 100:.1f}%" if t.ivb_pct is not None else "  N/A"
        p15_s = f"{t.composite.p_iv15:.2f}x" if t.composite.p_iv15 is not None else "N/A"
        print(
            f"{t.rank:<4}{t.ticker:<7}{t.aict.tier:<10}"
            f"{t.tragic_algebra.ta_tier:<10}{t.composite.total:<8.1f}"
            f"{ivb_s:<8}{p15_s:<8}"
        )

    if r.failed_tickers:
        print()
        print("FAILED:")
        for f in r.failed_tickers:
            print("  ", f)

    # Print justification for the top-3 and bottom-3
    print()
    print("=" * 78)
    print("SAMPLE JUSTIFICATIONS:")
    sample = list(r.results[:3]) + list(r.results[-3:])
    for t in sample:
        print(f"  #{t.rank}  {t.ticker}: {t.justification}")


if __name__ == "__main__":
    main()
