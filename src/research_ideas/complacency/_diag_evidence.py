"""Show what each evidence gatherer returns for CRWD — diagnose empty-context issue."""
import sys
from src.research_ideas.complacency.qualitative import (
    _fetch_recent_news, _fetch_latest_transcript,
    _fetch_10k_risk_factors, _fetch_earnings_calendar,
    _gather_evidence_A2, _gather_evidence_C2, _gather_evidence_D1,
)
sys.stdout.reconfigure(encoding="utf-8")

ticker = sys.argv[1] if len(sys.argv) > 1 else "CRWD"
print(f"\n=== Evidence diagnostics for {ticker} ===\n")

print("1) /news/stock (90d window, limit=5)")
news = _fetch_recent_news(ticker, days=90, limit=5)
print(f"   returned {len(news)} items")
for n in news[:2]:
    print(f"   - {n.get('date','?')} :: {n.get('title','')[:80]}")

print("\n2) /earning-call-transcript (latest)")
t = _fetch_latest_transcript(ticker)
if t:
    print(f"   source: {t['source']}  date: {t['date']}  len: {len(t['content_snippet'])} chars")
    print(f"   first 200 chars: {t['content_snippet'][:200]}")
else:
    print("   NONE")

print("\n3) /financial-reports-json risk factors")
rf = _fetch_10k_risk_factors(ticker)
if rf:
    print(f"   source: {rf['source']}  len: {len(rf['content_snippet'])} chars")
    print(f"   first 200 chars: {rf['content_snippet'][:200]}")
else:
    print("   NONE")

print("\n4) /earnings-calendar")
cal = _fetch_earnings_calendar(ticker)
print(f"   {cal}")

print("\n--- Aggregated packs per indicator ---")
print(f"\nA2 packs: {len(_gather_evidence_A2(ticker))}")
print(f"C2 packs: {len(_gather_evidence_C2(ticker))}")
print(f"D1 packs: {len(_gather_evidence_D1(ticker))}")
