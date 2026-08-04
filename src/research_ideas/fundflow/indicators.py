"""
src/research_ideas/fundflow/indicators.py
==========================================
Flow math for the geographic fund-flow screen.

Everything here operates on DOLLARS, not index points, so the numbers are
comparable across a $650bn US complex and a $320m Indonesia fund once
normalised by assets.

The primitive is signed dollar flow per session:

    CLV_t  = ((C - L) - (H - C)) / (H - L)          in [-1, +1]
    flow_t = CLV_t x volume_t x close_t             in dollars

CLV (close location value, Chaikin) asks where in the day's range the tape
settled: a close on the high means buyers absorbed everything offered
(accumulation, +1); a close on the low means sellers cleared the book
(distribution, -1). Weighting it by dollar volume turns a shape into a
magnitude. Summed over a window it estimates net dollars accumulated — the
same quantity ETF issuers report as net flow, derived from the tape instead
of from the creation/redemption ledger.

Region-level aggregation sums the raw dollar series across every ETF in the
basket BEFORE computing any ratio, so a region's reading is money-weighted:
SPY's dollars swamp VTI's in the US row exactly as they should, without an
arbitrary weighting scheme.

  signed_dollar_flow(df, has_true_ohlc)      -> pd.Series (USD/session)
  dollar_volume(df)                          -> pd.Series (USD/session)
  implied_creation_flow(shares, close)       -> pd.Series (USD/session)
  aggregate_basket(bundles)                  -> RegionSeries
  flow_metrics(rs, window)                   -> dict of the scored reads
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# Above this share of basket assets sitting behind a frozen share-count feed,
# the aggregated creation/redemption number stops being a measurement of the
# region and is suppressed.
_MAX_STALE_AUM_SHARE = 0.35


# ─── Per-ETF primitives ─────────────────────────────────────────────────────


def close_location_value(df: pd.DataFrame) -> pd.Series:
    """
    Chaikin CLV in [-1, +1]. Sessions that print high == low (a halt, or a
    close-only feed) carry no location information and return 0 rather than
    dividing by zero.
    """
    high, low, close = df["high"], df["low"], df["close"]
    rng = (high - low)
    clv = ((close - low) - (high - close)) / rng.replace(0.0, np.nan)
    return clv.fillna(0.0).clip(-1.0, 1.0)


def dollar_volume(df: pd.DataFrame) -> pd.Series:
    """Turnover in dollars — the raw 'how much money changed hands' series."""
    return (df["close"] * df["volume"]).fillna(0.0)


def signed_dollar_flow(df: pd.DataFrame, has_true_ohlc: bool = True) -> pd.Series:
    """
    Direction-signed dollar flow per session.

    With true OHLC the sign and the conviction both come from CLV. On a
    close-only feed there is no intraday range, so we fall back to the sign of
    the daily return — cruder (it is +/-1 with no partial conviction) but it
    keeps the series defined rather than silently zero.
    """
    dv = dollar_volume(df)
    if has_true_ohlc:
        return (close_location_value(df) * dv).fillna(0.0)
    sign = np.sign(df["close"].pct_change().fillna(0.0))
    return (sign * dv).fillna(0.0)


def implied_creation_flow(shares: Optional[pd.Series], close: pd.Series) -> Optional[pd.Series]:
    """
    True creation/redemption flow: the change in shares outstanding valued at
    that session's close. This is the issuer-reported number, and it is the
    only series here that is a measurement rather than an estimate — which is
    exactly why it is reported alongside the composite instead of inside it
    (see data_fetch on feed staleness).
    """
    if shares is None:
        return None
    s = shares.reindex(close.index)
    # Forward-fill so a missing market-cap row reads as "no creation that day"
    # instead of manufacturing a spike when the feed resumes.
    s = s.ffill()
    delta = s.diff()
    return (delta * close).where(delta.notna())


# ─── Region-level aggregation ───────────────────────────────────────────────


@dataclass
class RegionSeries:
    """A geography's basket collapsed into one set of dollar series."""
    region: str
    flow: pd.Series                                  # signed USD/session
    dvol: pd.Series                                  # gross USD turnover/session
    price: pd.Series                                 # AUM-weighted price index (base 100)
    aum: Optional[float] = None                      # total basket AUM, USD
    implied: Optional[pd.Series] = None              # creation/redemption USD/session
    implied_quality: str = "none"                    # good | partial | stale | none
    members: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def aggregate_basket(bundles: list, region: str) -> Optional[RegionSeries]:
    """
    Collapse a list of FlowBundles into one RegionSeries.

    Dollar series are summed on a UNION index and zero-filled: an ETF that did
    not trade on a session contributed no dollars, which is the correct
    reading, whereas an inner join would silently drop whole sessions whenever
    one basket member had a data gap.

    The price index is AUM-weighted across members and rebased to 100, so the
    flow-vs-price divergence check compares like with like.
    """
    usable = [b for b in bundles if b is not None and b.df is not None and not b.df.empty]
    if not usable:
        return None

    idx = usable[0].df.index
    for b in usable[1:]:
        idx = idx.union(b.df.index)
    idx = idx.sort_values()

    flow = pd.Series(0.0, index=idx)
    dvol = pd.Series(0.0, index=idx)
    implied_total = pd.Series(0.0, index=idx)
    implied_any = False
    qualities: list[str] = []
    # Quality has to be weighted by assets, not counted by member. India's
    # basket is INDA ($6.6bn, frozen feed) plus SMIN ($0.8bn, live): counting
    # members would grade it "partial" and print a near-zero flow that looks
    # like a measurement when in truth the fund holding 87% of the money
    # reported nothing.
    stale_aum = 0.0
    graded_aum = 0.0
    notes: list[str] = []
    aum_total = 0.0
    aum_any = False

    # Price index: AUM-weighted average of each member's rebased close.
    weighted_px = pd.Series(0.0, index=idx)
    weight_sum = pd.Series(0.0, index=idx)

    for b in usable:
        f = signed_dollar_flow(b.df, b.has_true_ohlc).reindex(idx).fillna(0.0)
        d = dollar_volume(b.df).reindex(idx).fillna(0.0)
        flow = flow.add(f, fill_value=0.0)
        dvol = dvol.add(d, fill_value=0.0)
        notes.extend(b.notes)

        if b.aum:
            aum_total += float(b.aum)
            aum_any = True

        # Weight the price index by AUM where known, else equally.
        w = float(b.aum) if b.aum else 1.0
        close = b.df["close"].reindex(idx).ffill()
        base = close.dropna()
        if not base.empty and base.iloc[0]:
            rebased = close / base.iloc[0] * 100.0
            mask = rebased.notna()
            weighted_px = weighted_px.add((rebased * w).where(mask, 0.0), fill_value=0.0)
            weight_sum = weight_sum.add(pd.Series(w, index=idx).where(mask, 0.0), fill_value=0.0)

        if b.implied_flow_quality in ("good", "partial"):
            ic = implied_creation_flow(b.shares, b.df["close"])
            if ic is not None:
                implied_total = implied_total.add(ic.reindex(idx).fillna(0.0), fill_value=0.0)
                implied_any = True
        else:
            stale_aum += w
        graded_aum += w
        qualities.append(b.implied_flow_quality)

    price = (weighted_px / weight_sum.replace(0.0, np.nan)).ffill()

    # If the funds with frozen feeds hold most of the basket's money, the
    # summed creation flow is not a measurement of the region and is graded
    # stale so the caller suppresses it rather than printing a false zero.
    stale_share = (stale_aum / graded_aum) if graded_aum > 0 else 1.0
    if not implied_any or stale_share > _MAX_STALE_AUM_SHARE:
        quality = "stale" if qualities else "none"
    elif all(q == "good" for q in qualities):
        quality = "good"
    else:
        quality = "partial"

    return RegionSeries(
        region=region,
        flow=flow,
        dvol=dvol,
        price=price,
        aum=aum_total if aum_any else None,
        implied=implied_total if implied_any else None,
        implied_quality=quality,
        members=[b.symbol for b in usable],
        notes=notes,
    )


