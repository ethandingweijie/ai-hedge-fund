"""
src/research_ideas/momentum/scoring.py
=======================================
Three-dimension SIGNED momentum scorer. The same function scores any price
series, so sector ETFs and tickers share one code path.

  STATE  — where momentum is now (trend regime + medium-term return)
  TURN   — is it changing direction (MACD/SMA50/RSI inflection, fresh only)
  ACCEL  — is it strengthening (ROC-of-ROC, return slope, ADX-rising, volume)

Each pillar scores in [-2, +2]; composite = sum in [-6, +6]. Positive →
long, negative → short.

  score_series(bundle) -> dict
  apply_sector_alignment(ticker_dict, sector_by_etf) -> mutates/returns dict
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from src.research_ideas.momentum.data_fetch import MomentumBundle
from src.research_ideas.momentum import indicators as ind


_TURN_LOOKBACK = 10        # bars: a turn must be this fresh to count
_ACCEL_EPS = 0.01          # 1% swing in 21d return to call it acceleration


def _clamp_net(up: int, down: int) -> int:
    net = up - down
    if net >= 2:
        return 2
    if net == 1:
        return 1
    if net == -1:
        return -1
    if net <= -2:
        return -2
    return 0


def _score_state(rets: dict, regime: dict) -> tuple[float, list[str]]:
    notes: list[str] = []
    stack = regime.get("stack", 0)
    r63 = rets.get("r_63d")
    r121 = rets.get("r_12_1")

    up = 0
    down = 0
    if stack == 1:
        up += 1
    elif stack == -1:
        down += 1
    if r63 is not None:
        if r63 > 0.08:
            up += 1
        elif r63 < -0.08:
            down += 1
    if r121 is not None:
        if r121 > 0.10:
            up += 1
        elif r121 < -0.10:
            down += 1

    score = _clamp_net(up, down)
    if score > 0:
        notes.append(
            f"Uptrend regime (stack={stack:+d}, 3m {(_pct(r63))}, 12-1 {(_pct(r121))})"
        )
    elif score < 0:
        notes.append(
            f"Downtrend regime (stack={stack:+d}, 3m {(_pct(r63))}, 12-1 {(_pct(r121))})"
        )
    return float(score), notes


def _score_turn(macd_x: dict, sma_x: dict, rsi_x: dict) -> tuple[float, Optional[int], list[str]]:
    notes: list[str] = []
    macd_dir = macd_x.get("turn_direction", 0)
    sma_dir = sma_x.get("direction", 0)
    rsi_dir = rsi_x.get("direction", 0)

    # MACD histogram zero-cross is the primary inflection; SMA50 reclaim/loss
    # and RSI band exit are confirmations (half weight).
    raw = macd_dir * 1.0 + sma_dir * 0.5 + rsi_dir * 0.5

    if raw >= 1.0:
        score = 2.0
    elif raw >= 0.5:
        score = 1.0
    elif raw <= -1.0:
        score = -2.0
    elif raw <= -0.5:
        score = -1.0
    else:
        score = 0.0

    # Freshness: prefer the MACD cross age, else whichever source fired.
    days_since = None
    for src in (macd_x.get("days_since_cross"), sma_x.get("days_since_cross"),
                rsi_x.get("days_since_cross")):
        if src is not None:
            days_since = src if days_since is None else min(days_since, src)

    if score > 0:
        bits = []
        if macd_dir > 0:
            bits.append("MACD hist crossed up")
        if sma_dir > 0:
            bits.append("reclaimed 50DMA")
        if rsi_dir > 0:
            bits.append("RSI exited oversold")
        notes.append("Bullish turn: " + ", ".join(bits) + _fresh(days_since))
    elif score < 0:
        bits = []
        if macd_dir < 0:
            bits.append("MACD hist crossed down")
        if sma_dir < 0:
            bits.append("lost 50DMA")
        if rsi_dir < 0:
            bits.append("RSI rolled from overbought")
        notes.append("Bearish turn: " + ", ".join(bits) + _fresh(days_since))
    return score, days_since, notes


def _score_accel(roc: dict, slope: Optional[float], adx: Optional[dict],
                 vol_ratio: Optional[float], r_21d: Optional[float]) -> tuple[float, list[str]]:
    notes: list[str] = []
    accel = roc.get("accel")

    up = 0
    down = 0
    if accel is not None:
        if accel > _ACCEL_EPS:
            up += 1
        elif accel < -_ACCEL_EPS:
            down += 1
    if slope is not None:
        if slope > 0:
            up += 1
        elif slope < 0:
            down += 1
    if adx is not None and adx.get("rising"):
        if adx.get("di_dir") == 1:
            up += 1
        elif adx.get("di_dir") == -1:
            down += 1
    if vol_ratio is not None and vol_ratio > 1.2 and r_21d is not None:
        if r_21d > 0:
            up += 1
        elif r_21d < 0:
            down += 1

    score = _clamp_net(up, down)
    if score > 0:
        notes.append(
            f"Accelerating up (Δ21d {(_pct(accel))}"
            + (f", ADX {adx['adx']:.0f} rising" if adx and adx.get('rising') else "")
            + (f", vol {vol_ratio:.1f}×" if vol_ratio else "") + ")"
        )
    elif score < 0:
        notes.append(
            f"Accelerating down (Δ21d {(_pct(accel))}"
            + (f", ADX {adx['adx']:.0f} rising" if adx and adx.get('rising') else "")
            + (f", vol {vol_ratio:.1f}×" if vol_ratio else "") + ")"
        )
    return float(score), notes


def derive_verdict(state: float, turn: float, accel: float, composite: float) -> str:
    fresh_bull = turn >= 1
    fresh_bear = turn <= -1
    # A fresh inflection that contradicts (or neutralises) the prior state is
    # the headline — but if it's already deep + accelerating, call it
    # "Accelerating" rather than merely "Turning".
    if fresh_bull and accel >= 0 and state <= 1:
        if composite >= 4 and accel >= 1:
            return "Accelerating-Long"
        return "Turning-Long"
    if fresh_bear and accel <= 0 and state >= -1:
        if composite <= -4 and accel <= -1:
            return "Accelerating-Short"
        return "Turning-Short"
    if composite >= 4 and accel >= 1:
        return "Accelerating-Long"
    if composite <= -4 and accel <= -1:
        return "Accelerating-Short"
    return "Neutral"


def _verdict_direction(verdict: str) -> str:
    if verdict.endswith("Long"):
        return "LONG"
    if verdict.endswith("Short"):
        return "SHORT"
    return "NEUTRAL"


def score_series(bundle: MomentumBundle) -> dict:
    """Compute the three signed pillars + composite + verdict for one series."""
    df = bundle.df
    close = df["close"]

    rets = ind.multi_horizon_returns(close)
    regime = ind.ma_regime(close)
    macd_x = ind.macd_hist_cross(df, lookback=_TURN_LOOKBACK)
    sma_x = ind.price_ma_cross(close, window=50, lookback=_TURN_LOOKBACK)
    rsi_x = ind.rsi_cross(df, lookback=_TURN_LOOKBACK)
    roc = ind.roc_of_roc(close, window=21)
    slope = ind.return_slope(close, window=21, slope_lookback=10)
    adx = ind.adx_state(df) if bundle.has_true_ohlc else None
    vol_ratio = ind.volume_confirmation(df, window=21)

    state, state_notes = _score_state(rets, regime)
    turn, days_since, turn_notes = _score_turn(macd_x, sma_x, rsi_x)
    accel, accel_notes = _score_accel(roc, slope, adx, vol_ratio, rets.get("r_21d"))

    composite = state + turn + accel
    verdict = derive_verdict(state, turn, accel, composite)
    direction = _verdict_direction(verdict)
    flag_notes = state_notes + turn_notes + accel_notes

    return {
        **rets,
        "ma_stack": regime.get("stack", 0),
        "macd_hist": macd_x.get("macd_hist"),
        "rsi": rsi_x.get("rsi"),
        "adx": adx.get("adx") if adx else None,
        "accel_roc": roc.get("accel"),
        "return_slope": slope,
        "volume_ratio": vol_ratio,
        "days_since_turn": days_since,
        "state_score": state,
        "turn_score": turn,
        "accel_score": accel,
        "composite": float(composite),
        "signal_strength": round(abs(composite) / 6.0 * 100.0, 1),
        "verdict": verdict,
        "direction": direction,
        "passes_gate": verdict != "Neutral",
        "flag_notes": flag_notes,
        "justification": _compose_justification(bundle.symbol, verdict, composite, flag_notes),
    }


def apply_sector_alignment(ticker_dict: dict, sector_by_etf: dict) -> dict:
    """
    Overlay: flag whether a ticker's direction agrees with its sector ETF's.
    `sector_by_etf` maps ETF symbol -> scored sector dict (composite/direction).
    Mutates and returns `ticker_dict`.
    """
    etf = ticker_dict.get("sector_etf")
    sec = sector_by_etf.get(etf) if etf else None
    if not sec:
        ticker_dict["sector_aligned"] = None
        return ticker_dict

    t_comp = ticker_dict.get("composite") or 0.0
    s_comp = sec.get("composite") or 0.0
    t_sign = (t_comp > 0) - (t_comp < 0)
    s_sign = (s_comp > 0) - (s_comp < 0)
    aligned = bool(t_sign != 0 and t_sign == s_sign)

    ticker_dict["sector_aligned"] = aligned
    ticker_dict["sector_direction"] = sec.get("direction")
    ticker_dict["sector_composite"] = s_comp
    notes = ticker_dict.get("flag_notes") or []
    if aligned:
        notes.append(
            f"Sector tailwind: {etf} {sec.get('verdict')} "
            f"({_signed(s_comp)}) confirms direction"
        )
    elif t_sign != 0 and s_sign != 0:
        notes.append(
            f"Sector divergence: {etf} {sec.get('verdict')} "
            f"({_signed(s_comp)}) — idiosyncratic move"
        )
    ticker_dict["flag_notes"] = notes
    return ticker_dict


# ─── formatting helpers ─────────────────────────────────────────────────────


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _signed(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:+.0f}"


def _fresh(days_since: Optional[int]) -> str:
    if days_since is None:
        return ""
    if days_since == 0:
        return " (today)"
    return f" ({days_since}d ago)"


def _compose_justification(symbol: str, verdict: str, composite: float,
                           notes: list[str]) -> str:
    headline = {
        "Accelerating-Long": "Uptrend accelerating",
        "Turning-Long": "Turning up — bullish inflection",
        "Neutral": "No actionable momentum signal",
        "Turning-Short": "Turning down — bearish inflection",
        "Accelerating-Short": "Downtrend accelerating",
    }[verdict]
    flag_str = "; ".join(notes[:4]) if notes else "no individual flags surfaced"
    return f"{symbol}: {headline} (composite {composite:+.0f}/6). {flag_str}."
