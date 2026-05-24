"""Smoke-test the Tier 1 evidence-source additions on CRWD."""
import sys
from src.research_ideas.complacency.evidence_sources import (
    fetch_sec_recent_8k, format_8k_filings_for_prompt,
    fetch_sec_def14a_excerpt,
    fetch_sec_recent_form144, format_form144_for_prompt,
    fetch_price_target_consensus,
)

sys.stdout.reconfigure(encoding="utf-8")
ticker = sys.argv[1] if len(sys.argv) > 1 else "CRWD"

print(f"\n=== 1A: SEC 8-K filings (last 180d) for {ticker} ===")
filings_8k = fetch_sec_recent_8k(ticker, days=180, limit=10)
print(f"  count: {len(filings_8k)}")
print(format_8k_filings_for_prompt(filings_8k))

print(f"\n=== 1B: SEC DEF 14A proxy excerpt for {ticker} ===")
proxy = fetch_sec_def14a_excerpt(ticker)
if proxy:
    print(f"  filed_date: {proxy.get('_filed_date')}")
    print(f"  source_url: {proxy.get('_source_url')}")
    for key, snip in (proxy.get("sections") or {}).items():
        print(f"  section {key}: len={len(snip)} chars; first 150: {snip[:150]}")
else:
    print("  NONE")

print(f"\n=== 1D: SEC Form 144 (last 90d) for {ticker} ===")
filings_144 = fetch_sec_recent_form144(ticker, days=90, limit=10)
print(f"  count: {len(filings_144)}")
print(format_form144_for_prompt(filings_144))

print(f"\n=== 2A: FMP price-target consensus for {ticker} ===")
pt = fetch_price_target_consensus(ticker)
if pt:
    cv_str = f"{pt['cv_estimate']:.2%}" if pt.get('cv_estimate') is not None else "n/a"
    print(f"  high: {pt['target_high']}  low: {pt['target_low']}")
    print(f"  avg:  {pt['target_avg']}   median: {pt['target_median']}")
    print(f"  CV (half-range / avg): {cv_str}  ← lower CV = more uniform consensus = A3 flag")
else:
    print("  NONE")
