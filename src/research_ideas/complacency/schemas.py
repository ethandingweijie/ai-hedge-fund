"""
src/research_ideas/complacency/schemas.py
==========================================
Pydantic models for the Complacency Detection equity screener.

Per the spec §3.1, each ticker produces 4 pillar scores (0-2 each) summing
to a composite (0-8). The "gate" is composite ≥ 6 AND every pillar ≥ 1.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ComplacencyVerdict = Literal[
    "Strong-Short",   # gate passed; multiple pillars at strong (2pt)
    "Watch",          # gate passed but weaker conviction
    "Borderline",     # composite 4-5, partial signal
    "Pass",           # composite ≤ 3, no signal
    "N/A",            # insufficient data
]


class PutRecommendation(BaseModel):
    """Suggested put-option contract for tickers flagged Strong-Short or Watch.
    Sourced from yfinance option chain (v1; spec §10 calls for IV-percentile
    filter in v2 via Polygon)."""
    strike: float
    strike_pct_otm: float          # negative; -0.12 == 12% OTM
    expiry: str                    # ISO yyyy-mm-dd
    days_to_expiry: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    implied_volatility: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    rationale: str = ""
    contract_symbol: Optional[str] = None


class ComplacencyTickerResult(BaseModel):
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None
    rank: Optional[int] = None

    # ─── Pillar inputs (raw observations) ─────────────────────────────────
    ev_sales: Optional[float] = None
    ev_sales_sector_median: Optional[float] = None    # benchmark
    ev_sales_relative: Optional[float] = None         # = ev_sales / median
    fcf_yield_ttm: Optional[float] = None             # signed; <0 is bad
    altman_z: Optional[float] = None
    piotroski: Optional[int] = None
    ad_ratio_4q_avg: Optional[float] = None           # insider acquired/disposed; missing on free FMP tier
    eps_revision_yoy: Optional[float] = None
    sma200_extension: Optional[float] = None          # (price − 200DMA) / 200DMA
    rsi_weekly: Optional[float] = None
    range_position: Optional[float] = None            # (price − 52wlo) / (52whi − 52wlo)

    # ─── Pillar scores (0-2 each) ─────────────────────────────────────────
    val_score: float = 0.0
    beh_score: float = 0.0
    tech_score: float = 0.0
    qual_score: float = 0.0
    composite: float = 0.0                            # sum of 4 pillars (max 8)
    passes_gate: bool = False                         # composite ≥ 6 AND all pillars ≥ 1

    verdict: ComplacencyVerdict = "N/A"
    flag_notes: list[str] = Field(default_factory=list)    # human-readable signals fired
    justification: Optional[str] = None
    put_recommendation: Optional[PutRecommendation] = None  # only when verdict in {Strong-Short, Watch}
    options_data_freshness: Optional[str] = None     # ISO ts of put-rec fetch
    error: Optional[str] = None                       # set if calc failed


class ComplacencyCohortResult(BaseModel):
    run_id: str
    created_at: str
    universe: str = "complacency-default"             # named ticker set
    ticker_count: int = 0
    gate_passers: int = 0                             # how many cleared the gate
    failed_tickers: list[dict] = Field(default_factory=list)
    results: list[ComplacencyTickerResult] = Field(default_factory=list)
