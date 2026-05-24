"""Smoke-test the three new evidence sources on CRWD."""
import sys
from src.research_ideas.complacency.evidence_sources import (
    fetch_sec_10k_sections, tavily_search,
    compute_financial_signals, format_financial_signals_for_prompt,
)

sys.stdout.reconfigure(encoding="utf-8")
ticker = sys.argv[1] if len(sys.argv) > 1 else "CRWD"

print(f"\n=== Source 1: SEC EDGAR 10-K sections for {ticker} ===")
sec = fetch_sec_10k_sections(ticker)
if not sec:
    print("   FAIL: empty")
else:
    print(f"   filed_date: {sec.get('_filed_date')}")
    print(f"   source_url: {sec.get('_source_url')}")
    for key in ("risk_factors", "mda", "controls", "kam"):
        v = sec.get(key)
        if v:
            print(f"   {key}: len={len(v)}  first 200 chars: {v[:200]}")
        else:
            print(f"   {key}: <not extracted>")

print(f"\n=== Source 2: Tavily targeted searches for {ticker} ===")
for label, q in [
    ("D1 mgmt scandal",       f'"{ticker}" CEO OR CFO scandal OR departure OR investigation'),
    ("C1 disclosure",         f'"{ticker}" restatement OR auditor change OR going concern'),
    ("A1 single thesis",      f'"{ticker}" bull case OR thesis OR analyst day'),
]:
    rows = tavily_search(q, days=90, max_results=3)
    print(f"\n   [{label}]  q={q}")
    if not rows:
        print("      no results")
    for r in rows:
        print(f"      score {r['score']:.2f}  {r['published_date'][:10]}  {r['title'][:80]}")

print(f"\n=== Source 3: Computed financial signals for {ticker} ===")
sig = compute_financial_signals(ticker)
print(format_financial_signals_for_prompt(sig))