# ─── Scored reads ───────────────────────────────────────────────────────────


def cmf(flow: pd.Series, dvol: pd.Series, window: int = 21) -> Optional[float]:
    """
    Chaikin Money Flow: net dollar flow as a fraction of gross turnover over
    the window, in [-1, +1]. Reads as "of every dollar that traded, what share
    was accumulation" — a conviction measure that is scale-free, so Indonesia
    and the US sit on the same axis.
    """
    if flow is None or len(flow) < window:
        return None
    num = float(flow.iloc[-window:].sum())
    den = float(dvol.iloc[-window:].sum())
    if den <= 0:
        return None
    return max(-1.0, min(1.0, num / den))


def cmf_series(flow: pd.Series, dvol: pd.Series, window: int = 21) -> pd.Series:
    """Rolling CMF — used for zero-cross (turn) detection."""
    num = flow.rolling(window).sum()
    den = dvol.rolling(window).sum().replace(0.0, np.nan)
    return (num / den).clip(-1.0, 1.0)


def money_flow_index(flow: pd.Series, window: int = 14) -> Optional[float]:
    """
    Region money-flow index in [0, 100]: accumulation dollars as a share of
    all directional dollars over the window. 50 = balanced tape.
    """
    if flow is None or len(flow) < window:
        return None
    w = flow.iloc[-window:]
    pos = float(w[w > 0].sum())
    neg = float(-w[w < 0].sum())
    if pos + neg <= 0:
        return None
    return 100.0 * pos / (pos + neg)


