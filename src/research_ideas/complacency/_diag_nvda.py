"""
Trace one ticker end-to-end (default NVDA):
  1. What's stored in the persisted cohort?
  2. What does live FMP return when we re-fetch?
  3. Specifically: insider A/D, RSI, 200DMA extension.
"""
import sys
from src.research_ideas.complacency.data_fetch import (
    fetch_ticker_bundle, _fetch_insider_ad_ratio,
    _fetch_quote, _fetch_weekly_close_history, _compute_rsi,
)
from app.backend.services.complacency_storage import get_latest_complacency_run

sys.stdout.reconfigure(encoding="utf-8")

TICKER = sys.argv[1] if len(sys.argv) > 1 else "NVDA"

# ─── 1. What's in the persisted cohort? ──────────────────────────────
print(f"\n=== 1. PERSISTED cohort row for {TICKER} ===")
run = get_latest_complacency_run()
if run:
    row = next((r for r in run["results"] if r["ticker"] == TICKER), None)
    if not row:
        print(f"  {TICKER} not in cohort universe.")
    else:
        for k in ["price", "ev_sales", "fcf_yield_ttm", "altman_z", "piotroski",
                  "ad_ratio_4q_avg", "rsi_weekly", "sma200_extension",
                  "range_position", "eps_revision_yoy", "ev_sales_sector_median",
                  "ev_sales_relative", "val_score", "beh_score", "tech_score",
                  "qual_score", "composite", "verdict"]:
            v = row.get(k)
            mark = "OK" if v is not None else "NULL"
            print(f"  [{mark:>4}] {k:<28} {v}")
else:
    print("  No persisted cohort.")

# ─── 2. Live A/D ratio (insider trading endpoint) ──────────────────────
print(f"\n=== 2. LIVE /insider-trading/search for {TICKER} ===")
ad = _fetch_insider_ad_ratio(TICKER)
print(f"  A/D ratio (4Q avg) = {ad}")

# Also dump the raw rows to see if FMP is returning anything at all
from src.tools.api import _fmp_get, _STABLE
raw = _fmp_get(
    f"{_STABLE}/insider-trading/search",
    {"symbol": TICKER, "page": 0, "limit": 20},
    api_key=None,
    uncap=True,
)
n_raw = len(raw) if isinstance(raw, list) else 0
print(f"  Raw rows returned: {n_raw}")
if isinstance(raw, list) and raw:
    sample = raw[0]
    print(f"  Sample row keys: {sorted(sample.keys())[:10]}")
    print(f"  Sample tx type:  {sample.get('transactionType')}")
    print(f"  Sample acq/disp: {sample.get('acquisitionOrDisposition')}")

# ─── 3. Live quote (price, MAs, 52w) ──────────────────────────────────
print(f"\n=== 3. LIVE /quote for {TICKER} ===")
q = _fetch_quote(TICKER)
if q:
    for k in ["price", "marketCap", "priceAvg50", "priceAvg200", "yearHigh", "yearLow"]:
        v = q.get(k)
        mark = "OK" if v is not None else "NULL"
        print(f"  [{mark:>4}] {k:<20} {v}")
else:
    print("  Quote returned None")

# ─── 4. Live RSI computed from EOD ───────────────────────────────────
print(f"\n=== 4. LIVE weekly-RSI for {TICKER} ===")
closes = _fetch_weekly_close_history(TICKER, weeks=30)
print(f"  Weekly closes fetched: {len(closes)}")
if closes:
    print(f"  First/last closes: {closes[0]:.2f} … {closes[-1]:.2f}")
    rsi = _compute_rsi(closes, period=14)
    print(f"  Computed RSI(14): {rsi}")
else:
    print("  No closes — RSI cannot compute")
