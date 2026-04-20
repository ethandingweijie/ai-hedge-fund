"""
src/tools/fred.py — FRED (St. Louis Fed) observation fetcher.

Wraps the FRED observations endpoint with:
  * in-process 24h cache (FRED data is daily; more frequent fetches waste calls)
  * defensive error handling — any network/HTTP/parse failure returns None so
    callers can fall back to a static table rather than crashing a valuation
  * tiny surface area — one public function, ``get_fred_spread``

Primary use case: fetching ICE BofA Option-Adjusted Spread (OAS) series so
``get_cost_of_debt()`` in ``src/data/sector_profiles.py`` can price debt at
live market spreads instead of LLM-sourced guesses.

Environment variable:
    FRED_API_KEY  — register free at https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

_log = logging.getLogger(__name__)

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_CACHE_TTL_SECONDS = 60 * 60 * 24        # 24h — FRED updates once per day
_HTTP_TIMEOUT      = 10                  # seconds

# Cache layout: key → (fetched_at_epoch, value_pct_or_None)
_cache: dict[str, tuple[float, Optional[float]]] = {}
_cache_lock = threading.Lock()


def _get_api_key() -> Optional[str]:
    return os.environ.get("FRED_API_KEY")


def get_fred_spread(series_id: str) -> Optional[float]:
    """Return the latest FRED observation for ``series_id`` as a float in the
    series' native units (percentage points for OAS series, i.e. 1.01 = 101bps).

    Returns ``None`` on any failure — missing API key, network error, rate
    limit, unknown series, or non-numeric observation. Callers should treat
    ``None`` as "use fallback".

    Cached per process for 24h.
    """
    now = time.time()

    # Fast-path: cached value still within TTL
    with _cache_lock:
        cached = _cache.get(series_id)
        if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    api_key = _get_api_key()
    if not api_key:
        _log.warning("[FRED] FRED_API_KEY not set — cannot fetch %s", series_id)
        with _cache_lock:
            _cache[series_id] = (now, None)
        return None

    params = urllib.parse.urlencode({
        "series_id":  series_id,
        "api_key":    api_key,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      1,
    })
    url = f"{_FRED_BASE}?{params}"

    value: Optional[float] = None
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
            payload = json.load(r)
        obs = (payload.get("observations") or [])
        if obs:
            raw = obs[0].get("value")
            # FRED uses "." as the missing-data sentinel
            if raw not in (None, "", "."):
                value = float(raw)
    except Exception as exc:                              # pragma: no cover
        _log.warning("[FRED] fetch failed for %s: %s", series_id, exc)
        value = None

    with _cache_lock:
        _cache[series_id] = (now, value)
    return value


def clear_cache() -> None:
    """Drop the in-process cache. Intended for tests."""
    with _cache_lock:
        _cache.clear()
