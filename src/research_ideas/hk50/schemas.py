"""
src/research_ideas/hk50/schemas.py
===================================
Pydantic models for the "Long China / HK" two-screener cohort. Everything
persisted / served to the frontend goes through one of these shapes.

A name carries BOTH screen scores (growth_score, dividend_score) in one row;
the UI derives the Growth ranking and the Dividend ranking client-side from the
same payload (and the top-5 of each for the hero-card face).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# AICT tier is "—" for every non-software/internet name (AICT not applicable).
AICTLabel = str
LeadScreen = Literal["Growth", "Dividend"]


class MetricResult(BaseModel):
    """One resolved screener metric: value + provenance tag."""
    value: Optional[float] = None
    source: str = "missing"   # primary | yfinance | fmp_growth | compute | structural | missing


class IV15Detail(BaseModel):
    """Full IV15 build-up for the ticker card (Nones where EPS<=0 / no quote)."""
    aict: AICTLabel = "—"
    currency: str = "?"
    haircut: Optional[float] = None          # growth haircut (AICT names only)
    terminal_multiple: Optional[float] = None  # Buffett terminal multiple (None = Gordon-only)
    blend_weight_a: Optional[float] = None   # wA — weight on Valuation A (Gordon)
    base_eps: Optional[float] = None         # E0 (owner-earnings proxy)
    stage1_growth: Optional[float] = None    # g1
    iv_gordon: Optional[float] = None        # IV_A
    iv_buffett: Optional[float] = None       # IV_B
    iv15: Optional[float] = None             # blended IV15 (per share, quote ccy)
    price: Optional[float] = None
    p_iv15: Optional[float] = None
    valuation_points: Optional[int] = None   # Vpts on the P/IV15 ladder (max 35)


class HK50TickerResult(BaseModel):
    ticker: str                              # the REPORTED ticker (ADR or HK)
    hk_ticker: str                           # canonical HK ticker (or native ADR)
    name: str
    route_label: str = ""                    # "ADR/FMP" | "HK/AKShare+yf"
    currency: str = "?"

    growth_score: float = 0.0                # High-Growth screen (0-100)
    dividend_score: float = 0.0              # High-Dividend screen (0-100)
    lead: LeadScreen = "Growth"              # which screen leads (max of the two)
    aict_tier: AICTLabel = "—"

    price: Optional[float] = None
    iv15: Optional[float] = None             # per-share IV15 (quote ccy)
    p_iv15: Optional[float] = None

    metrics: dict[str, MetricResult] = Field(default_factory=dict)  # all 10 screener metrics
    iv15_detail: IV15Detail = Field(default_factory=IV15Detail)

    growth_rank: Optional[int] = None        # 1..N within the Growth screen
    dividend_rank: Optional[int] = None      # 1..N within the Dividend screen
    error: Optional[str] = None


class HK50CohortResult(BaseModel):
    run_id: str
    created_at: str
    ticker_count: int = 0
    avg_growth: Optional[float] = None
    avg_dividend: Optional[float] = None
    median_p_iv15: Optional[float] = None
    lead_growth_count: int = 0               # how many names lead on Growth
    failed_tickers: list[dict] = Field(default_factory=list)  # [{ticker, reason}]
    results: list[HK50TickerResult] = Field(default_factory=list)
