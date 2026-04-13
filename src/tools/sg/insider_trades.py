"""
SGX insider trades — yfinance .insider_transactions.
Coverage is sparse for SGX (same as HK) but best available free source.
"""

from src.tools.sg.ticker import to_yfinance_code
from src.tools.sg._utils import _parse_float


def get_sg_insider_trades(
    ticker: str,
    start_date: str = "",
    end_date: str = "",
    limit: int = 50,
) -> list[dict]:
    """Fetch insider transactions for an SGX ticker. Returns [] if none available."""
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)

    try:
        t = yf.Ticker(yf_code)
        df = t.insider_transactions
        if df is None or df.empty:
            return []

        trades = []
        for _, row in df.head(limit).iterrows():
            txn_type = str(row.get("Transaction", "")).lower()
            shares = _parse_float(row.get("Shares"))
            value = _parse_float(row.get("Value"))

            # Negative for sales
            if shares and any(w in txn_type for w in ("sale", "sell", "disposed")):
                shares = -abs(shares)
                if value:
                    value = -abs(value)

            date_val = row.get("Start Date")
            date_str = str(date_val)[:10] if date_val else ""

            # Date filtering
            if start_date and date_str < start_date:
                continue
            if end_date and date_str > end_date:
                continue

            trades.append({
                "ticker": ticker,
                "name": str(row.get("Insider", "")),
                "title": str(row.get("Position", "")),
                "transaction_date": date_str,
                "transaction_shares": shares,
                "transaction_value": value,
                "filing_date": date_str,
            })

        return trades

    except Exception:
        return []
