"""
SGX currency handling — SGD/USD FX rate.

Most SGX-listed companies report in SGD. A small number (e.g., Wilmar
International) report in USD. This module provides:
  - SGD/USD exchange rate lookup (yfinance, cached 1 hour)
  - Per-company reporting currency lookup
"""

import os
import time
from typing import Optional

_SGD_USD_FALLBACK = 0.75  # approximate SGD/USD fallback rate
_CACHE_TTL = 3600  # 1 hour
_cached_rate: tuple[float, float] | None = None  # (rate, timestamp)

# Companies that report in non-SGD currencies
_REPORTING_CURRENCY: dict[str, str] = {
    "F34":  "USD",   # Wilmar International
    "EB5":  "USD",   # First Resources
    "P8Z":  "USD",   # Bumitama Agri
    "E5H":  "USD",   # Golden Agri-Resources
    "H78":  "USD",   # Hongkong Land
    "J36":  "USD",   # Jardine Matheson
    "J37":  "USD",   # Jardine C&C
    "RW0U": "EUR",   # Cromwell European REIT
}


def get_reporting_currency(sg_code: str) -> str:
    """Return the reporting currency for an SGX ticker. Default: SGD."""
    raw = sg_code.strip().upper().replace(".SI", "")
    return _REPORTING_CURRENCY.get(raw, "SGD")


def sgd_to_usd_rate() -> float:
    """Return the current SGD/USD exchange rate (how many USD per 1 SGD).
    Uses yfinance with 1-hour cache. Falls back to hardcoded rate."""
    global _cached_rate
    if _cached_rate and (time.time() - _cached_rate[1]) < _CACHE_TTL:
        return _cached_rate[0]

    try:
        import yfinance as yf
        t = yf.Ticker("SGDUSD=X")
        info = t.info
        rate = info.get("regularMarketPrice") or info.get("previousClose") or info.get("ask")
        if rate and 0.5 < rate < 1.0:  # sanity check
            _cached_rate = (float(rate), time.time())
            return _cached_rate[0]
    except Exception:
        pass

    return _SGD_USD_FALLBACK


def statement_to_sgd(value: Optional[float], sg_code: str) -> Optional[float]:
    """Convert a financial statement value to SGD if the company reports in another currency."""
    if value is None:
        return None
    ccy = get_reporting_currency(sg_code)
    if ccy == "SGD":
        return value
    if ccy == "USD":
        # USD → SGD: divide by SGD/USD rate (invert)
        rate = sgd_to_usd_rate()
        return value / rate if rate else value
    # EUR and others: no conversion for now
    return value
