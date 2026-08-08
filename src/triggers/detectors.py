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
from src.tools.api import _edgar_get, _get_cik, get_earnings_surprises, get_insider_trades_edgar

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


# ── Detector 4: New SEC filing of a given form type ────────────────────────
# Used for the 100-Question screener's event-triggered rescoring (see
# src/research_ideas/hundred_q/questions_registry.py::TRIGGER_TO_QUESTIONS).
# One call checks ONE form type — the caller loops over ("10-K", "10-Q",
# "DEF 14A") and maps each result to its own trigger_type ("new_10k",
# "new_10q", "new_def14a"), since each maps to a different pillar subset.

def new_edgar_filing(
    ticker: str,
    form_type: str,
) -> tuple[bool, str, str]:
    """
    Fires when the latest filing of `form_type` (e.g. "10-K", "10-Q",
    "DEF 14A") has an accession number not yet seen for this
    ticker+form_type combination.

    state_key = the accession number itself (not a date) — the caller
    passes this to already_fired()/mark_fired() so the trigger only
    re-fires when a genuinely NEW filing of this type appears, no matter
    how many times the monitor sweep runs in between.

    Returns (fired, reason, accession_number). Returns (False, "", "")
    if no CIK match or no filing of this type is found.
    """
    cik = _get_cik(ticker)
    if not cik:
        return False, "", ""

    cik_padded = cik.zfill(10)
    subs = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    if not isinstance(subs, dict):
        return False, "", ""

    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])

    for form, accession, filed in zip(forms, accessions, filing_dates):
        if form == form_type:
            reason = f"New {form_type} filed {filed} (accession {accession})"
            return True, reason, accession

    return False, "", ""


# ── Detector 5: Insider net-buy dollar threshold ────────────────────────────

def form4_net_buy(
    ticker: str,
    threshold_usd: float = 100_000,
    lookback_days: int = 2,
) -> tuple[bool, str, str]:
    """
    Fires when net insider open-market buying (buys minus sells, in
    dollars) over the last `lookback_days` exceeds threshold_usd.

    Distinct from fresh_form4() above, which fires on a headcount-based
    cluster-buy signal (>=N distinct buyers in 30 days) — this is the
    user-specified dollar-threshold trigger for the 100-Question
    screener's governance pillar (see the approved plan's worked example:
    "Form-4 net-buy > $100k fires only the insider-narrative sub-question,
    not the whole governance pillar").

    Returns (fired, reason, today_str).
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    since = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    try:
        trades = get_insider_trades_edgar(ticker, since, today_str)
        if not trades:
            return False, "", today_str

        net_usd = 0.0
        for t in trades:
            shares = t.transaction_shares or 0
            value = t.transaction_value or 0.0
            if shares > 0:
                net_usd += abs(value)
            elif shares < 0:
                net_usd -= abs(value)

        if net_usd > threshold_usd:
            reason = f"Insider net buy ${net_usd:,.0f} over last {lookback_days}d (> ${threshold_usd:,.0f} threshold)"
            return True, reason, today_str

    except Exception as exc:
        print(f"  [trigger:form4_net_buy] {ticker} — error: {exc}")

    return False, "", today_str


# ── Detector 6: Earnings just reported ──────────────────────────────────────

def earnings_reported(
    ticker: str,
    lookback_days: int = 5,
) -> tuple[bool, str, str]:
    """
    Fires when this ticker reported earnings within the last
    `lookback_days` days — the inverse of earnings_soon() (which fires
    BEFORE an upcoming earnings event; this fires AFTER one has landed).

    state_key = the earnings date itself, so the trigger only fires once
    per actual earnings event even if the monitor runs multiple days
    after it (same convention as earnings_soon's state_key).

    Returns (fired, reason, earnings_date_str). Returns (False, "", "")
    if no recent earnings report is found.
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    try:
        surprises = get_earnings_surprises(ticker, end_date=today_str, limit=1)
        if not surprises:
            return False, "", ""

        latest = surprises[0]
        report_date_str = latest.get("date", "")
        if not report_date_str:
            return False, "", ""

        report_date = date.fromisoformat(report_date_str)
        days_since = (today - report_date).days
        if 0 <= days_since <= lookback_days:
            beat_str = "beat" if latest.get("beat") else "missed"
            reason = (
                f"Earnings reported {report_date_str} ({days_since}d ago) — "
                f"{beat_str} estimates (EPS {latest.get('eps_actual')} vs est {latest.get('eps_estimated')})"
            )
            return True, reason, report_date_str

    except Exception as exc:
        print(f"  [trigger:earnings_reported] {ticker} — error: {exc}")

    return False, "", ""
