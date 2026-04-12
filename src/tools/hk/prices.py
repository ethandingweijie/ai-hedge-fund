"""
HK price data — yfinance primary, Sina Finance fallback.

Primary source: yfinance
  - Accepts start/end date range — only fetches the rows needed (~0.9 s)
  - Globally accessible

Fallback: ak.stock_hk_daily (Sina Finance)
  - Downloads FULL history (~1,800+ rows) then filters in pandas
  - Cold-start latency ~3 s; warm ~0.2 s
  - Used when yfinance raises any exception or returns no rows

Returns list[Price] — same type as the US path.
"""
from __future__ import annotations

import logging

from src.data.models import Price
from src.tools.hk._utils import _parse_float
from src.tools.hk.ticker import to_akshare_code, to_yfinance_code

_log = logging.getLogger(__name__)


# ── Primary: Sina Finance (stock_hk_daily) ────────────────────────────────────

def _prices_via_sina(ticker: str, start_date: str, end_date: str) -> list[Price]:
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("akshare not installed")

    symbol = to_akshare_code(ticker)   # e.g. "06862"

    # stock_hk_daily returns the full history in one call — filter afterwards
    df = ak.stock_hk_daily(symbol=symbol, adjust="qfq")

    if df is None or df.empty:
        return []

    # Columns are already English: date, open, high, low, close, volume
    # Normalise the date column to string "YYYY-MM-DD" so comparison always works
    # (stock_hk_daily may return datetime.date objects instead of strings)
    df["date"] = df["date"].astype(str).str[:10]
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

    if df.empty:
        return []

    prices: list[Price] = []
    for _, row in df.iterrows():
        try:
            prices.append(
                Price(
                    open=float(row["open"]),
                    close=float(row["close"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    volume=int(_parse_float(row.get("volume")) or 0),
                    time=str(row["date"])[:10],
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            _log.debug("Skipping Sina price row for %s: %s", symbol, exc)
    return prices


# ── Fallback: yfinance ────────────────────────────────────────────────────────

def _prices_via_yfinance(ticker: str, start_date: str, end_date: str) -> list[Price]:
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance not installed")

    yf_sym = to_yfinance_code(ticker)   # e.g. "6862.HK"
    hist   = yf.Ticker(yf_sym).history(start=start_date, end=end_date, auto_adjust=True)

    if hist is None or hist.empty:
        return []

    prices: list[Price] = []
    for ts, row in hist.iterrows():
        try:
            prices.append(
                Price(
                    open=float(row["Open"]),
                    close=float(row["Close"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    volume=int(row.get("Volume") or 0),
                    time=str(ts)[:10],
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            _log.debug("Skipping yfinance price row for %s: %s", yf_sym, exc)
    return prices


# ── Public API ────────────────────────────────────────────────────────────────

def get_hk_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> list[Price]:
    """
    Fetch daily OHLCV for an HK-listed stock.

    Tries Sina Finance (stock_hk_daily) first — it is globally accessible
    and returns forward-adjusted data.  Falls back to yfinance if Sina raises
    any exception or returns no rows for the requested date range.

    Parameters
    ----------
    ticker     : canonical "NNNNN.HK" or any valid HK format
    start_date : "YYYY-MM-DD"
    end_date   : "YYYY-MM-DD"

    Returns
    -------
    list[Price]  — empty list only when both sources fail
    """
    symbol = to_akshare_code(ticker)

    # ── Try yfinance (date-range, no full-history download) ───────────────────
    try:
        prices = _prices_via_yfinance(ticker, start_date, end_date)
        if prices:
            return prices
        _log.debug("yfinance returned no rows for %s in range %s–%s, trying Sina",
                   symbol, start_date, end_date)
    except Exception as exc:
        _log.warning(
            "yfinance failed for %s: %s — falling back to Sina stock_hk_daily",
            symbol, exc,
        )

    # ── Fallback: Sina Finance (downloads full history, then filters) ─────────
    try:
        prices = _prices_via_sina(ticker, start_date, end_date)
        if prices:
            _log.info("Sina supplied %d rows for %s", len(prices), symbol)
        else:
            _log.warning("Sina also returned no rows for %s", symbol)
        return prices
    except Exception as exc:
        _log.error("Sina fallback also failed for %s: %s", symbol, exc)
        return []
