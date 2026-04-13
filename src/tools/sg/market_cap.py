"""
SGX market cap — yfinance primary.
"""

from typing import Optional
from src.tools.sg.ticker import to_yfinance_code


def get_sg_market_cap(ticker: str) -> Optional[float]:
    """Return market cap in SGD for an SGX ticker. None if unavailable."""
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)
    try:
        t = yf.Ticker(yf_code)
        mcap = t.info.get("marketCap")
        return float(mcap) if mcap else None
    except Exception:
        return None
