"""
src/research_ideas/momentum/indicators.py
==========================================
Turn + acceleration helpers for the momentum research idea.

These are the computations NOT already in src/agents/technicals.py. The
moving-average / RSI / ADX / ATR primitives are imported from there and
reused — do not re-implement them here.

Every function takes a chronologically-sorted OHLCV DataFrame (index =
DatetimeIndex, columns: open/high/low/close/volume) and returns plain
floats / ints / small dicts. All return None (or a neutral value) when the
series is too short, so the scorer can degrade gracefully on thin history.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from src.agents.technicals import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_rsi,
)


# ─── Trailing returns ───────────────────────────────────────────────────────


def trailing_return(close: pd.Series, periods: int) -> Optional[float]:
    """Simple total return over the last `periods` sessions (e.g. 21 ≈ 1m)."""
    if close is None or len(close) <= periods:
        return None
    past = close.iloc[-periods - 1]
    last = close.iloc[-1]
    if past is None or past == 0 or pd.isna(past) or pd.isna(last):
        return None
    return float(last / past - 1.0)


def multi_horizon_returns(close: pd.Series) -> dict:
    """
    Short → long trailing returns plus the canonical 12-1 momentum
    (252-day return excluding the most recent 21 sessions).
    """
    r_5d = trailing_return(close, 5)
    r_21d = trailing_return(close, 21)
    r_63d = trailing_return(close, 63)
    r_126d = trailing_return(close, 126)
    r_252d = trailing_return(close, 252)

    # 12-1: return from t-252 to t-21 (skip the last month).
    r_12_1 = None
    if close is not None and len(close) > 252:
        start = close.iloc[-252]
        end = close.iloc[-21]
        if start and not pd.isna(start) and not pd.isna(end) and start != 0:
            r_12_1 = float(end / start - 1.0)

    return {
        "r_5d": r_5d,
        "r_21d": r_21d,
        "r_63d": r_63d,
        "r_126d": r_126d,
        "r_252d": r_252d,
        "r_12_1": r_12_1,
    }


# ─── MACD (12,26,9) + histogram zero-cross ──────────────────────────────────


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram) using technicals.calculate_ema."""
    ema_fast = calculate_ema(df, fast)
    ema_slow = calculate_ema(df, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _days_since_sign_cross(series: pd.Series, lookback: int) -> tuple[int, Optional[int]]:
    """
    Find the most recent zero-cross of `series` within the last `lookback`
    bars. Returns (direction, days_since):
      direction = +1 (crossed up: neg→pos), -1 (crossed down: pos→neg), 0 (none)
      days_since = bars since the cross (0 = crossed on the last bar), or None.
    """
    if series is None or len(series) < 3:
        return 0, None
    vals = series.dropna().to_numpy()
    if len(vals) < 3:
        return 0, None
    n = len(vals)
    horizon = min(lookback, n - 1)
    # Walk backwards from the last bar looking for a sign change.
    for i in range(1, horizon + 1):
        cur = vals[n - i]
        prev = vals[n - i - 1]
        if prev <= 0 < cur:
            return +1, i - 1
        if prev >= 0 > cur:
            return -1, i - 1
    return 0, None


def macd_hist_cross(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    Detect the most recent MACD-histogram zero-cross within `lookback` bars.
    neg→pos = bullish turn (+1); pos→neg = bearish turn (-1).
    """
    _, _, hist = macd(df)
    direction, days_since = _days_since_sign_cross(hist, lookback)
    return {
        "macd_hist": float(hist.iloc[-1]) if len(hist) else None,
        "turn_direction": direction,
        "days_since_cross": days_since,
    }


# ─── Moving-average crosses ─────────────────────────────────────────────────


def sma(close: pd.Series, window: int) -> Optional[float]:
    if close is None or len(close) < window:
        return None
    return float(close.iloc[-window:].mean())


def price_ma_cross(close: pd.Series, window: int = 50, lookback: int = 10) -> dict:
    """
    Detect price reclaiming (+1) or losing (-1) its SMA(`window`) within
    `lookback` bars. spread = (price - sma) / sma at the last bar.
    """
    if close is None or len(close) < window + 2:
        return {"direction": 0, "days_since_cross": None, "spread": None}
    sma_series = close.rolling(window).mean()
    diff = (close - sma_series)
    direction, days_since = _days_since_sign_cross(diff, lookback)
    last_sma = sma_series.iloc[-1]
    spread = None
    if last_sma and not pd.isna(last_sma) and last_sma != 0:
        spread = float(close.iloc[-1] / last_sma - 1.0)
    return {"direction": direction, "days_since_cross": days_since, "spread": spread}


def ma_regime(close: pd.Series) -> dict:
    """
    Moving-average alignment: price vs SMA50 vs SMA200.
      +1 bullish stack (price > 50 > 200), -1 bearish stack, 0 mixed.
    """
    price = float(close.iloc[-1]) if close is not None and len(close) else None
    s50 = sma(close, 50)
    s200 = sma(close, 200)
    stack = 0
    if price is not None and s50 is not None and s200 is not None:
        if price > s50 > s200:
            stack = 1
        elif price < s50 < s200:
            stack = -1
    return {"price": price, "sma50": s50, "sma200": s200, "stack": stack}


# ─── RSI turn ───────────────────────────────────────────────────────────────


def rsi_cross(df: pd.DataFrame, period: int = 14, low: float = 30.0,
              high: float = 70.0, lookback: int = 10) -> dict:
    """
    Bullish turn (+1) when RSI crosses back above `low` (exiting oversold);
    bearish turn (-1) when it crosses below `high` (exiting overbought).
    """
    rsi = calculate_rsi(df, period=period)
    rsi_last = float(rsi.iloc[-1]) if len(rsi) and not pd.isna(rsi.iloc[-1]) else None
    vals = rsi.dropna().to_numpy()
    direction, days_since = 0, None
    n = len(vals)
    if n >= 2:
        horizon = min(lookback, n - 1)
        for i in range(1, horizon + 1):
            cur, prev = vals[n - i], vals[n - i - 1]
            if prev <= low < cur:
                direction, days_since = +1, i - 1
                break
            if prev >= high > cur:
                direction, days_since = -1, i - 1
                break
    return {"rsi": rsi_last, "direction": direction, "days_since_cross": days_since}


# ─── Acceleration (2nd derivative) ──────────────────────────────────────────


def roc_of_roc(close: pd.Series, window: int = 21) -> dict:
    """
    Compare the most recent `window`-bar return to the prior `window`-bar
    return. accel = recent - prior (signed). A larger-magnitude move in the
    same direction → |recent| > |prior| and accel shares the trend's sign.
    """
    if close is None or len(close) < 2 * window + 1:
        return {"recent": None, "prior": None, "accel": None}
    last = close.iloc[-1]
    mid = close.iloc[-window - 1]
    start = close.iloc[-2 * window - 1]
    if any(x is None or pd.isna(x) or x == 0 for x in (mid, start)):
        return {"recent": None, "prior": None, "accel": None}
    recent = float(last / mid - 1.0)
    prior = float(mid / start - 1.0)
    return {"recent": recent, "prior": prior, "accel": recent - prior}


def return_slope(close: pd.Series, window: int = 21, slope_lookback: int = 10) -> Optional[float]:
    """
    Slope of the rolling `window`-bar return series over the last
    `slope_lookback` points (OLS). Positive = momentum building up.
    Normalised by `slope_lookback` so it reads as 'return-change per bar'.
    """
    if close is None or len(close) < window + slope_lookback + 1:
        return None
    roll = close.pct_change(periods=window).dropna()
    if len(roll) < slope_lookback:
        return None
    y = roll.iloc[-slope_lookback:].to_numpy()
    x = np.arange(len(y), dtype=float)
    try:
        slope = float(np.polyfit(x, y, 1)[0])
    except (ValueError, np.linalg.LinAlgError):
        return None
    return slope


def adx_state(df: pd.DataFrame, period: int = 14, rising_lookback: int = 10) -> Optional[dict]:
    """
    ADX trend-strength + direction, computed via technicals.calculate_adx.
    Returns None when OHLC is degenerate (high==low everywhere — e.g. the
    FMP 'light' close-only feed), since ADX would be meaningless there.
      di_dir = +1 when +DI > -DI, else -1
      rising = ADX now > ADX `rising_lookback` bars ago
    """
    if df is None or len(df) < period + rising_lookback + 1:
        return None
    if "high" not in df or "low" not in df:
        return None
    # Degenerate OHLC guard (close-only feed).
    if bool((df["high"] == df["low"]).all()):
        return None
    try:
        adx_df = calculate_adx(df.copy(), period=period)
    except Exception:
        return None
    adx = adx_df["adx"].dropna()
    if len(adx) < rising_lookback + 1:
        return None
    adx_now = float(adx.iloc[-1])
    adx_prev = float(adx.iloc[-rising_lookback - 1])
    plus_di = float(adx_df["+di"].iloc[-1])
    minus_di = float(adx_df["-di"].iloc[-1])
    return {
        "adx": adx_now,
        "rising": adx_now > adx_prev,
        "di_dir": 1 if plus_di >= minus_di else -1,
    }


def volume_confirmation(df: pd.DataFrame, window: int = 21) -> Optional[float]:
    """Last session volume / trailing `window`-bar average volume."""
    if df is None or "volume" not in df or len(df) < window + 1:
        return None
    vol = df["volume"].astype(float)
    avg = vol.iloc[-window:].mean()
    if not avg or pd.isna(avg) or avg == 0:
        return None
    return float(vol.iloc[-1] / avg)


def atr_pct(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """ATR as a % of last close (volatility context). None if degenerate OHLC."""
    if df is None or len(df) < period + 1 or "high" not in df:
        return None
    if bool((df["high"] == df["low"]).all()):
        return None
    try:
        atr = calculate_atr(df, period=period)
    except Exception:
        return None
    last_atr = atr.iloc[-1]
    last_close = df["close"].iloc[-1]
    if pd.isna(last_atr) or not last_close:
        return None
    return float(last_atr / last_close)
