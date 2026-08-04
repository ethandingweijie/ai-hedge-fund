"""
src/research_ideas/fundflow/data_fetch.py
==========================================
Per-ETF data for the geographic fund-flow screen.

Two independent feeds, deliberately kept separate because their reliability
differs by an order of magnitude:

  1. OHLCV bars  (/stable/historical-price-eod/full)
     Daily, complete, uniform across every ETF in the universe. This is what
     the flow ENGINE runs on — signed dollar flow is derived from where each
     day closes inside its own range, weighted by dollar volume.

  2. Shares outstanding, implied from market cap  (/stable/historical-market-
     capitalization, divided by that day's close)
     Gives TRUE creation/redemption flow — the number an ETF issuer reports.
     Except FMP refreshes shares on its own cadence, per ticker: EWJ/EWH move
     most days, MCHI/VGK ~monthly, and INDA/EWY have shown a single frozen
     figure for years. A frozen series would silently print "zero flow", which
     is worse than printing nothing, so `implied_flow_quality` grades each
     symbol by how many change-days its shares series actually contains and
     the scorer keeps implied flow OUT of the composite entirely.

  fetch_flow_bundle(symbol, as_of=None, lookback_days=540) -> FlowBundle | None
  fetch_aum(symbol) -> float | None
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.research_ideas.momentum.data_fetch import fetch_price_series
from src.tools.api import _fmp_get, _STABLE


logger = logging.getLogger(__name__)


# A shares series needs at least this many distinct change-days over the
# fetched window before implied creation/redemption flow is trustworthy
# enough to display. Below it the feed is a stale snapshot, not a series.
_MIN_SHARE_CHANGE_DAYS = 8
_GOOD_SHARE_CHANGE_DAYS = 30


@dataclass
class FlowBundle:
    """One ETF's bars plus its (optional) implied share-count series."""
    symbol: str
    df: Optional[pd.DataFrame] = None          # OHLCV, chronological, DatetimeIndex
    has_true_ohlc: bool = False                # False = close-only feed, flow unusable
    aum: Optional[float] = None                # latest assets under management, USD
    expense_ratio: Optional[float] = None
    name: str = ""
    as_of: Optional[str] = None
    bars: int = 0
    # Implied creation/redemption series, aligned to df.index. NaN where the
    # market-cap feed had no row for that session.
    shares: Optional[pd.Series] = None
    share_change_days: int = 0
    implied_flow_quality: str = "none"         # "good" | "partial" | "stale" | "none"
    notes: list[str] = field(default_factory=list)


def _fetch_market_cap(symbol: str, start: str, end: str) -> Optional[pd.Series]:
    """Daily market cap (= shares x close for an ETF). None on failure."""
    data = _fmp_get(
        f"{_STABLE}/historical-market-capitalization",
        {"symbol": symbol, "from": start, "to": end, "limit": 5000},
        api_key=None,
        uncap=True,
    )
    if not isinstance(data, list) or not data:
        return None
    rows: dict[pd.Timestamp, float] = {}
    for r in data:
        d, mc = r.get("date"), r.get("marketCap")
        if not d or mc in (None, 0):
            continue
        try:
            rows[pd.Timestamp(d)] = float(mc)
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    return pd.Series(rows).sort_index()


def fetch_aum(symbol: str) -> Optional[tuple[float, Optional[float], str]]:
    """Latest (AUM, expense_ratio, name) from /stable/etf/info. None on failure."""
    data = _fmp_get(f"{_STABLE}/etf/info", {"symbol": symbol}, api_key=None, uncap=True)
    if not isinstance(data, list) or not data:
        return None
    row = data[0] or {}
    aum = row.get("assetsUnderManagement")
    if aum in (None, 0):
        return None
    try:
        return (
            float(aum),
            float(row["expenseRatio"]) if row.get("expenseRatio") is not None else None,
            str(row.get("name") or symbol),
        )
    except (TypeError, ValueError):
        return None


def _grade_shares(shares: pd.Series) -> tuple[int, str]:
    """Count change-days and grade the series' usability."""
    if shares is None or len(shares) < 30:
        return 0, "none"
    prev = shares.shift(1)
    # Relative change guards against float noise in the mc/close division.
    changed = ((shares - prev).abs() / prev.abs().clip(lower=1.0)) > 1e-5
    n = int(changed.fillna(False).sum())
    if n >= _GOOD_SHARE_CHANGE_DAYS:
        return n, "good"
    if n >= _MIN_SHARE_CHANGE_DAYS:
        return n, "partial"
    return n, "stale"


# ~3.2 years of calendar days (≈790 sessions). The binding constraint is the
# 1-year flow window: a 252-session CMF needs 252 bars before it produces its
# first value, and z-scoring that against its own trailing year needs 252 more.
# The old 540-day fetch covered the 1-month window comfortably and could not
# have supported the 6-month or 1-year ones at all.
_DEFAULT_LOOKBACK_DAYS = 1150


def fetch_flow_bundle(
    symbol: str,
    as_of: Optional[str] = None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> Optional[FlowBundle]:
    """
    Pull everything the flow engine needs for one ETF. Returns None only when
    there are no usable price bars — a missing market-cap or AUM feed degrades
    the bundle (implied_flow_quality / aum go empty) rather than dropping it.
    """
    price = fetch_price_series(symbol, as_of=as_of, lookback_days=lookback_days)
    if price is None or price.df is None or price.df.empty:
        return None

    df = price.df
    bundle = FlowBundle(
        symbol=symbol,
        df=df,
        has_true_ohlc=price.has_true_ohlc,
        as_of=price.as_of,
        bars=price.bars,
    )
    if not price.has_true_ohlc:
        bundle.notes.append(
            f"{symbol}: close-only price feed — intraday close-location flow "
            "unavailable, dollar-volume direction falls back to daily return sign"
        )

    # ── AUM (normalises flow into "% of assets", the unit issuers report) ──
    try:
        info = fetch_aum(symbol)
        if info:
            bundle.aum, bundle.expense_ratio, bundle.name = info
    except Exception as exc:
        logger.warning("fundflow: AUM fetch failed for %s: %s", symbol, exc)

    # ── Implied shares outstanding (true creation/redemption, when live) ──
    end_d = date.fromisoformat(as_of) if as_of else date.today()
    start_d = end_d - timedelta(days=lookback_days)
    try:
        mc = _fetch_market_cap(symbol, start_d.isoformat(), end_d.isoformat())
        if mc is not None and not mc.empty:
            close = df["close"]
            aligned = mc.reindex(close.index)
            shares = (aligned / close).dropna()
            if len(shares) >= 30:
                n, quality = _grade_shares(shares)
                bundle.shares = shares.reindex(close.index)
                bundle.share_change_days = n
                bundle.implied_flow_quality = quality
                if quality == "stale":
                    bundle.notes.append(
                        f"{symbol}: shares-outstanding feed changed on only {n} of "
                        f"{len(shares)} sessions — implied creation/redemption flow "
                        "suppressed as stale"
                    )
    except Exception as exc:
        logger.warning("fundflow: market-cap fetch failed for %s: %s", symbol, exc)

    return bundle