def net_flow(flow: pd.Series, window: int) -> Optional[float]:
    """Net accumulated dollars over the trailing window."""
    if flow is None or len(flow) < window:
        return None
    return float(flow.iloc[-window:].sum())


def flow_breadth(flow: pd.Series, window: int = 21) -> Optional[float]:
    """
    Share of sessions in the window that were net-accumulation, in [0, 1].
    Separates "one huge print" from "persistent bid" — two very different
    flow regimes that a summed dollar figure alone cannot tell apart.
    """
    if flow is None or len(flow) < window:
        return None
    w = flow.iloc[-window:]
    if len(w) == 0:
        return None
    return float((w > 0).sum()) / float(len(w))


def turnover_surge(dvol: pd.Series, fast: int = 21, slow: int = 63) -> Optional[float]:
    """
    Recent average dollar turnover over its own longer-run average. >1 means
    the geography is drawing more attention than usual, whichever way it is
    trading — this is the 'strength of flow' axis, independent of direction.
    """
    if dvol is None or len(dvol) < slow:
        return None
    f = float(dvol.iloc[-fast:].mean())
    s = float(dvol.iloc[-slow:].mean())
    if s <= 0:
        return None
    return f / s


def flow_z_score(flow: pd.Series, window: int = 21, history: int = 252) -> Optional[float]:
    """
    How unusual the current window's net flow is versus its own last year of
    rolling windows. The comparison is self-referential on purpose: a region
    is measured against its own normal, so a $2bn week means something very
    different for Indonesia than for the US.
    """
    if flow is None or len(flow) < window + 60:
        return None
    rolled = flow.rolling(window).sum().dropna()
    if len(rolled) < 40:
        return None
    hist = rolled.iloc[-history:]
    mu, sd = float(hist.mean()), float(hist.std())
    if sd <= 0:
        return None
    return (float(rolled.iloc[-1]) - mu) / sd


def cmf_z_series(flow: pd.Series, dvol: pd.Series, window: int = 21,
                 history: int = 252) -> pd.Series:
    """
    CMF expressed against the region's OWN trailing distribution.

    This is the single most important transform in the file. Raw CMF carries a
    structural positive bias for equity ETFs — an asset that drifts upward
    closes in the top half of its range more often than not, so every
    geography reads "accumulation" in any normal year and the cross-section
    collapses: nine regions all print +0.05 to +0.15 and the screen cannot
    tell them apart. Measuring each region against its own baseline removes
    the drift and leaves the deviation, which is the part that carries
    information. It also means one fixed threshold is fair across geographies
    whose baselines genuinely differ.
    """
    raw = cmf_series(flow, dvol, window)
    mu = raw.rolling(history, min_periods=60).mean()
    sd = raw.rolling(history, min_periods=60).std()
    return (raw - mu) / sd.replace(0.0, np.nan)


def cmf_z(flow: pd.Series, dvol: pd.Series, window: int = 21,
          history: int = 252) -> Optional[float]:
    """Latest de-biased flow pressure, in standard deviations."""
    s = cmf_z_series(flow, dvol, window, history).dropna()
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return None if not np.isfinite(v) else v


def cmf_z_delta(flow: pd.Series, dvol: pd.Series, window: int = 21,
                back: int = 21, history: int = 252) -> Optional[float]:
    """Change in de-biased flow pressure over the last `back` sessions."""
    s = cmf_z_series(flow, dvol, window, history).dropna()
    if len(s) < back + 1:
        return None
    v = float(s.iloc[-1] - s.iloc[-1 - back])
    return None if not np.isfinite(v) else v


