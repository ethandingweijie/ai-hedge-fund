"""
HK50 two-screener scoring report with ADR-prioritized routing.

Routing policy (per user directive):
  * For names with a COMPREHENSIVE US ADR, report via the ADR ticker (FMP) — e.g.
    Alibaba is reported as BABA, not 9988.HK. FMP's financial-growth endpoint
    supplies multi-year CAGRs (revenue / EPS / FCF / op-income / 5y-dividend)
    that AKShare cannot, which is what lifts coverage toward ~90%.
  * For names WITHOUT a comprehensive ADR (no US listing, or FMP-thin), use the
    HK ticker via AKShare + yfinance backfill.

ADR comprehensiveness was verified empirically against FMP (financial-growth +
ratios-ttm + key-metrics-ttm); only IVBIY/CHLKY/CEOHF/SHIIF came back thin, so
Innovent / China Mobile / CNOOC / Sinopec stay on the HK path.

Currency: every scored metric is a dimensionless ratio (growth rates, yields,
payout, PEG, FCF-coverage) — so a USD-traded / CNY-reporting ADR carries NO
currency risk into the scores. We still print the reporting currency per name
for transparency and flag anything unexpected.

Run: .venv\\Scripts\\python.exe scripts/hk50_adr_report.py
     (set FMP_API_KEY first for the ADR/FMP path)
"""
from __future__ import annotations

import time

# NB: do NOT re-wrap sys.stdout here — the import below already installs a UTF-8
# TextIOWrapper; wrapping twice orphans the first wrapper, which closes the
# underlying buffer on GC ("I/O operation on closed file").
from scripts.hk50_gap_analysis import (  # noqa: E402
    resolve, score_growth, score_div, ALL_METRICS, GROWTH_METRICS, DIV_METRICS,
    _yf_info, Resolved, _wrap_stdout_utf8,
)

END_DATE = "2026-05-30"

# HK ticker -> comprehensive US ADR (FMP). Verified COMPREHENSIVE (>=7/9 fields).
ADR_MAP = {
    "0700.HK": "TCEHY", "9988.HK": "BABA",  "3690.HK": "MPNGY", "1810.HK": "XIACY",
    "1024.HK": "KSHTY", "9618.HK": "JD",    "9999.HK": "NTES",  "9626.HK": "BILI",  "0992.HK": "LNVGY",
    "1211.HK": "BYDDY", "0175.HK": "GELYY", "2015.HK": "LI",    "9868.HK": "XPEV",
    "6690.HK": "HSHCY", "2020.HK": "ANPDY", "2331.HK": "LNNGY", "0288.HK": "WHGLY",
    "2269.HK": "WXIBF", "1177.HK": "SBHMY", "0005.HK": "HSBC",  "2888.HK": "SCBFF",
    "1398.HK": "IDCBY", "0939.HK": "CICHY", "3988.HK": "BACHY", "3968.HK": "CIHKY",
    "2388.HK": "BHKLY", "2318.HK": "PNGAY", "2628.HK": "LFC",   "1299.HK": "AAGIY",
    "0857.HK": "PCCYF", "1088.HK": "CSUAY", "0002.HK": "CLPHY", "0006.HK": "HGKGY",
    "0003.HK": "HOKCY", "1109.HK": "CRBJY", "0823.HK": "LKREF", "0011.HK": "HSNGY",
}
# Already-ADR tickers in the universe (kept on FMP as-is)
NATIVE_ADRS = {"CHA", "EH", "YUMC", "TCOM"}
# Thin-ADR names → stay on HK path (FMP ADR returned <4/9 fields)
THIN_ADR = {"1801.HK", "0941.HK", "0883.HK", "0386.HK"}

# (canonical HK ticker, display name) — full HK50
HK50 = [
    ("0700.HK", "Tencent"), ("9988.HK", "Alibaba"), ("3690.HK", "Meituan"),
    ("1024.HK", "Kuaishou"), ("1810.HK", "Xiaomi"), ("9618.HK", "JD.com"),
    ("9999.HK", "NetEase"), ("9626.HK", "Bilibili"), ("0992.HK", "Lenovo"),
    ("1211.HK", "BYD"), ("0175.HK", "Geely"), ("2015.HK", "Li Auto"),
    ("9868.HK", "XPeng"), ("6862.HK", "Haidilao"), ("6690.HK", "Haier"),
    ("2020.HK", "Anta"), ("2331.HK", "Li Ning"), ("0288.HK", "WH Group"),
    ("2313.HK", "Shenzhou"), ("CHA", "Chagee"), ("EH", "EHang"),
    ("YUMC", "Yum China"), ("TCOM", "Trip.com"), ("1801.HK", "Innovent"),
    ("2269.HK", "WuXi Bio"), ("1093.HK", "CSPC"), ("1177.HK", "Sino Biopharm"),
    ("0005.HK", "HSBC"), ("2888.HK", "StanChart"), ("1398.HK", "ICBC"),
    ("0939.HK", "CCB"), ("3988.HK", "BoC"), ("3968.HK", "CMB"),
    ("0011.HK", "Hang Seng Bank"), ("2388.HK", "BOCHK"), ("2318.HK", "Ping An"),
    ("2628.HK", "China Life"), ("1299.HK", "AIA"), ("0941.HK", "China Mobile"),
    ("0762.HK", "China Unicom"), ("0728.HK", "China Telecom"), ("0883.HK", "CNOOC"),
    ("0857.HK", "PetroChina"), ("0386.HK", "Sinopec"), ("1088.HK", "Shenhua"),
    ("0002.HK", "CLP"), ("0006.HK", "Power Assets"), ("0003.HK", "HK&China Gas"),
    ("1109.HK", "CR Land"), ("0823.HK", "Link REIT"),
]

