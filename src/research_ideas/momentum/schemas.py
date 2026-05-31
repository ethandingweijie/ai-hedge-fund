"""
src/research_ideas/momentum/schemas.py
=======================================
Pydantic models for the momentum screen.

Scoring is SIGNED so one scale serves both directions:
  three pillars (state / turn / acceleration) each in [-2, +2]
  composite = sum  -> [-6, +6]   (positive = long, negative = short)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


MomentumVerdict = Literal[
    "Accelerating-Long",   # established uptrend gaining steam
    "Turning-Long",        # fresh bullish inflection from a weak/neutral state
    "Neutral",             # no actionable signal
    "Turning-Short",       # fresh bearish inflection (the sell-down entry)
    "Accelerating-Short",  # established downtrend deepening
]

MomentumDirection = Literal["LONG", "SHORT", "NEUTRAL"]


class MomentumScore(BaseModel):
    """Shared scored fields — used for both tickers and sector ETFs."""
    # ── Multi-horizon trailing returns ───────────────────────────────────
    r_5d: Optional[float] = None
    r_21d: Optional[float] = None
    r_63d: Optional[float] = None
    r_126d: Optional[float] = None
    r_252d: Optional[float] = None
    r_12_1: Optional[float] = None            # 12-1 momentum (skip last month)

    # ── Raw indicator reads ──────────────────────────────────────────────
    ma_stack: int = 0                          # +1 bullish / -1 bearish / 0 mixed
    macd_hist: Optional[float] = None
    rsi: Optional[float] = None
    adx: Optional[float] = None
    accel_roc: Optional[float] = None          # recent 21d ret − prior 21d ret
    return_slope: Optional[float] = None
    volume_ratio: Optional[float] = None       # last vol / 21d avg vol
    days_since_turn: Optional[int] = None      # bars since the driving inflection

    # ── Signed pillar scores (-2 .. +2) ──────────────────────────────────
    state_score: float = 0.0
    turn_score: float = 0.0
    accel_score: float = 0.0
    composite: float = 0.0                     # sum of the three pillars (-6 .. +6)
    signal_strength: float = 0.0               # |composite| / 6 * 100

    verdict: MomentumVerdict = "Neutral"
    direction: MomentumDirection = "NEUTRAL"
    passes_gate: bool = False                  # verdict != Neutral
    flag_notes: list[str] = Field(default_factory=list)
    justification: Optional[str] = None


class MomentumSectorResult(MomentumScore):
    etf: str
    sector: str
    label: Optional[str] = None
    group: Optional[str] = None                # "macro" | "thematic"
    bars: int = 0
    composite_1m: Optional[float] = None       # composite as of ~21 sessions ago (rank a month ago)
    composite_3m: Optional[float] = None       # composite as of ~63 sessions ago (rank a quarter ago)


class MomentumTickerResult(MomentumScore):
    ticker: str
    name: str
    sector: Optional[str] = None
    sector_etf: Optional[str] = None
    price: Optional[float] = None
    rank: Optional[int] = None
    bars: int = 0

    # ── Sector-alignment overlay ─────────────────────────────────────────
    sector_aligned: Optional[bool] = None      # ticker direction agrees with its sector ETF
    sector_direction: Optional[MomentumDirection] = None
    sector_composite: Optional[float] = None
    error: Optional[str] = None


class MomentumCohortResult(BaseModel):
    run_id: str
    created_at: str
    as_of: Optional[str] = None                # screen date (None = live/today)
    universe: str = "momentum-default"
    ticker_count: int = 0
    long_count: int = 0
    short_count: int = 0
    sectors: list[MomentumSectorResult] = Field(default_factory=list)
    failed_tickers: list[dict] = Field(default_factory=list)
    results: list[MomentumTickerResult] = Field(default_factory=list)
