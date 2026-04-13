"""
SGX price data — OHLCV history via yfinance.
"""

from datetime import datetime, timedelta
from typing import Optional
from src.tools.sg.ticker import to_yfinance_code
from src.tools.sg._utils import _parse_float


def get_sg_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> list:
    """Fetch daily OHLCV prices for an SGX ticker.

    Returns a list of dicts with: time, open, high, low, close, volume.
    Uses yfinance as the primary (and only) source for SGX.
    """
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)

    try:
        t = yf.Ticker(yf_code)
        hist = t.history(start=start_date, end=end_date)

        if hist.empty:
            return []

        prices = []
        for idx, row in hist.iterrows():
            c = _parse_float(row.get("Close"))
            if c is None:
                continue
            prices.append({
                "time": idx.strftime("%Y-%m-%d"),
                "open": _parse_float(row.get("Open")),
                "high": _parse_float(row.get("High")),
                "low": _parse_float(row.get("Low")),
                "close": round(c, 4),
                "volume": int(row.get("Volume", 0)),
            })
        return prices

    except Exception as e:
        print(f"  [sg/prices] Error fetching {yf_code}: {e}")
        return []
