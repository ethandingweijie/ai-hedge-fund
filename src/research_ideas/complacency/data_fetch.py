"""
src/research_ideas/complacency/data_fetch.py
=============================================
FMP wrappers for the inputs needed by the 4-pillar complacency scorer.

Endpoints (per spec §2.1):
  /key-metrics-ttm        — EV/Sales, FCF yield TTM
  /financial-scores       — Altman Z, Piotroski
  /quote                  — price, 50DMA, 200DMA, 52w hi/lo, mktcap
  /historical-price-eod   — RSI (14-week) computed locally from weekly closes
  /analyst-estimates      — forward EPS for revision proxy
  /ratios?period=annual   — EPS history for revision tracking
  /insider-trading/search — A/D ratio (FMP Ultimate gating; falls back to None)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from src.tools.api import (
    _fmp_get,
    _safe_float,
    _STABLE,
    get_analyst_estimates,
)


@dataclass
class ComplacencyBundle:
    ticker: str
    name: str = ""
    sector: Optional[str] = None
    industry: Optional[str] = None
    # Quote
    price: Optional[float] = None
    market_cap: Optional[float] = None
    sma_50d: Optional[float] = None
    sma_200d: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    # Key metrics TTM
    ev_sales: Optional[float] = None
    fcf_yield_ttm: Optional[float] = None
    # Financial scores
    altman_z: Optional[float] = None
    piotroski: Optional[int] = None
    # Insider stats
    ad_ratio_4q_avg: Optional[float] = None
    # Forward estimates / revisions
    eps_revision_yoy: Optional[float] = None
    # Technicals (computed locally)
    rsi_weekly: Optional[float] = None
    # Derived
    sma200_extension: Optional[float] = None
    range_position: Optional[float] = None


# ─── FMP fetchers ──────────────────────────────────────────────────────────


def _fetch_quote(ticker: str) -> Optional[dict]:
    data = _fmp_get(f"{_STABLE}/quote", {"symbol": ticker}, api_key=None, uncap=True)
    if isinstance(data, list) and data:
        return data[0]
    return None


def _fetch_key_metrics_ttm(ticker: str) -> Optional[dict]:
    data = _fmp_get(
        f"{_STABLE}/key-metrics-ttm",
        {"symbol": ticker, "limit": 1},
        api_key=None,
        uncap=True,
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


def _fetch_financial_scores(ticker: str) -> Optional[dict]:
    data = _fmp_get(
        f"{_STABLE}/financial-scores",
        {"symbol": ticker, "limit": 1},
        api_key=None,
        uncap=True,
    )
    if isinstance(data, list) and data:
        return data[0]
    return None


def _fetch_weekly_close_history(ticker: str, weeks: int = 30) -> list[float]:
    """Daily EOD prices over ~weeks*7 days, downsampled to weekly closes."""
    end = date.today()
    start = end - timedelta(days=weeks * 7 + 30)  # buffer for holidays
    data = _fmp_get(
        f"{_STABLE}/historical-price-eod/light",
        {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat()},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return []
    # FMP returns newest-first; reverse to chronological order
    rows = sorted(data, key=lambda r: r.get("date", ""))
    # Downsample to Friday closes (or last entry per ISO week)
    weekly: dict[str, float] = {}
    for r in rows:
        d = r.get("date")
        p = _safe_float(r.get("price"))
        if not d or p is None:
            continue
        try:
            iso_year, iso_week, _ = date.fromisoformat(d).isocalendar()
        except ValueError:
            continue
        weekly[f"{iso_year}-W{iso_week:02d}"] = p   # last entry per week wins
    closes = [weekly[k] for k in sorted(weekly)]
    return closes[-weeks:] if len(closes) > weeks else closes


def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Standard Wilder RSI on a list of closes (oldest → newest)."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # Seed with simple averages over first `period`
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing over the rest
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _fetch_eps_history(ticker: str) -> list[Optional[float]]:
    """Annual EPS diluted, oldest→newest, from /stable/ratios."""
    data = _fmp_get(
        f"{_STABLE}/income-statement",
        {"symbol": ticker, "period": "annual", "limit": 3},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return []
    rows = sorted(data, key=lambda r: r.get("date", ""))
    return [_safe_float(r.get("epsDiluted")) for r in rows]


def _fetch_insider_ad_ratio(ticker: str) -> Optional[float]:
    """
    Insider acquired/disposed ratio (4-quarter average). Requires FMP
    Ultimate plan; returns None on lower tiers (endpoint 403s).
    """
    end = date.today()
    start = end - timedelta(days=365)
    data = _fmp_get(
        f"{_STABLE}/insider-trading/search",
        {"symbol": ticker, "page": 0, "limit": 100},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    a_shares = 0.0
    d_shares = 0.0
    for row in data:
        tx_type = row.get("transactionType", "") or ""
        # Discretionary open-market only
        if tx_type not in ("P-Purchase", "S-Sale"):
            continue
        acq = row.get("acquisitionOrDisposition", "")
        shares = abs(_safe_float(row.get("securitiesTransacted")) or 0)
        if acq == "A":
            a_shares += shares
        elif acq == "D":
            d_shares += shares
    if (a_shares + d_shares) == 0:
        return None
    return a_shares / (a_shares + d_shares + 1e-9)


# ─── Public bundler ────────────────────────────────────────────────────────


def fetch_ticker_bundle(ticker: str, meta: dict) -> Optional[ComplacencyBundle]:
    """Pull everything needed to score `ticker` against the 4 pillars."""
    bundle = ComplacencyBundle(
        ticker=ticker,
        name=meta.get("name", ticker),
        sector=meta.get("sector"),
        industry=meta.get("industry"),
    )

    # Quote (price, MAs, 52w range, market cap)
    q = _fetch_quote(ticker)
    time.sleep(0.10)
    if not q:
        return None
    bundle.price = _safe_float(q.get("price"))
    bundle.market_cap = _safe_float(q.get("marketCap"))
    bundle.sma_50d = _safe_float(q.get("priceAvg50"))
    bundle.sma_200d = _safe_float(q.get("priceAvg200"))
    bundle.week52_high = _safe_float(q.get("yearHigh"))
    bundle.week52_low = _safe_float(q.get("yearLow"))

    # Key metrics TTM (EV/Sales, FCF yield)
    km = _fetch_key_metrics_ttm(ticker)
    time.sleep(0.10)
    if km:
        bundle.ev_sales = _safe_float(km.get("evToSales") or km.get("enterpriseValueOverSales"))
        bundle.fcf_yield_ttm = _safe_float(km.get("freeCashFlowYield"))

    # Financial scores
    fs = _fetch_financial_scores(ticker)
    time.sleep(0.10)
    if fs:
        bundle.altman_z = _safe_float(fs.get("altmanZScore"))
        pio = fs.get("piotroskiScore")
        if pio is not None:
            try:
                bundle.piotroski = int(pio)
            except (TypeError, ValueError):
                pass

    # Insider A/D ratio (paid endpoint; falls back to None)
    try:
        bundle.ad_ratio_4q_avg = _fetch_insider_ad_ratio(ticker)
    except Exception:
        bundle.ad_ratio_4q_avg = None
    time.sleep(0.10)

    # Forward EPS revision proxy: compare consensus Y+1 EPS to latest reported
    try:
        today = date.today().isoformat()
        estimates = get_analyst_estimates(ticker, end_date=today, period="annual", limit=1)
        eps_hist = _fetch_eps_history(ticker)
        if estimates and eps_hist:
            latest_eps = eps_hist[-1] if eps_hist[-1] is not None else None
            fwd_eps = estimates[0].eps_avg
            if latest_eps and fwd_eps is not None and latest_eps != 0:
                bundle.eps_revision_yoy = (fwd_eps / latest_eps) - 1.0
    except Exception:
        bundle.eps_revision_yoy = None
    time.sleep(0.10)

    # Weekly RSI (computed locally from EOD light)
    try:
        closes = _fetch_weekly_close_history(ticker, weeks=30)
        bundle.rsi_weekly = _compute_rsi(closes, period=14)
    except Exception:
        bundle.rsi_weekly = None
    time.sleep(0.10)

    # Derived: SMA-200 extension and 52w range position
    if bundle.price and bundle.sma_200d and bundle.sma_200d > 0:
        bundle.sma200_extension = (bundle.price - bundle.sma_200d) / bundle.sma_200d
    if (
        bundle.price is not None
        and bundle.week52_high is not None
        and bundle.week52_low is not None
        and bundle.week52_high > bundle.week52_low
    ):
        bundle.range_position = (bundle.price - bundle.week52_low) / (
            bundle.week52_high - bundle.week52_low
        )

    return bundle
