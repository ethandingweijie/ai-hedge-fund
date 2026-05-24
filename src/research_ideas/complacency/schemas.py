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


QualConvictionLabel = Literal[
    "EXCEPTIONAL",   # both quant gate passed AND qual ≥ 35/50
    "BOTH",          # quant gate passed AND qual 25-34
    "QUANT-ONLY",    # quant gate passed but qual < 25 (could be value trap)
    "QUAL-ONLY",     # qual ≥ 35 but quant < gate (early — narrative bearish, numbers haven't caught up)
    "PASS",          # both low
]


class QualEvidence(BaseModel):
    """One piece of evidence backing a qualitative score."""
    source: str                  # e.g. "10-K 2024 risk factors" | "Q3 2024 earnings transcript"
    quote: str                   # verbatim snippet (≤ 300 chars after trim)
    date: Optional[str] = None   # ISO yyyy-mm-dd
    url: Optional[str] = None


class QualIndicatorScore(BaseModel):
    """One indicator's score with evidence + confidence."""
    indicator: str               # e.g. "A2_catalyst_proximity"
    score: int                   # 0..5 per rubric
    confidence: float            # 0..1 — agent's self-rated confidence
    summary: str                 # 1-line takeaway
    evidence: list[QualEvidence] = Field(default_factory=list)
    scored_at: Optional[str] = None     # ISO ts
    model_used: Optional[str] = None    # e.g. "qwen3.6-max"


class QualitativeAssessment(BaseModel):
    """Aggregate of all qualitative indicators for one ticker."""
    indicators: dict[str, QualIndicatorScore] = Field(default_factory=dict)
    composite: int = 0                       # sum of scores (max = 5 × N indicators)
    max_possible: int = 0                    # 5 × number of indicators scored
    composite_normalized: float = 0.0        # composite / max_possible
    conviction_label: QualConvictionLabel = "PASS"
    assessed_at: Optional[str] = None
    cost_usd: float = 0.0                    # tracked for budget oversight
    incomplete: bool = False                 # true if some indicators failed to score


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
    qualitative: Optional[QualitativeAssessment] = None  # LLM-scored qualitative thesis (Strong-Short / Watch only)
    # ── Aggregate (quant + qual) — 0-100 scale ───────────────────────────
    aggregate_score: Optional[float] = None          # quant 0-50 + qual 0-50 = 0-100
    aggregate_quant_pts: Optional[float] = None      # (quant_composite / 8) * 50
    aggregate_qual_pts: Optional[float] = None       # (qual_composite / qual_max) * 50
    error: Optional[str] = None                       # set if calc failed


class ComplacencyCohortResult(BaseModel):
    run_id: str
    created_at: str
    universe: str = "complacency-default"             # named ticker set
    ticker_count: int = 0
    gate_passers: int = 0                             # how many cleared the gate
    failed_tickers: list[dict] = Field(default_factory=list)
    results: list[ComplacencyTickerResult] = Field(default_factory=list)
