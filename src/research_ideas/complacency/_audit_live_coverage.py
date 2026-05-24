"""
Audit each scoring input across the latest Complacency cohort and report:
  - Field coverage (n/50 populated)
  - Source (live FMP endpoint or computed)
  - Whether any static fallback fired
"""
import sys
from app.backend.services.complacency_storage import get_latest_complacency_run

sys.stdout.reconfigure(encoding="utf-8")

run = get_latest_complacency_run()
if not run:
    print("No persisted cohort.")
    sys.exit(1)

n = run["ticker_count"]
results = run["results"]

print(f"Latest cohort: run_id={run['run_id']}  created={run['created_at']}  n={n}")
print(f"Gate passers: {run.get('gate_passers')}\n")

# (field, source label, is-live)
INPUTS = [
    ("ev_sales",               "FMP /key-metrics-ttm  evToSalesTTM",        True),
    ("ev_sales_sector_median", "Live cache: S&P 500 medians (weekly)",      True),
    ("ev_sales_relative",      "Derived: ev_sales / sector median",         True),
    ("fcf_yield_ttm",          "FMP /key-metrics-ttm  freeCashFlowYieldTTM",True),
    ("altman_z",               "FMP /financial-scores  altmanZScore",       True),
    ("piotroski",              "FMP /financial-scores  piotroskiScore",     True),
    ("ad_ratio_4q_avg",        "FMP /insider-trading/search (Ultimate-tier)", False),  # may be null on free
    ("eps_revision_yoy",       "Live: analyst NI Y+1 / latest reported NI", True),
    ("rsi_weekly",             "Local: 14-wk Wilder RSI on EOD light",      True),
    ("sma200_extension",       "Derived: (price - sma200) / sma200",        True),
    ("range_position",         "Derived: (price - 52w_low) / 52w_range",    True),
    ("price",                  "FMP /quote  price",                         True),
    ("market_cap",             "FMP /quote  marketCap",                     True),
    ("sector",                 "Static (universe JSON, GICS-aligned)",      True),
]

print(f"{'Field':<30}{'Live':<7}{'Coverage':<12}{'Source':<55}")
print("-" * 110)
for field, source, is_live in INPUTS:
    populated = sum(1 for r in results if r.get(field) is not None)
    pct = 100.0 * populated / n
    mark = "OK " if is_live else "~~ "
    print(f"{field:<30}[{mark}] {populated:>3}/{n}  {pct:5.0f}%  {source}")

# Show the live medians actually used
sector_medians_used = sorted({
    (r.get("sector", "—"), r.get("ev_sales_sector_median"))
    for r in results if r.get("ev_sales_sector_median") is not None
})
print("\nLive sector medians threaded into this cohort:")
for sec, med in sector_medians_used:
    print(f"  {sec:25s} EV/S = {med:.2f}×")

# Check no static fallback was hit (would show as values like 7.0, 4.5, etc.)
from src.research_ideas.complacency.scoring import SECTOR_EV_SALES_MEDIAN_FALLBACK
static_values = set(SECTOR_EV_SALES_MEDIAN_FALLBACK.values())
suspicious = [
    r for r in results
    if r.get("ev_sales_sector_median") in static_values
]
if suspicious:
    print(f"\n⚠ Possible static fallback in {len(suspicious)} ticker(s):")
    for r in suspicious[:5]:
        print(f"   {r['ticker']:6s} sector={r.get('sector')}  median={r.get('ev_sales_sector_median')}")
else:
    print("\nNo rows used the static fallback table — all medians live.")
