"""SEC EDGAR fallback for KPIs that FMP doesn't expose cleanly.

Currently used for: cet1_ratio (Banks). FMP doesn't include risk-weighted
assets (RWA) so CET1 can't be derived from balance-sheet alone — but every
bank reports it explicitly in the 10-Q "Capital Ratios" section.

Pipeline:
  1. Resolve ticker → CIK via SEC's public company_tickers.json
  2. Fetch latest 10-Q metadata via SEC submissions API
  3. Download 10-Q HTML (~10MB) and strip to plaintext
  4. Apply refined regex to extract the actual reported CET1 ratio (NOT the
     regulatory minimum which is always Basel III's 4.5% floor)
  5. Cache result for the quarter (10-Q only updates ~quarterly)

Compliance: SEC requires a User-Agent header with a contact email.
Rate limit: 10 req/sec across all endpoints (sec.gov + data.sec.gov).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# SEC enforces this — they monitor and block requests without a real User-Agent
_SEC_UA = "AI-Hedge-Fund (research@aihedgefund.local)"

_CIK_CACHE: dict[str, str] = {}
_TICKER_MAP: dict[str, str] | None = None
_RATIO_CACHE: dict[tuple[str, str], dict] = {}   # (ticker, kpi) → {value, source_url, filing_date}

# Throttle: at most one SEC call every 100ms — well under their 10 req/sec ceiling.
_LAST_CALL_TS: float = 0.0
_MIN_INTERVAL_SEC = 0.12


def _throttle() -> None:
    """Block until at least _MIN_INTERVAL_SEC has passed since last SEC call."""
    global _LAST_CALL_TS
    elapsed = time.time() - _LAST_CALL_TS
    if elapsed < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - elapsed)
    _LAST_CALL_TS = time.time()


def _http_get_json(url: str, timeout: int = 10) -> Any:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": _SEC_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_get_text(url: str, timeout: int = 30) -> str:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": _SEC_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _load_ticker_cik_map() -> dict[str, str]:
    """Fetch SEC's official ticker→CIK mapping. Cached forever (refreshes daily,
    but for our purposes never changes)."""
    global _TICKER_MAP
    if _TICKER_MAP is not None:
        return _TICKER_MAP
    try:
        data = _http_get_json("https://www.sec.gov/files/company_tickers.json")
        # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        out: dict[str, str] = {}
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            t = (entry.get("ticker") or "").upper()
            cik = entry.get("cik_str")
            if t and cik is not None:
                out[t] = str(cik).zfill(10)
        _TICKER_MAP = out
        return out
    except Exception:
        return {}


def resolve_cik(ticker: str) -> str | None:
    """Return zero-padded 10-char CIK for ticker, or None if not found."""
    t = ticker.upper()
    if t in _CIK_CACHE:
        return _CIK_CACHE[t]
    cik = _load_ticker_cik_map().get(t)
    if cik:
        _CIK_CACHE[t] = cik
    return cik


def latest_10q_url(ticker: str) -> tuple[str | None, str | None]:
    """Return (10-Q HTML url, filing date) or (None, None) on failure."""
    cik = resolve_cik(ticker)
    if not cik:
        return None, None
    try:
        sub = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception:
        return None, None
    rec = (sub.get("filings") or {}).get("recent") or {}
    forms  = rec.get("form", [])
    dates  = rec.get("filingDate", [])
    accs   = rec.get("accessionNumber", [])
    docs   = rec.get("primaryDocument", [])
    cik_int = int(sub.get("cik", cik))
    for i, f in enumerate(forms):
        if f == "10-Q":
            acc_clean = accs[i].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{docs[i]}"
            return url, dates[i]
    return None, None


# Refined CET1 patterns — each pattern targets a bank-specific phrasing.
# We collect ALL matches and pick the one most likely to be the ACTUAL ratio
# (not the regulatory minimum). Heuristic: skip matches where surrounding
# context (±100 chars) contains "minimum", "requirement", "buffer", "floor".
_CET1_PATTERNS = [
    re.compile(r"CET1\s*(?:capital\s*)?ratio\D{0,40}(\d{1,2}\.\d{1,2})\s*%", re.I),
    re.compile(r"Common\s+Equity\s+Tier\s+1\s*(?:capital\s*)?ratio\D{0,40}(\d{1,2}\.\d{1,2})\s*%", re.I),
    # BAC / WFC / USB pattern — "CET1 ratio under the Standardized Approach was X.X%"
    re.compile(r"CET1\s*(?:ratio|capital ratio)\s*(?:under|on)\s+(?:the\s+)?(?:Standardized|Basel|Advanced)[^%]{0,80}?(\d{1,2}\.\d{1,2})\s*%", re.I),
    # Table-row pattern: "CET1" followed by digits in a table cell
    re.compile(r"CET1[^a-z\d]{1,20}(\d{1,2}\.\d{1,2})\s*%", re.I),
]

_NEGATIVE_CONTEXT_TOKENS = ("minimum", "requirement", "buffer", "floor", "g-sib", "regulatory")


def _is_negative_context(text: str, match_start: int) -> bool:
    """True if the regulatory-minimum context surrounds this match."""
    before = text[max(0, match_start - 100):match_start].lower()
    return any(tok in before for tok in _NEGATIVE_CONTEXT_TOKENS)


def extract_cet1_from_10q(html: str) -> float | None:
    """Returns CET1 ratio as decimal (e.g. 0.148 for 14.8%), or None."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    candidates: list[tuple[float, str]] = []
    seen_positions: set[int] = set()
    for rx in _CET1_PATTERNS:
        for m in rx.finditer(text):
            pos = m.start()
            if pos in seen_positions:
                continue
            seen_positions.add(pos)
            try:
                val = float(m.group(1)) / 100
            except (ValueError, IndexError):
                continue
            if val < 0.04 or val > 0.30:
                continue   # CET1 should be 4-30% — anything else is junk
            if _is_negative_context(text, pos):
                continue   # Reject regulatory minimum
            # Snippet for debugging
            snip = text[max(0, pos-40):pos+80].strip()
            candidates.append((val, snip))

    if not candidates:
        return None

    # Heuristic: pick the LATEST candidate in the document (10-Qs typically put
    # current-quarter values toward the end of the Capital Ratios section).
    # If many candidates cluster around one value, pick the median (robust).
    if len(candidates) >= 3:
        vals = sorted(c[0] for c in candidates)
        return vals[len(vals) // 2]
    return candidates[-1][0]


def cet1_for_ticker(ticker: str) -> dict | None:
    """Returns {"cet1_ratio": float, "source": url, "filing_date": str} or None."""
    cache_key = (ticker.upper(), "cet1_ratio")
    if cache_key in _RATIO_CACHE:
        return _RATIO_CACHE[cache_key]

    url, filing_date = latest_10q_url(ticker)
    if not url:
        return None
    try:
        html = _http_get_text(url, timeout=45)
    except Exception:
        return None

    val = extract_cet1_from_10q(html)
    if val is None:
        return None

    result = {"cet1_ratio": val, "source": url, "filing_date": filing_date}
    _RATIO_CACHE[cache_key] = result
    return result


__all__ = [
    "resolve_cik",
    "latest_10q_url",
    "extract_cet1_from_10q",
    "cet1_for_ticker",
]
