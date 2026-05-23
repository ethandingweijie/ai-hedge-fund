"""
Live calibration of the SW46 pipeline against Cassandra Unchained's published
ranking screenshot dated 2026-05-13. Invokes _process_ticker() on each ticker
and side-by-sides the output vs the published numbers.

Run with:
    $env:FMP_API_KEY = "<your-key>"
    .\.venv\Scripts\python.exe -m src.research_ideas.sw46._calibrate
"""
from __future__ import annotations

import logging
import sys

from src.research_ideas.sw46.runner import _process_ticker
from src.research_ideas.sw46.schemas import SW46TickerResult


# Cassandra's published ranking (8 tickers, sorted by composite score).
# Each entry: (cass_tier, cass_score, cass_price, cass_ivb_pct,
#              cass_iv15, cass_p15, cass_iv12, cass_p12, cass_iv18, cass_p18)
CASS = [
    ("PAYC",  "Stone",  58.0, 134.90, 11.3, 91.38, 1.48, 124.26, 1.09, 71.85, 1.88),
    ("FRSH",  "Chapel", 55.8,   8.20, 11.4,  5.65, 1.45,   7.65, 1.07,  4.49, 1.83),
    ("CRM",   "Chapel", 45.0, 165.84,  8.6, 69.81, 2.38,  96.02, 1.73, 54.78, 3.03),
    ("PCTY",  "Chapel", 42.3, 104.23,  7.3, 29.47, 3.54,  41.07, 2.54, 22.88, 4.56),
    ("MNDY",  "Stone",  34.4,  67.70, 12.8, 55.21, 1.23,  74.07, 0.91, 43.91, 1.54),
    ("NOW",   "Chapel", 32.2,  87.05, 10.0, 43.56, 2.00,  62.85, 1.39, 32.87, 2.65),
    ("HUBS",  "Stone",  15.9, 179.00,  5.7, 39.94, 4.48,  53.55, 3.34, 31.78, 5.63),
    ("WDAY",  "Stone",  11.3, 116.50,  5.2, 21.37, 5.45,  28.48, 4.09, 17.10, 6.81),
]


def _fmt_money(v: float | None) -> str:
    return f"${v:7.2f}" if v else "    N/A"


def _fmt_pct(v: float | None) -> str:
    return f"{v*100:5.1f}%" if v is not None else "  N/A "


def _fmt_mult(v: float | None) -> str:
    return f"{v:5.2f}x" if v is not None else " N/A "


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.stdout.reconfigure(encoding="utf-8")  # ensure Δ etc. render

    hdr = (
        f"{'TKR':6s} | {'Mine':<7s} {'Cass':<7s} | "
        f"{'MyScore':>7s} {'Cass':>5s} {'Diff':>5s} | "
        f"{'MyIV15':>8s} {'CassIV15':>8s} | "
        f"{'MyIVB':>7s} {'CassIVB':>7s} | "
        f"{'MyP/15':>7s} {'CassP/15':>8s}"
    )
    print(hdr)
    print("-" * len(hdr))

    for (tkr, cass_tier, cass_score, cass_price, cass_ivb, cass_iv15, cass_p15,
         _cass_iv12, _cass_p12, _cass_iv18, _cass_p18) in CASS:
        r = _process_ticker(tkr, history_years=7)
        if not isinstance(r, SW46TickerResult):
            print(f"{tkr:6s} | FAILED: {r}")
            continue
        d = r.model_dump()
        my_score = d["composite"]["total"]
        my_iv15 = d["iv15"]["iv15_per_share"]
        my_iv12 = (d["iv12"] or {}).get("iv15_per_share")
        my_iv18 = (d["iv18"] or {}).get("iv15_per_share")
        my_ivb = d["ivb_pct"]
        my_p15 = d["composite"]["p_iv15"]
        my_tier = d["aict"]["tier"]
        base_src = d["iv15"]["base_oe_source"]
        ta_tier = d["tragic_algebra"]["ta_tier"]
        pooled_de = d["tragic_algebra"]["pooled_delta_e"]
        base_oe = d["iv15"]["base_oe"]
        g1_5 = d["iv15"]["growth_year1_5"]

        tier_match = "OK" if my_tier == cass_tier else "!!"
        delta = my_score - cass_score

        print(
            f"{tkr:6s} | {my_tier:<7s} {cass_tier:<7s}{tier_match} | "
            f"{my_score:7.1f} {cass_score:5.1f} {delta:+5.1f} | "
            f"{_fmt_money(my_iv15)} {_fmt_money(cass_iv15)} | "
            f"{_fmt_pct(my_ivb)} {_fmt_pct(cass_ivb/100):>7s} | "
            f"{_fmt_mult(my_p15)} {_fmt_mult(cass_p15)}   src={base_src}"
        )
        de_str = f"{pooled_de:.2f}" if pooled_de is not None else "N/A"
        base_str = (
            f"${base_oe/1e9:.2f}B" if (base_oe is not None and abs(base_oe) >= 1e8)
            else (f"${base_oe/1e6:.0f}M" if base_oe is not None else "N/A")
        )
        g_str = f"{g1_5*100:.1f}%" if g1_5 is not None else "N/A"
        print(
            f"       | TA={ta_tier:<7s} pooled_dE={de_str:>6s}  base_OE={base_str:>7s}  "
            f"g1-5={g_str:>6s}"
        )


if __name__ == "__main__":
    main()
