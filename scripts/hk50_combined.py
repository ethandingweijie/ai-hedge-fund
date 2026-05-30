"""
HK50 consolidated screener board.

One row per name on the ADR-prioritized routing (report BABA, not 9988.HK, where a
comprehensive US ADR exists; else AKShare+yfinance on the HK ticker).

Table columns (the leaderboard view)
-------------------------------------
  S/N            — rank by aggregate score (descending).
  Stock Name     — display name + the ticker we report on.
  Agg Score      — the stock's LEADING screen score: max(Growth, Dividend), tagged
                   (G) or (D). Each screen score is itself an aggregate of 5 metrics.
  IV15           — 15-yr owner-earnings intrinsic value @ 15% RR (quote currency),
                   AICT-modulated for SW/Internet names only, else neutral Gordon.
  Stock Price    — current quote (same currency as IV15).
  AICT           — AICT tier (Fortress/Castle/Chapel/Stone/Wood) for SW/Internet;
                   "—" for every other sector (AICT not applicable).
  Key Metrics    — compact headline drivers of the LEADING screen:
                     Growth  -> Rev 3y CAGR · Fwd EPS growth · PEG
                     Dividend-> Yield · 5y Div CAGR · Payout
                   The full 5-metric breakdown for BOTH screens, plus the IV15
                   build-up, lives in the per-ticker card (see print_card()).

Run: .venv\\Scripts\\python.exe -m scripts.hk50_combined            (full board)
     .venv\\Scripts\\python.exe -m scripts.hk50_combined --cards    (board + sample cards)
"""
from __future__ import annotations

import sys

# adr_report imports gap_analysis (which wraps stdout once, idempotently).
from scripts.hk50_adr_report import (  # noqa: E402
    HK50, route, backfill_from_hk_listing,
)
from scripts.hk50_gap_analysis import (  # noqa: E402
    resolve, score_growth, score_div, _wrap_stdout_utf8,
)
from scripts.hk50_iv15 import (  # noqa: E402
    build_inputs, stage1_growth, iv15, piv_score,
    AICT_TIER, TIER_PARAMS, NEUTRAL_PARAMS,
)

END_DATE = "2026-05-30"

# Screener sub-metric weights (for the ticker-card breakdown).
GROWTH_WEIGHTS = {"revenue_3y_cagr": 30, "eps_fwd_growth": 25, "fcf_growth": 20,
                  "op_income_growth": 15, "peg": 10}
DIV_WEIGHTS = {"dividend_yield": 30, "dividend_5y_cagr": 25, "payout_ratio": 20,
               "fcf_coverage": 15, "yield_vs_5yr_avg": 10}
# (label, formatter) per metric.
GROWTH_FMT = {
    "revenue_3y_cagr": ("Revenue 3y CAGR", "pct+"),
    "eps_fwd_growth":  ("Fwd EPS growth",  "pct+"),
    "fcf_growth":      ("FCF growth",      "pct+"),
    "op_income_growth":("Op-income growth","pct+"),
    "peg":             ("PEG",             "num"),
}
DIV_FMT = {
    "dividend_yield":   ("Dividend yield",   "pct1"),
    "dividend_5y_cagr": ("Dividend 5y CAGR", "pct+"),
    "payout_ratio":     ("Payout ratio",     "pct0"),
    "fcf_coverage":     ("FCF coverage",     "x"),
    "yield_vs_5yr_avg": ("Yield vs 5y avg",  "x"),
}


