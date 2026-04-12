"""
HK market cap via AKShare stock_hk_financial_indicator_em.
Falls back to yfinance if AKShare fails.
"""
from __future__ import annotations

import logging

from src.tools.hk._utils import _parse_float
from src.tools.hk.ticker import to_akshare_code, to_yfinance_code

_log = logging.getLogger(__name__)

# AKShare returns 总市值 in 亿元 (100 million).  Multiply to get raw HKD.
_YIYI = 1e8


def get_hk_market_cap(ticker: str, end_date: str) -> float | None:
    """
    Return total market capitalisation in HKD.

    Primary:  AKShare stock_hk_financial_indicator_em → 总市值 × 1e8
    Fallback: yfinance .info["marketCap"]
    """
    cap = _from_akshare(ticker)
    if cap is not None:
        return cap
    return _from_yfinance(ticker)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _from_akshare(ticker: str) -> float | None:
    try:
        import akshare as ak
    except ImportError:
        return None

    symbol = to_akshare_code(ticker)
    try:
        df = ak.stock_hk_financial_indicator_em(symbol=symbol)
    except Exception as exc:
        _log.debug("AKShare financial_indicator_em failed for %s: %s", symbol, exc)
        return None

    if df is None or df.empty:
        return None

    # The DataFrame is wide: first column = metric name, second = value.
    try:
        indicator: dict = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
    except Exception:
        return None

    raw = indicator.get("总市值(港元)") or indicator.get("总市值")
    val = _parse_float(raw)
    if val is None:
        return None

    # If value looks like it is already in full HKD (>1e10), don't scale again
    if val > 1e10:
        return val
    # Otherwise assume 亿 units
    return val * _YIYI


def _from_yfinance(ticker: str) -> float | None:
    try:
        import yfinance as yf
    except ImportError:
        return None

    yf_sym = to_yfinance_code(ticker)
    try:
        info = yf.Ticker(yf_sym).info
        cap = info.get("marketCap")
        return float(cap) if cap is not None else None
    except Exception as exc:
        _log.debug("yfinance market cap failed for %s: %s", yf_sym, exc)
        return None
