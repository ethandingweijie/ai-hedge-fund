"""
HK insider / executive shareholding changes.

Primary:  yfinance .insider_transactions  (best available for HKEX)
Fallback: empty list (AKShare stock_ggcg_em is A-share focused)

Returns list[InsiderTrade] — same type as the US path.
"""
from __future__ import annotations

import logging

from src.data.models import InsiderTrade
from src.tools.hk.ticker import to_canonical, to_yfinance_code

_log = logging.getLogger(__name__)


def get_hk_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    """
    Fetch insider / executive trade disclosures for an HK-listed stock.

    Coverage note
    -------------
    yfinance insider data for HK names is reliable for large-cap stocks
    (Tencent, Alibaba, HSBC, AIA etc.) but sparse for small/mid-cap plays.
    The function degrades gracefully — returns [] when no data is available
    rather than raising an error.

    Parameters
    ----------
    ticker     : any valid HK ticker format
    end_date   : "YYYY-MM-DD"
    start_date : "YYYY-MM-DD" or None
    limit      : max rows to return

    Returns
    -------
    list[InsiderTrade] — empty on any error or no data
    """
    trades = _from_yfinance(ticker, end_date, start_date, limit)
    return trades


# ---------------------------------------------------------------------------
# yfinance implementation
# ---------------------------------------------------------------------------

def _from_yfinance(
    ticker: str,
    end_date: str,
    start_date: str | None,
    limit: int,
) -> list[InsiderTrade]:
    try:
        import yfinance as yf
    except ImportError:
        _log.error("yfinance not installed — cannot fetch HK insider trades")
        return []

    yf_sym = to_yfinance_code(ticker)
    canonical = to_canonical(ticker)

    try:
        t = yf.Ticker(yf_sym)
        df = t.insider_transactions
    except Exception as exc:
        _log.debug("yfinance insider_transactions failed for %s: %s", yf_sym, exc)
        return []

    if df is None or df.empty:
        return []

    trades: list[InsiderTrade] = []
    for _, row in df.iterrows():
        try:
            # yfinance returns Timestamps; coerce to string
            raw_date = row.get("Start Date") or row.get("Date")
            if raw_date is None:
                continue
            filing_date = str(raw_date)[:10]

            # Date filters
            if filing_date > end_date:
                continue
            if start_date and filing_date < start_date:
                continue

            shares = row.get("Shares")
            transaction_type = str(row.get("Transaction") or "").lower()
            if shares is not None:
                shares = float(shares)
                # yfinance reports shares as positive; apply sign from transaction type
                if any(w in transaction_type for w in ("sale", "sell", "disposed")):
                    shares = -abs(shares)
                else:
                    shares = abs(shares)

            value = row.get("Value")
            trade_value = float(value) if value is not None else None
            # Apply sign to value too
            if trade_value is not None and shares is not None and shares < 0:
                trade_value = -abs(trade_value)

            trades.append(
                InsiderTrade(
                    ticker=canonical,
                    issuer=None,
                    name=str(row.get("Insider") or "").strip() or None,
                    title=str(row.get("Position") or "").strip() or None,
                    is_board_director=None,
                    transaction_date=filing_date,
                    transaction_shares=shares,
                    transaction_price_per_share=None,
                    transaction_value=trade_value,
                    shares_owned_before_transaction=None,
                    shares_owned_after_transaction=None,
                    security_title=None,
                    filing_date=filing_date,
                )
            )
        except Exception as exc:
            _log.debug("Skipping insider row for %s: %s", yf_sym, exc)
            continue

    trades.sort(key=lambda t: t.filing_date, reverse=True)
    return trades[:limit]
