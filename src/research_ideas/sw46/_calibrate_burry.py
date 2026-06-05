"""
Calibration check of the SW50 (formerly SW46) pipeline against Burry /
Cassandra-Unchained's 2026-06 write-ups (the 8 names the user supplied:
ADBE, INTU, ADSK, U, ZS, PANW, CRWD, DOCU). Runs the FULL cohort (save=True so
it doubles as the persist step), then side-by-sides the 8 reference names plus
the two newly-added names (FROG, IOT).

Run with (FMP_API_KEY must be set in the shell env):
    .\.venv\Scripts\python.exe -m src.research_ideas.sw46._calibrate_burry
"""
from __future__ import annotations

import logging
import sys

from src.research_ideas.sw46.runner import run_sw46


# Burry 2026-06 write-ups: ticker -> (tier, pooled_dE_pct or None for N/A).
BURRY = {
    "ADBE": ("Chapel", 88.3),
    "INTU": ("Castle", 75.4),
    "ADSK": ("Chapel", 103.5),
    "NOW":  ("Chapel", -78.5),  # net diluter -> deeply negative (TT*)
    "U":    ("Castle", None),   # ΔE N/A (negative window)
    "ZS":   ("Chapel", None),   # ΔE N/A
    "PANW": ("Castle", None),   # ΔE negative -> N/A
    "CRWD": ("Castle", None),   # ΔE not computable -> N/A
    "DOCU": ("Stone",  None),   # ΔE negative -> N/A
}
NEW_NAMES = ["FROG", "IOT"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    r = run_sw46(history_years=10, max_workers=4, save=True)
    by_ticker = {t.ticker: t for t in r.results}

    print()
    print("=" * 92)
    print(f"SW50 cohort run_id={r.run_id}  tickers_ok={len(r.results)}  failed={len(r.failed_tickers)}")
    pooled = f"{r.pooled_delta_e*100:.2f}%" if r.pooled_delta_e is not None else "N/A"
    print(f"Cohort pooled ΔE = {pooled}")
    print("=" * 92)

    hdr = (f"{'TKR':6s} | {'MyTier':<8s}{'Burry':<8s}{'':3s}| "
           f"{'MyΔE':>7s} {'BurryΔE':>8s} | {'TA':<8s} | {'Rank':>4s} {'Score':>6s} {'P/IV15':>7s}")
    print(hdr)
    print("-" * len(hdr))

    def row(tkr, burry_tier=None, burry_de=None):
        t = by_ticker.get(tkr)
        if t is None:
            print(f"{tkr:6s} | (not in results — failed or missing)")
            return
        my_tier = t.aict.tier
        de = t.tragic_algebra.pooled_delta_e
        de_s = f"{de*100:.1f}%" if de is not None else "N/A"
        bde_s = f"{burry_de:.1f}%" if burry_de is not None else "N/A"
        bt_s = burry_tier or "—"
        match = "" if burry_tier is None else (" OK " if my_tier == burry_tier else " !! ")
        p15 = f"{t.composite.p_iv15:.2f}x" if t.composite.p_iv15 is not None else "N/A"
        print(f"{tkr:6s} | {my_tier:<8s}{bt_s:<8s}{match:3s}| "
              f"{de_s:>7s} {bde_s:>8s} | {t.tragic_algebra.ta_tier:<8s} | "
              f"{t.rank or 0:>4d} {t.composite.total:>6.1f} {p15:>7s}")

    for tkr, (bt, bde) in BURRY.items():
        row(tkr, bt, bde)
    print("-" * len(hdr))
    for tkr in NEW_NAMES:
        row(tkr)

    if r.failed_tickers:
        print("\nFAILED:")
        for f in r.failed_tickers:
            print("  ", f)


if __name__ == "__main__":
    main()
