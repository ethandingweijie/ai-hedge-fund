"""
src/research_ideas/momentum/data_fetch.py
==========================================
Price-series fetch for the momentum screen.

Prefers FMP's FULL /historical-price-eod (real OHLC, needed for ADX/ATR);
falls back to get_prices() (the 'light' close-only feed) when the full feed
is unavailable, in which case ADX/ATR are skipped by the scorer.

  fetch_price_series(symbol, as_of=None, lookback_days=540) -> MomentumBundle | None

`as_of` (ISO yyyy-mm-dd) lets us point the screen at a historical date to
validate against a known move (e.g. the software sell-down). Defaults to today.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.tools.api import _fmp_get, _STABLE, get_prices, prices_to_df


logger = logging.getLogger(__name__)


@dataclass
class MomentumBundle:
    symbol: str
    name: str = ""
    sector: Optional[str] = None
    sector_etf: Optional[str] = None
    df: Optional[pd.DataFrame] = None        # OHLCV, chronological, DatetimeIndex
    has_true_ohlc: bool = False              # False = close-only feed (ADX skipped)
    as_of: Optional[str] = None
    bars: int = 0


def _fetch_full_ohlc(symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """FMP full EOD (open/high/low/close/volume). Returns None on failure."""
    data = _fmp_get(
        f"{_STABLE}/historical-price-eod/full",
        {"symbol": symbol, "from": start, "to": end},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    rows = []
    for r in data:
        d = r.get("date")
        if not d:
            continue
        try:
            rows.append({
                "time": d,
                "open": float(r.get("open")),
                "high": float(r.get("high")),
                "low": float(r.get("low")),
                "close": float(r.get("close")),
                "volume": float(r.get("volume") or 0),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    return df


def fetch_price_series(
    symbol: str,
    as_of: Optional[str] = None,
    lookback_days: int = 540,
) -> Optional[MomentumBundle]:
    """
    Pull ~`lookback_days` calendar days of EOD bars for `symbol`, ending at
    `as_of` (default today). Real OHLC if available, else close-only fallback.
    Returns None if no usable history.
    """
    end_d = date.fromisoformat(as_of) if as_of else date.today()
    start_d = end_d - timedelta(days=lookback_days)
    start, end = start_d.isoformat(), end_d.isoformat()

    df: Optional[pd.DataFrame] = None
    has_true_ohlc = False

    try:
        df = _fetch_full_ohlc(symbol, start, end)
        if df is not None and not bool((df["high"] == df["low"]).all()):
            has_true_ohlc = True
    except Exception as exc:
        logger.warning("Full OHLC fetch failed for %s: %s", symbol, exc)
        df = None

    if df is None or df.empty:
        # Fallback: light close-only feed via the shared price client.
        try:
            prices = get_prices(symbol, start, end)
            if prices:
                df = prices_to_df(prices)
                has_true_ohlc = False
        except Exception as exc:
            logger.warning("Light price fetch failed for %s: %s", symbol, exc)
            return None

    if df is None or df.empty:
        return None

    return MomentumBundle(
        symbol=symbol,
        df=df,
        has_true_ohlc=has_true_ohlc,
        as_of=end,
        bars=len(df),
    )
