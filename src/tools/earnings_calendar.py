"""Next scheduled earnings date per ticker, from FMP's earnings calendar.

The research rating needs one fact from it: whether a results date falls
inside the review window. A rating whose structural and 12-month views
disagree with results days away is placed Under Review rather than pinned.

One calendar fetch covers every company for the window, so the whole
window is cached per UTC day and every ticker in a run reads from it.
No key, a failed call or no event all return None -- the caller treats
"unknown" as "no earnings ahead", never as a reason to fail a run.
"""
from __future__ import annotations

import os
import threading
from datetime import date, timedelta
from typing import Optional

_STABLE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 12

#: (from_date, days) -> {FMP symbol: earliest date in the window}
_CALENDAR_CACHE: dict[tuple[str, int], dict[str, str]] = {}
_LOCK = threading.Lock()


def _window(start: date, days: int) -> dict[str, str]:
    key = (start.isoformat(), days)
    with _LOCK:
        if key in _CALENDAR_CACHE:
            return _CALENDAR_CACHE[key]
    api_key = os.environ.get("FMP_API_KEY") or ""
    if not api_key:
        return {}
    by_symbol: dict[str, str] = {}
    try:
        import requests
        resp = requests.get(
            f"{_STABLE}/earnings-calendar",
            params={"from": start.isoformat(),
                    "to": (start + timedelta(days=days)).isoformat(),
                    "apikey": api_key},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return {}
        for ev in resp.json() or []:
            sym = str(ev.get("symbol") or "").upper()
            when = str(ev.get("date") or "")[:10]
            if sym and when and (sym not in by_symbol or when < by_symbol[sym]):
                by_symbol[sym] = when
    except Exception:
        return {}                     # not cached: a transient failure may recover
    with _LOCK:
        _CALENDAR_CACHE[key] = by_symbol
    return by_symbol


def next_earnings_date(ticker: str, days: int = 21,
                       today: Optional[date] = None) -> Optional[str]:
    """ISO date of the next results inside `days`, or None."""
    from src.tools.fmp_transcripts import to_fmp_symbol
    sym = to_fmp_symbol(ticker)
    if not sym:
        return None
    return _window(today or date.today(), days).get(sym.upper())
