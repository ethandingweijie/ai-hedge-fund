"""
Trigger detectors for the event-driven monitor.

Each detector is stateless — it checks one condition and returns a result.
Cooldown / deduplication is handled by monitor.py + state.py.

Return type for all detectors: (fired: bool, reason: str, state_key: str)
  fired     : True if the condition is met
  reason    : human-readable description of what fired (empty string if not fired)
  state_key : the date string used as the cooldown key in trigger_state.json
              (today's date for price_shock/form4; earnings date for earnings_soon)
"""

import os
from datetime import date, timedelta

import requests
from src.tools.api import get_insider_trades_edgar

_STABLE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 12   # seconds per HTTP call


def _api_key() -> str:
    return (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
        or ""
    )


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


# ── Detector 1: Price shock ────────────────────────────────────────────────────

def price_shock(
    ticker: str,
    threshold_pct: float = 5.0,
) -> tuple[bool, str, str]:
    """
    Fires when today's price change (via FMP /stable/quote) exceeds threshold_pct
    in either direction.

    Uses FMP's live `changesPercentage` field — reflects the most recent session's
    move vs. the prior close (pre-market check captures the full prior day's move).

    Returns (fired, reason, today_str).
    """
    today_str = _today()
    try:
        resp = requests.get(
            f"{_STABLE}/quote/{ticker}",
            params={"apikey": _api_key()},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"  [trigger:price_shock] {ticker} — HTTP {resp.status_code}")
            return False, "", today_str

        data = resp.json()
        if isinstance(data, list):
            data = data[0] if data else {}

        change = float(data.get("changesPercentage", 0.0) or 0.0)
        if abs(change) >= threshold_pct:
            direction = "UP" if change > 0 else "DOWN"
            reason = f"Price shock {direction} {change:+.1f}% (≥ {threshold_pct:.1f}% threshold)"
            return True, reason, today_str

    except Exception as exc:
        print(f"  [trigger:price_shock] {ticker} — error: {exc}")

    return False, "", today_str


# ── Detector 2: Earnings within N days (pre-emptive) ──────────────────────────

def earnings_soon(
    ticker: str,
    days_ahead: int = 7,
) -> tuple[bool, str, str]:
    """
    Fires when this ticker has an earnings event scheduled within `days_ahead` days.

    Pre-emptive trigger: runs the pipeline to refresh the thesis *before* the
    binary event, regardless of whether a price shock has occurred.

    State key = the earnings date itself (not today), so the pipeline is only
    re-run once per earnings event even if the monitor runs multiple days before it.

    Returns (fired, reason, earnings_date_str).
    Returns (False, "", "") if no upcoming earnings found.
    """
    today = date.today()
    from_date = today.strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            f"{_STABLE}/earnings-calendar",
            params={"from": from_date, "to": to_date, "apikey": _api_key()},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"  [trigger:earnings_soon] {ticker} — HTTP {resp.status_code}")
            return False, "", ""

        events = resp.json() or []
        for ev in events:
            if ev.get("symbol", "").upper() == ticker.upper():
                earnings_date = ev.get("date", "")
                if not earnings_date:
                    continue
                eps_est = ev.get("epsEstimated")
                hour    = ev.get("hour", "")         # "bmo" | "amc" | ""
                eps_str  = f", EPS est. ${eps_est:.2f}" if isinstance(eps_est, (int, float)) else ""
                hour_str = f" ({hour.upper()})" if hour else ""
                days_out = (date.fromisoformat(earnings_date) - today).days
                reason = (
                    f"Earnings in {days_out}d on {earnings_date}{hour_str}{eps_str} "
                    f"— pre-emptive pipeline refresh"
                )
                return True, reason, earnings_date

    except Exception as exc:
        print(f"  [trigger:earnings_soon] {ticker} — error: {exc}")

    return False, "", ""


# ── Detector 3: Fresh Form 4 insider cluster buy ───────────────────────────────

def fresh_form4(
    ticker: str,
    lookback_days: int = 2,
    cluster_threshold: int = 2,
) -> tuple[bool, str, str]:
    """
    Fires when a new Form 4 insider buy filing appeared in the last `lookback_days`
    days AND a cluster buy exists (≥ cluster_threshold distinct insiders bought
    in the last 30 days).

    Uses SEC EDGAR directly (free, no API key). Mirrors the cluster-buy logic
    in insider_activity_agent.py.

    Returns (fired, reason, today_str).
    """
    today      = date.today()
    today_str  = today.strftime("%Y-%m-%d")
    fresh_from = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    window_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        # Check for fresh filings first — if none, skip the 30-day cluster query
        fresh_trades = get_insider_trades_edgar(ticker, fresh_from, today_str)
        fresh_buys = [
            t for t in fresh_trades
            if (t.transaction_shares or 0) > 0   # positive shares = open-market buy
        ]
        if not fresh_buys:
            return False, "", today_str

        # Cluster check over 30-day window
        all_trades = get_insider_trades_edgar(ticker, window_from, today_str)
        buyers: set[str] = set()
        total_value = 0.0
        for t in all_trades:
            if (t.transaction_shares or 0) > 0:
                name = (t.name or "Unknown").strip()
                buyers.add(name)
                total_value += t.transaction_value or 0.0

        if len(buyers) >= cluster_threshold:
            val_str = f" (${total_value / 1e6:.1f}M total)" if total_value else ""
            reason = (
                f"Insider Cluster Buy — {len(buyers)} insiders bought in last 30d"
                f"{val_str} (fresh filing ≤{lookback_days}d ago)"
            )
            return True, reason, today_str

    except Exception as exc:
        print(f"  [trigger:fresh_form4] {ticker} — error: {exc}")

    return False, "", today_str