def _fmt(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct+":
        return f"{v*100:+.0f}%"
    if kind == "pct1":
        return f"{v*100:.1f}%"
    if kind == "pct0":
        return f"{v*100:.0f}%"
    if kind == "x":
        return f"{v:.1f}x"
    return f"{v:.1f}"  # num


def compute_iv15(hk_ticker: str, rep: str) -> dict:
    """Full IV15 build-up for a routed name (Nones where not meaningful)."""
    if hk_ticker in AICT_TIER:
        tier = AICT_TIER[hk_ticker]
        haircut, mult, wA = TIER_PARAMS[tier]
        label = tier
    else:
        haircut, mult, wA = NEUTRAL_PARAMS
        label = "—"
    out = {"aict": label, "haircut": haircut, "mult": mult, "wA": wA, "currency": "?",
           "E0": None, "g1": None, "ivA": None, "ivB": None,
           "iv": None, "price": None, "piv": None, "vpts": None}
    try:
        inp = build_inputs(rep)
    except Exception:
        return out
    out["currency"] = inp.get("currency", "?")
    out["price"] = inp.get("price")
    E0 = inp["E0"]
    if E0 is None or E0 <= 0:
        return out
    g1 = stage1_growth(inp)
    ivA, ivB, iv = iv15(E0, g1, haircut, mult, wA)
    price = inp["price"]
    piv = (price / iv) if (price and iv and iv > 0) else None
    out.update(E0=E0, g1=g1, ivA=ivA, ivB=ivB, iv=iv, price=price, piv=piv,
               vpts=piv_score(piv) if piv is not None else None)
    return out


def key_metrics_str(lead: str, r: dict) -> str:
    """Compact headline drivers of the leading screen — for the table row."""
    if lead == "Growth":
        return (f"Rev3y {_fmt(r['revenue_3y_cagr'].value, 'pct+')} · "
                f"EPSg {_fmt(r['eps_fwd_growth'].value, 'pct+')} · "
                f"PEG {_fmt(r['peg'].value, 'num')}")
    return (f"Yld {_fmt(r['dividend_yield'].value, 'pct1')} · "
            f"Div5y {_fmt(r['dividend_5y_cagr'].value, 'pct+')} · "
            f"Pay {_fmt(r['payout_ratio'].value, 'pct0')}")


def _screener_row(sn, rc, score, km):
    ivd = rc["ivd"]
    namecol = f"{rc['name']} ({rc['rep']})"
    iv_s = f"{ivd['iv']:9.2f}" if ivd["iv"] else f"{'n/m':>9}"
    pr_s = f"{ivd['price']:9.2f}" if ivd["price"] else f"{'n/a':>9}"
    return (f"{sn:>3}  {namecol:<26} {score:6.1f}   {iv_s} {pr_s} "
            f"{ivd['aict']:<8} {km}")


def print_screener_views(recs):
    """Two independent ranked screeners (Growth, Dividend) + the hero-card top-5
    preview. This is the shape the UI hero card consumes: one table for Growth,
    toggle to Dividend; the main card face shows top 5 of each."""
    by_g = sorted(recs, key=lambda x: x["g"], reverse=True)
    by_d = sorted(recs, key=lambda x: x["d"], reverse=True)

    # ----- Hero-card face: top 5 of each screener -----
    print("\n" + "=" * 140)
    print("HERO CARD — LONG CHINA / HK   (main-card face: top 5 each screener)")
    print("=" * 140)
    print(f"  {'GROWTH SCREENER — Top 5':<52}   {'DIVIDEND SCREENER — Top 5'}")
    print(f"  {'-'*52}   {'-'*52}")
    for i in range(5):
        gr, dr = by_g[i], by_d[i]
        gcell = f"{i+1}. {gr['name']} ({gr['rep']})  {gr['g']:.1f}"
        dcell = f"{i+1}. {dr['name']} ({dr['rep']})  {dr['d']:.1f}"
        print(f"  {gcell:<52}   {dcell}")

    # ----- Full Growth Screener table (toggle view 1) -----
    print("\n" + "=" * 140)
    print("GROWTH SCREENER  (ranked by Growth score; toggle view)")
    print("=" * 140)
    print(f"{'S/N':>3}  {'Stock Name':<26} {'Growth':>6}   {'IV15':>9} {'Price':>9} "
          f"{'AICT':<8} Key Growth Metrics (Rev3y · EPSg · PEG)")
    print("-" * 140)
    for sn, rc in enumerate(by_g, 1):
        print(_screener_row(sn, rc, rc["g"], key_metrics_str("Growth", rc["r"])))

    # ----- Full Dividend Screener table (toggle view 2) -----
    print("\n" + "=" * 140)
    print("DIVIDEND SCREENER  (ranked by Dividend score; toggle view)")
    print("=" * 140)
    print(f"{'S/N':>3}  {'Stock Name':<26} {'Div':>6}   {'IV15':>9} {'Price':>9} "
          f"{'AICT':<8} Key Dividend Metrics (Yld · Div5y · Pay)")
    print("-" * 140)
    for sn, rc in enumerate(by_d, 1):
        print(_screener_row(sn, rc, rc["d"], key_metrics_str("Dividend", rc["r"])))


def print_card(rank, rep, hk, name, r, g, d, ivd):
    """Full per-ticker detail — what the UI ticker card shows on click."""
    lead = "GROWTH" if g >= d else "DIVIDEND"
    print("\n" + "┌" + "─" * 76)
    print(f"│  #{rank}  {name}  —  report {rep}" + (f"  (HK {hk})" if rep != hk else ""))
    print(f"│  Lead: {lead}   Growth {g:.1f}   Dividend {d:.1f}   "
          f"AICT {ivd['aict']}   ccy {ivd['currency']}")
    print("├" + "─" * 76)
    print("│  GROWTH screen (weight)            value      [source]")
    for m, (lbl, kind) in GROWTH_FMT.items():
        res = r[m]
        print(f"│    {lbl:<20} ({GROWTH_WEIGHTS[m]:>2}%)   {_fmt(res.value, kind):>8}   [{res.source}]")
    print("│  DIVIDEND screen (weight)          value      [source]")
    for m, (lbl, kind) in DIV_FMT.items():
        res = r[m]
        print(f"│    {lbl:<20} ({DIV_WEIGHTS[m]:>2}%)   {_fmt(res.value, kind):>8}   [{res.source}]")
    print("├" + "─" * 76)
    if ivd["iv"]:
        mult = "Gordon-only" if ivd["mult"] is None else f"{ivd['mult']:.0f}x mult"
        print(f"│  IV15 build-up:  EPS0={ivd['E0']:.2f}  g1={ivd['g1']*100:.1f}%  "
              f"haircut={ivd['haircut']*100:.0f}%  blend wA={ivd['wA']:.2f} ({mult})")
        print(f"│    IV_A(Gordon)={ivd['ivA']:.2f}   IV_B(Buffett)={ivd['ivB']:.2f}   "
              f"IV15={ivd['iv']:.2f}")
        print(f"│    Price={ivd['price']:.2f}   P/IV15={ivd['piv']:.2f}   Vpts={ivd['vpts']}")
    else:
        print(f"│  IV15: not meaningful (EPS<=0 or no quote) — price="
              f"{ivd['price'] if ivd['price'] else 'n/a'}")
    print("└" + "─" * 76)


def main():
    _wrap_stdout_utf8()
    show_cards = "--cards" in sys.argv[1:]
    show_screeners = "--screeners" in sys.argv[1:]
    print("=" * 140)
    print(f"HK50 CONSOLIDATED SCREENER BOARD  |  end={END_DATE}  |  sorted by aggregate (leading-screen) score")
    print("  Agg = max(Growth, Dividend), tagged (G)/(D).  IV15 = 15yr OE @ 15% RR (AICT-modulated for SW/Internet only).")
    print("  Key Metrics = headline drivers of the leading screen; full breakdown + IV15 build-up in the ticker card.")
    print("=" * 140)
    hdr = (f"{'S/N':>3}  {'Stock Name':<26} {'Agg Score':<13} "
           f"{'IV15':>9} {'Price':>9} {'AICT':<8} Key Metrics")
    print(hdr)
    print("-" * 140)

    recs = []
    for hk_ticker, name in HK50:
        rep, _ = route(hk_ticker)
        try:
            r = resolve(rep)
        except Exception as e:
            print(f"  --  {name:<26} ERR {type(e).__name__}: {e}")
            continue
        backfill_from_hk_listing(hk_ticker, rep, r)
        g, _ = score_growth(r)
        d, _ = score_div(r)
        ivd = compute_iv15(hk_ticker, rep)
        lead = "Growth" if g >= d else "Dividend"
        best = max(g, d)
        recs.append({"rep": rep, "hk": hk_ticker, "name": name, "r": r,
                     "g": g, "d": d, "best": best, "lead": lead, "ivd": ivd})

    recs.sort(key=lambda x: x["best"], reverse=True)

    for sn, rc in enumerate(recs, 1):
        ivd = rc["ivd"]
        namecol = f"{rc['name']} ({rc['rep']})"
        tag = "G" if rc["lead"] == "Growth" else "D"
        aggcol = f"{rc['best']:5.1f} ({tag})"
        iv_s = f"{ivd['iv']:9.2f}" if ivd["iv"] else f"{'n/m':>9}"
        pr_s = f"{ivd['price']:9.2f}" if ivd["price"] else f"{'n/a':>9}"
        km = key_metrics_str(rc["lead"], rc["r"])
        print(f"{sn:>3}  {namecol:<26} {aggcol:<13} {iv_s} {pr_s} {ivd['aict']:<8} {km}")

    print("=" * 140)
    n = len(recs)
    avg_g = sum(x["g"] for x in recs) / n
    avg_d = sum(x["d"] for x in recs) / n
    n_growth = sum(1 for x in recs if x["lead"] == "Growth")
    pivs = [x["ivd"]["piv"] for x in recs if x["ivd"]["piv"]]
    med_piv = sorted(pivs)[len(pivs) // 2] if pivs else None
    cheap = sum(1 for p in pivs if p <= 1.0)
    print(f"\nCOHORT ({n} names):  avg Growth={avg_g:.1f}   avg Dividend={avg_d:.1f}   "
          f"lead split: {n_growth} Growth / {n - n_growth} Dividend")
    print(f"  median P/IV15={med_piv:.2f}   names trading <= IV15: {cheap}/{len(pivs)}")

    if show_screeners:
        print_screener_views(recs)

    if show_cards:
        print("\n" + "=" * 140)
        print("SAMPLE TICKER CARDS (what each row expands to on click) — top Growth-lead & top Dividend-lead:")
        print("=" * 140)
        top_growth = next(x for x in recs if x["lead"] == "Growth")
        top_div = next(x for x in recs if x["lead"] == "Dividend")
        for rc in (top_growth, top_div):
            rank = recs.index(rc) + 1
            print_card(rank, rc["rep"], rc["hk"], rc["name"], rc["r"],
                       rc["g"], rc["d"], rc["ivd"])


if __name__ == "__main__":
    main()
