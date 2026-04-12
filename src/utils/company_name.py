"""
Shared ticker → company name resolver.

Used by pdf_report.py, deep_research.py, and strategic_router.py to inject the
full legal company name into LLM prompts rather than the raw ticker symbol.
This prevents ambiguous tickers (e.g. "CHA" = CHAGEE *or* China Telecom ADR)
from being misidentified by the LLM.

Implementation:
- Queries FMP /stable/profile endpoint (same key as FINANCIAL_DATASETS_API_KEY)
- Process-level LRU cache avoids redundant API calls within a single pipeline run
- Falls back to the ticker symbol if the API call fails or returns no name
"""

import os

_COMPANY_NAME_CACHE: dict[str, str] = {}


def fetch_company_name(ticker: str) -> str:
    """Return the full legal company name for a ticker.

    Queries FMP /stable/profile; caches per process; falls back to ticker symbol.
    Always returns a non-empty string (worst case: the ticker itself).
    """
    if ticker in _COMPANY_NAME_CACHE:
        return _COMPANY_NAME_CACHE[ticker]
    try:
        import requests as _req

        key = (
            os.environ.get("FMP_API_KEY")
            or os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
        )
        if key:
            resp = _req.get(
                "https://financialmodelingprep.com/stable/profile",
                params={"symbol": ticker, "apikey": key},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                row = data[0] if isinstance(data, list) and data else (data or {})
                name = (row.get("companyName") or "").strip()
                if name:
                    _COMPANY_NAME_CACHE[ticker] = name
                    return name
    except Exception:
        pass
    _COMPANY_NAME_CACHE[ticker] = ticker
    return ticker
