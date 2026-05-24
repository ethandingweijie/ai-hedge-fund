"""
Trigger a full Complacency cohort run and print a summary.

Usage:
    $env:FMP_API_KEY = "<key>"
    .\.venv\Scripts\python.exe -m src.research_ideas.complacency._run_full_cohort
"""
import logging
import sys

from src.research_ideas.complacency.runner import run_complacency


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    r = run_complacency(max_workers=4, save=True, auto_refresh_sectors=False)

    print()
    print("=" * 80)
    print(f"Complacency cohort run_id: {r.run_id}")
    print(f"Tickers scored OK: {len(r.results)}    Failed: {len(r.failed_tickers)}")
    print(f"Gate passers     : {r.gate_passers}")
    print()
    print("TOP 12 by composite (highest complacency):")
    print(f"{'#':<4}{'TKR':<7}{'Sector':<26}{'Verdict':<14}{'C/8':<6}"
          f"{'V':<4}{'B':<4}{'T':<4}{'Q':<4}{'EV/S':<7}{'sector':<8}{'rel':<6}")
    print("-" * 80)
    for t in r.results[:12]:
        sec = (t.sector or "")[:24]
        ev = f"{t.ev_sales:.1f}×" if t.ev_sales is not None else "—"
        med = f"{t.ev_sales_sector_median:.1f}×" if t.ev_sales_sector_median is not None else "—"
        rel = f"{t.ev_sales_relative:.2f}×" if t.ev_sales_relative is not None else "—"
        print(
            f"{t.rank or 0:<4}{t.ticker:<7}{sec:<26}{t.verdict:<14}"
            f"{t.composite:<6.1f}"
            f"{t.val_score:<4.0f}{t.beh_score:<4.0f}{t.tech_score:<4.0f}{t.qual_score:<4.0f}"
            f"{ev:<7}{med:<8}{rel:<6}"
        )

    print()
    print("BOTTOM 5 (cleanest):")
    for t in r.results[-5:]:
        sec = (t.sector or "")[:24]
        print(f"{t.rank or 0:<4}{t.ticker:<7}{sec:<26}{t.verdict:<14}{t.composite:<6.1f}")

    if r.failed_tickers:
        print()
        print("FAILED:")
        for f in r.failed_tickers:
            print("  ", f)

    print()
    print("=" * 80)
    print("SAMPLE JUSTIFICATIONS:")
    for t in r.results[:3]:
        print(f"\n  #{t.rank}  {t.ticker} ({t.composite:.1f}/8):")
        if t.justification:
            print(f"     {t.justification}")


if __name__ == "__main__":
    main()
