"""
Diagnostic — fire each FMP endpoint the Complacency scorer needs and dump
the full first-row payload so we can see what FMP actually returns.

Run:
    $env:FMP_API_KEY = "..."
    .\.venv\Scripts\python.exe -m src.research_ideas.complacency._diag_fmp NVDA
"""
import json
import sys

from src.tools.api import _fmp_get, _STABLE


def main(ticker: str = "NVDA") -> None:
    print(f"\n=== /stable/key-metrics-ttm?symbol={ticker} ===")
    km = _fmp_get(f"{_STABLE}/key-metrics-ttm", {"symbol": ticker, "limit": 1}, api_key=None, uncap=True)
    if isinstance(km, list) and km:
        print(json.dumps(km[0], indent=2)[:2200])
    else:
        print(f"  (no rows) — raw: {km!r}")

    print(f"\n=== /stable/financial-scores?symbol={ticker} ===")
    fs = _fmp_get(f"{_STABLE}/financial-scores", {"symbol": ticker, "limit": 1}, api_key=None, uncap=True)
    if isinstance(fs, list) and fs:
        print(json.dumps(fs[0], indent=2))
    else:
        print(f"  (no rows) — raw: {fs!r}")

    print(f"\n=== /stable/ratios-ttm?symbol={ticker} (fallback for FCF yield) ===")
    rt = _fmp_get(f"{_STABLE}/ratios-ttm", {"symbol": ticker, "limit": 1}, api_key=None, uncap=True)
    if isinstance(rt, list) and rt:
        # Print just the EV / FCF / sales related fields
        row = rt[0]
        relevant = {k: v for k, v in row.items() if any(t in k.lower() for t in ("ev", "fcf", "free", "yield", "sales"))}
        print(json.dumps(relevant, indent=2))
    else:
        print(f"  (no rows) — raw: {rt!r}")

    print(f"\n=== /stable/quote?symbol={ticker} (already known) ===")
    q = _fmp_get(f"{_STABLE}/quote", {"symbol": ticker}, api_key=None, uncap=True)
    if isinstance(q, list) and q:
        print(json.dumps(q[0], indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "NVDA")
