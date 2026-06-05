"""
src/research_ideas/sw46/schemas.py
===================================
Pydantic models for SW46 outputs. Everything stored / served downstream
to the frontend goes through one of these shapes.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


AICTTier = Literal["Fortress", "Castle", "Chapel", "Stone", "Wood"]
TATier = Literal["Not-TT", "Near-TT", "TT*", "N/A"]


class TragicAlgebraYear(BaseModel):
    """One-year row in the Tragic Algebra buildup."""
    fiscal_year: int
    net_income: Optional[float] = None       # N
    sbc_expense: Optional[float] = None      # G
    cash_tax_withholding: Optional[float] = None  # C
    cash_tax_withholding_estimated: bool = False
    buybacks: Optional[float] = None         # B
    share_change: Optional[float] = None     # dS (end - start) — informational only
    avg_share_price: Optional[float] = None  # P — informational only
    unfunded_comp: Optional[float] = None    # max(0, SBC - B): comp NOT funded by buybacks
    genuine_buyback_return: Optional[float] = None  # max(0, B - SBC): real capital return
    is_net_diluter: Optional[bool] = None    # True if buybacks < SBC (dilution charged)
    omega: Optional[float] = None            # Omega = SBC + C + max(0, SBC - B)  (Method E)
    owner_earnings: Optional[float] = None   # OE = N + SBC - Omega = N - C - max(0, SBC - B)
    delta_e: Optional[float] = None          # dE = OE / N


class TragicAlgebraResult(BaseModel):
    years: list[TragicAlgebraYear] = Field(default_factory=list)
    pooled_delta_e: Optional[float] = None   # sum(OE) / sum(N)
    avg_owner_earnings: Optional[float] = None
    latest_owner_earnings: Optional[float] = None
    median_owner_earnings: Optional[float] = None    # median of POSITIVE-OE years
    positive_oe_years: int = 0
    sum_net_income: Optional[float] = None           # ΣN — used to guard ΔE math when negative
    sbc_trend: Optional[float] = None        # SBC growth slope vs revenue (signed)
    ta_tier: TATier = "N/A"
    estimated_c_years: int = 0


class ROICResult(BaseModel):
    owner_earnings: Optional[float] = None
    interest_income: Optional[float] = None
    capital_lease_payments: Optional[float] = None
    other_expense_adjustments: float = 0.0
    numerator: Optional[float] = None
    total_capital: Optional[float] = None
    lt_operating_leases: Optional[float] = None
    net_cash: Optional[float] = None
    purchase_obligations: float = 0.0
    other_capital: float = 0.0
    denominator: Optional[float] = None
    roic: Optional[float] = None             # numerator / denominator


class AICTResult(BaseModel):
    tier: AICTTier = "Stone"
    growth_haircut: float = 0.0
    terminal_multiple: float = 10.0
    blend_buffett_weight: float = 0.5        # weight on terminal-multiple valuation in IV15 blend


class IV15Result(BaseModel):
    iv15_total: Optional[float] = None       # blended IV
    iv15_per_share: Optional[float] = None
    iv15_ddm_total: Optional[float] = None
    iv15_buffett_total: Optional[float] = None
    base_oe: Optional[float] = None
    base_oe_source: Literal["latest", "median", "forward_ni", "forward_margin", "none"] = "none"
    required_return_used: float = 0.15
    growth_year1_5: Optional[float] = None
    growth_year6_10: Optional[float] = None
    growth_year11_15: Optional[float] = None
    terminal_multiple_used: Optional[float] = None
    shares_outstanding: Optional[float] = None


class CompositeScore(BaseModel):
    shareholder_bucket: float = 0.0          # cap 30
    quality_bucket: float = 0.0              # cap 35
    valuation_bucket: float = 0.0            # cap 35  (can be -2)
    total: float = 0.0                       # sum, no further cap
    # sub-component visibility
    pts_ta_tier: float = 0.0
    pts_delta_e: float = 0.0
    pts_sbc_trend: float = 0.0
    pts_aict_tier: float = 0.0
    pts_roic: float = 0.0
    pts_growth: float = 0.0
    pts_p_iv15: float = 0.0
    p_iv15: Optional[float] = None


class SW46TickerResult(BaseModel):
    ticker: str
    name: str
    aict: AICTResult
    tragic_algebra: TragicAlgebraResult
    roic: ROICResult
    iv15: IV15Result
    iv12: Optional[IV15Result] = None    # 12% required-return sensitivity
    iv18: Optional[IV15Result] = None    # 18% required-return sensitivity
    ivb_pct: Optional[float] = None      # implied LT return at current price (solved)
    p_iv12: Optional[float] = None
    p_iv18: Optional[float] = None
    composite: CompositeScore
    price: Optional[float] = None
    market_cap: Optional[float] = None
    fwd_revenue_growth: Optional[float] = None
    rank: Optional[int] = None           # 1..N rank within the cohort run, by composite total
    justification: Optional[str] = None  # one-line narrative per Cassandra-style framework
    error: Optional[str] = None              # set if calc failed


class SW46CohortResult(BaseModel):
    run_id: str
    created_at: str
    pooled_delta_e: Optional[float] = None
    ticker_count: int = 0
    failed_tickers: list[dict] = Field(default_factory=list)  # [{ticker, reason}]
    results: list[SW46TickerResult] = Field(default_factory=list)