# Structurally-N/A patterns: metrics that don't exist for a business model, so a
# "missing" there is NOT a data gap (a non-payer has no dividend metrics, etc.).
DIV_DEPENDENT = {"dividend_yield", "dividend_5y_cagr", "payout_ratio",
                 "fcf_coverage", "yield_vs_5yr_avg"}
# 5y-history-dependent dividend metrics: structurally N/A for recent initiators
# (a company that started paying < 5y ago has no 5y CAGR / 5y-avg-yield).
DIV_5Y_HISTORY = {"dividend_5y_cagr", "yield_vs_5yr_avg"}
# Banks + insurers — "free cash flow" is not a meaningful concept for them, so
# fcf_coverage is structurally N/A (FMP may still return a junk FCF yield).
FINANCIALS = {
    "0005.HK", "2888.HK", "1398.HK", "0939.HK", "3988.HK", "3968.HK", "0011.HK",
    "2388.HK", "2318.HK", "2628.HK", "1299.HK",
}


def route(hk_ticker: str) -> tuple[str, str]:
    """Return (report_ticker, route_label)."""
    if hk_ticker in NATIVE_ADRS:
        return hk_ticker, "ADR/FMP"
    if hk_ticker in ADR_MAP:
        return ADR_MAP[hk_ticker], "ADR/FMP"
    return hk_ticker, "HK/AKShare+yf"


def currency_of(report_ticker: str) -> tuple[str, str]:
    """(trading_ccy, reporting_ccy) from yfinance info — informational only."""
    yi = _yf_info(report_ticker)
    trade = yi.get("currency") or "?"
    report = yi.get("financialCurrency") or trade
    return trade, report


def coverage_pct(r: dict) -> float:
    got = sum(1 for m in ALL_METRICS if r[m].source != "missing")
    return round(100.0 * got / len(ALL_METRICS), 0)


def is_payer(r: dict) -> bool:
    """A name genuinely pays a dividend (yield resolved and materially > 0).
    Using the *yield* (not payout_ratio, which is 0.0 for non-payers and so
    looks 'resolved') avoids misclassifying non-payers as payers."""
    dy = r["dividend_yield"].value
    return dy is not None and dy > 0.005


def applicable_metrics(hk_ticker: str, r: dict) -> set[str]:
    """The metrics that genuinely apply to this name. Excludes structural-N/A:
      * non-payer            -> all 5 dividend metrics N/A
      * bank / insurer       -> fcf_coverage N/A (FCF not a meaningful concept)
      * payer w/o 5y history -> dividend_5y_cagr + yield_vs_5yr_avg N/A
    A 'missing' inside the applicable set is a TRUE data gap; outside it isn't."""
    applicable = set(GROWTH_METRICS)
    if is_payer(r):
        applicable |= set(DIV_METRICS)
        if hk_ticker in FINANCIALS:
            applicable.discard("fcf_coverage")
        # No 5y dividend record anywhere (FMP 5Y growth, yfinance 5y-avg, computed
        # CAGR all empty) => recent initiator => 5y-history metrics are structural.
        if (r["dividend_5y_cagr"].source == "missing"
                and r["yield_vs_5yr_avg"].source == "missing"):
            applicable -= DIV_5Y_HISTORY
    # A metric the resolver flagged "structural" (e.g. op-income/FCF growth across a
    # loss->profit turnaround, where a CAGR is mathematically undefined) is N/A, not
    # a data gap — drop it from the applicable denominator.
    applicable = {m for m in applicable if r[m].source != "structural"}
    return applicable


def cov_of(applicable: set[str], r: dict) -> float:
    got = sum(1 for m in applicable if r[m].source != "missing")
    return round(100.0 * got / len(applicable), 0) if applicable else 0.0


def backfill_from_hk_listing(hk_ticker: str, rep: str, r: dict) -> None:
    """When a name is routed to an OTC-pink ADR, yfinance's `info` for that ADR is
    often thin (no 5y-avg yield). `yield_vs_5yr_avg` is a pure ratio of yields —
    currency- and listing-neutral — so we can safely source the 5y-avg from the
    HK listing's yfinance when the ADR's is missing. Mutates r in place."""
    if rep == hk_ticker or r["yield_vs_5yr_avg"].source != "missing":
        return
    cur_y = r["dividend_yield"].value
    if cur_y is None:
        return
    avg5 = _yf_info(hk_ticker).get("fiveYearAvgDividendYield")  # in %
    if avg5:
        r["yield_vs_5yr_avg"] = Resolved(cur_y / (avg5 / 100.0), "yfinance")