def cmf_baseline_cross(flow: pd.Series, dvol: pd.Series, window: int = 21,
                       lookback: int = 10, history: int = 252) -> dict:
    """
    The de-biased CMF crossing zero — i.e. the region's flow crossing its own
    normal. This is the primary inflection: the point where money starts
    behaving differently than it has been, rather than the point where a
    permanently-positive raw CMF happens to dip.
    """
    s = cmf_z_series(flow, dvol, window, history)
    direction, days = _days_since_sign_cross(s, lookback)
    last = s.dropna()
    return {
        "direction": direction,
        "days_since_cross": days,
        "cmf_z": float(last.iloc[-1]) if len(last) else None,
    }


def cumulative_flow_line(flow: pd.Series, demean: bool = False,
                         history: int = 252) -> pd.Series:
    """
    Running total of signed dollar flow — the region's accumulation line.

    `demean=True` subtracts each session's trailing-year average flow first,
    for the same reason cmf_z exists: an undrifted line crossing its own
    moving average is a signal, whereas a permanently-rising one crosses in
    only one direction and is therefore no signal at all.
    """
    f = flow
    if demean:
        f = flow - flow.rolling(history, min_periods=60).mean()
    return f.fillna(0.0).cumsum()


def line_vs_ma(line: pd.Series, window: int = 21, lookback: int = 10) -> dict:
    """
    Where the accumulation line sits versus its own moving average, and how
    recently it crossed. A cross of the accumulation line is the flow analogue
    of a price breaking its 50DMA: the trend of money, not of price.
    """
    if line is None or len(line) < window + 2:
        return {"direction": 0, "days_since_cross": None, "above": None}
    ma = line.rolling(window).mean()
    diff = (line - ma).dropna()
    if len(diff) < 3:
        return {"direction": 0, "days_since_cross": None, "above": None}
    direction, days = _days_since_sign_cross(diff, lookback)
    return {
        "direction": direction,
        "days_since_cross": days,
        "above": bool(diff.iloc[-1] > 0),
    }


def _days_since_sign_cross(series: pd.Series, lookback: int) -> tuple[int, Optional[int]]:
    """
    Most recent zero-cross within `lookback` bars.
      +1 = crossed up (neg->pos), -1 = crossed down, 0 = none.
      days_since: 0 means it crossed on the latest bar.
    """
    if series is None or len(series) < 3:
        return 0, None
    vals = series.dropna().to_numpy()
    if len(vals) < 3:
        return 0, None
    n = len(vals)
    horizon = min(lookback, n - 1)
    for i in range(1, horizon + 1):
        cur, prev = vals[n - i], vals[n - i - 1]
        if prev <= 0 < cur:
            return +1, i - 1
        if prev >= 0 > cur:
            return -1, i - 1
    return 0, None


def cmf_cross(flow: pd.Series, dvol: pd.Series, window: int = 21,
              lookback: int = 10) -> dict:
    """Zero-cross of rolling CMF — the primary flow inflection."""
    series = cmf_series(flow, dvol, window)
    direction, days = _days_since_sign_cross(series, lookback)
    last = series.dropna()
    return {
        "direction": direction,
        "days_since_cross": days,
        "cmf": float(last.iloc[-1]) if len(last) else None,
    }


def short_vs_long_cross(flow: pd.Series, dvol: pd.Series, fast: int = 5,
                        slow: int = 21, lookback: int = 10) -> dict:
    """
    Fast CMF crossing slow CMF — the near-term tape turning ahead of the
    month-long trend. Earlier than the zero-cross, and noisier, so the scorer
    weights it at half.
    """
    f = cmf_series(flow, dvol, fast)
    s = cmf_series(flow, dvol, slow)
    direction, days = _days_since_sign_cross((f - s), lookback)
    return {"direction": direction, "days_since_cross": days}


def cmf_delta(flow: pd.Series, dvol: pd.Series, window: int = 21,
              back: int = 21) -> Optional[float]:
    """Change in CMF over the last `back` sessions — is conviction building."""
    series = cmf_series(flow, dvol, window).dropna()
    if len(series) < back + 1:
        return None
    return float(series.iloc[-1] - series.iloc[-1 - back])


def flow_slope(flow: pd.Series, window: int = 21) -> Optional[float]:
    """
    OLS slope of the cumulative accumulation line over the window, normalised
    by average daily turnover so it is unitless and cross-region comparable.
    """
    if flow is None or len(flow) < window + 1:
        return None
    line = flow.iloc[-window:].cumsum().to_numpy(dtype=float)
    if len(line) < 3:
        return None
    x = np.arange(len(line), dtype=float)
    denom = float(np.abs(flow.iloc[-window:]).mean())
    if denom <= 0:
        return None
    slope = float(np.polyfit(x, line, 1)[0])
    return slope / denom
