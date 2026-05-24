"""Dump all pillar inputs for a set of tickers from the persisted cohort."""
import sys
from app.backend.services.complacency_storage import get_latest_complacency_run

sys.stdout.reconfigure(encoding="utf-8")

TICKERS = sys.argv[1:] or ["MSTR", "NET", "NVDA", "PLTR", "RBLX", "TSLA"]

run = get_latest_complacency_run()
results = {r["ticker"]: r for r in run["results"]}

# ─── Per-ticker pillar breakdown ─────────────────────────────────────
for tkr in TICKERS:
    r = results.get(tkr)
    if not r:
        print(f"\n{tkr}: not in cohort universe.")
        continue

    print(f"\n{'='*70}")
    print(f"{tkr} — {r['name']}  ({r['sector']})  [Verdict: {r['verdict']}]")
    print(f"  Composite {r['composite']}/8   gate_passed={r['passes_gate']}")

    # Valuation pillar
    val = r["val_score"]
    print(f"\n  VALUATION (score: {val}/2)")
    ev = r.get("ev_sales")
    med = r.get("ev_sales_sector_median")
    rel = r.get("ev_sales_relative")
    fcf = r.get("fcf_yield_ttm")
    print(f"    EV/Sales     {ev:>8.2f}× " if ev is not None else "    EV/Sales     N/A")
    print(f"    sector med   {med:>8.2f}× " if med is not None else "    sector med   N/A")
    print(f"    rel ratio    {rel:>8.2f}× " + ("  STRONG (>2.5×)" if rel and rel > 2.5 else "  weak (>1.5×)" if rel and rel > 1.5 else "  no flag") if rel is not None else "    rel          N/A")
    print(f"    EV/S abs            " + ("  STRONG (>25×)" if ev and ev > 25 else "  weak (>15×)" if ev and ev > 15 else "  no abs flag"))
    print(f"    FCF yield TTM {fcf*100:>7.2f}% " + ("  STRONG (<-0.5%)" if fcf and fcf < -0.005 else "  weak (<1.5%)" if fcf and fcf < 0.015 else "  no flag") if fcf is not None else "    FCF yield    N/A")

    # Behavioural
    beh = r["beh_score"]
    print(f"\n  BEHAVIOURAL (score: {beh}/2)")
    ad = r.get("ad_ratio_4q_avg")
    rp = r.get("range_position")
    epsr = r.get("eps_revision_yoy")
    print(f"    A/D 4Q       {ad:>8.2f}  " + ("  STRONG (<0.20)" if ad is not None and ad < 0.20 else "  weak (<0.35)" if ad is not None and ad < 0.35 else "  no flag") if ad is not None else "    A/D          N/A")
    print(f"    range_pos    {rp*100:>7.1f}% " if rp is not None else "    range_pos    N/A")
    print(f"    EPS rev yoy  {epsr*100:>7.1f}% " if epsr is not None else "    EPS rev      N/A")
    if rp is not None and epsr is not None:
        if rp > 0.90 and epsr < 0:
            print(f"    → range>90% + EPS rev<0  STRONG")
        elif rp > 0.85 and epsr < 0.05:
            print(f"    → range>85% + EPS rev<5%  weak")

    # Technical
    tech = r["tech_score"]
    print(f"\n  TECHNICAL (score: {tech}/2)")
    sma_ext = r.get("sma200_extension")
    rsi = r.get("rsi_weekly")
    if sma_ext is not None:
        mark = "  STRONG (>40%)" if sma_ext > 0.40 else "  weak (>20%)" if sma_ext > 0.20 else "  no flag"
        print(f"    SMA200 ext  {sma_ext*100:>+7.1f}% " + mark)
    if rsi is not None:
        mark = "  STRONG (>75)" if rsi > 75 else "  weak (>65)" if rsi > 65 else "  no flag"
        print(f"    Weekly RSI  {rsi:>7.1f}   " + mark)

    # Quality
    qual = r["qual_score"]
    print(f"\n  QUALITY (score: {qual}/2)")
    az = r.get("altman_z")
    pio = r.get("piotroski")
    if az is not None:
        if az < 1.81:    mark = "  STRONG (<1.81 distress)"
        elif az > 50:     mark = "  STRONG (>50 mkt-cap-decoupled)"
        elif az < 3:      mark = "  weak (<3 grey zone)"
        elif az > 25:     mark = "  weak (>25 elevated)"
        else:             mark = "  no flag"
        print(f"    Altman Z    {az:>7.2f}   " + mark)
    if pio is not None:
        mark = "  STRONG (<=3)" if pio <= 3 else "  weak (<=5)" if pio <= 5 else "  no flag"
        print(f"    Piotroski   {pio:>7}/9   " + mark)