def main():
    _wrap_stdout_utf8()
    print("=" * 132)
    print(f"HK50 TWO-SCREENER REPORT  |  ADR-prioritized routing  |  end={END_DATE}")
    print(f"  ADR/FMP: {len(ADR_MAP) + len(NATIVE_ADRS)} names (BABA, JD, NTES ...)   "
          f"HK/AKShare+yf: {50 - len(ADR_MAP) - len(NATIVE_ADRS)} names (no/thin ADR)")
    print("=" * 132)
    hdr = (f"{'Report':<7} {'(was)':<9} {'Name':<15} {'route':<14} {'cur':<7} "
           f"{'Growth':>7} {'cov':>5} {'Dividend':>8} {'cov':>5} {'data%':>6} {'appl%':>6}  gaps")
    print(hdr)
    print("-" * 132)

    prov = {m: {"primary": 0, "yfinance": 0, "fmp_growth": 0, "compute": 0,
                "structural": 0, "missing": 0} for m in ALL_METRICS}
    rows = []
    low_cov = []
    for hk_ticker, name in HK50:
        rep, label = route(hk_ticker)
        t0 = time.time()
        try:
            r = resolve(rep)
        except Exception as e:
            print(f"{rep:<7} {hk_ticker:<9} {name:<15} {label:<14} ERR {type(e).__name__}: {e}")
            continue
        backfill_from_hk_listing(hk_ticker, rep, r)
        for m in ALL_METRICS:
            prov[m][r[m].source] += 1
        g, gc = score_growth(r)
        d, dc = score_div(r)
        trade, report = currency_of(rep)
        ccy = trade if trade == report else f"{trade}/{report}"
        dpct = coverage_pct(r)
        applicable = applicable_metrics(hk_ticker, r)
        bcov = cov_of(applicable, r)
        pays = is_payer(r)
        # A gap is only a gap if the metric is APPLICABLE and still missing.
        gaps = sorted(m for m in applicable if r[m].source == "missing")
        rows.append((rep, hk_ticker, name, label, ccy, g, gc, d, dc, dpct, bcov, gaps, pays))
        if bcov < 90:
            low_cov.append((rep, name, bcov, gaps))
        print(f"{rep:<7} {hk_ticker:<9} {name:<15} {label:<14} {ccy:<7} "
              f"{g:>7} {gc:>4.0f}% {d:>8} {dc:>4.0f}% {dpct:>5.0f}% {bcov:>5.0f}%  "
              f"{','.join(gaps) if gaps else '-'}")

    n = len(rows)
    print("=" * 132)

    # provenance
    print("\nPROVENANCE  (of %d names, where each metric came from)" % n)
    print(f"{'metric':<20} {'primary':>8} {'yfinance':>9} {'fmp_growth':>11} {'compute':>8} {'struct':>7} {'MISSING':>8}")
    for m in ALL_METRICS:
        p = prov[m]
        tag = "  <- div-dependent" if m in DIV_DEPENDENT else ""
        print(f"{m:<20} {p['primary']:>8} {p['yfinance']:>9} {p['fmp_growth']:>11} "
              f"{p['compute']:>8} {p['structural']:>7} {p['missing']:>8}{tag}")

    # coverage summary
    avg_data = sum(x[9] for x in rows) / n
    avg_best = sum(x[10] for x in rows) / n
    ge90_data = sum(1 for x in rows if x[9] >= 90)
    ge90_best = sum(1 for x in rows if x[10] >= 90)
    print(f"\nCOVERAGE: raw data% avg={avg_data:.0f}%  ({ge90_data}/{n} names >=90%)")
    print(f"          applicable% avg={avg_best:.0f}%  ({ge90_best}/{n} names >=90%)")
    print("          [applicable drops STRUCTURAL N/A: dividend metrics for non-payers,")
    print("           fcf_coverage for banks/insurers, 5y-history metrics for recent")
    print("           initiators, and op-income/FCF growth across loss->profit turnarounds]")

    # gaps after structural exclusion
    print("\nNAMES STILL BELOW 90% APPLICABLE COVERAGE (true data gaps to close):")
    if not low_cov:
        print("  none — all 50 at >=90% applicable coverage")
    for rep, name, bcov, gaps in sorted(low_cov, key=lambda x: x[2]):
        print(f"  {bcov:>3.0f}%  {rep:<7} {name:<16} missing: {','.join(gaps) if gaps else '-'}")

    # scores
    print("\nTOP 12 HIGH GROWTH:")
    for x in sorted(rows, key=lambda r: r[5], reverse=True)[:12]:
        print(f"  G={x[5]:>5} (cov {x[6]:.0f}%)  {x[0]:<7} {x[2]:<16} [{x[3]}]")
    print("\nTOP 12 HIGH DIVIDEND:")
    for x in sorted([r for r in rows if r[12]], key=lambda r: r[7], reverse=True)[:12]:
        print(f"  D={x[7]:>5} (cov {x[8]:.0f}%)  {x[0]:<7} {x[2]:<16} [{x[3]}]")


if __name__ == "__main__":
    main()
