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

# Reuse the Complacency qualitative evidence/score shapes verbatim — one
# per-(ticker, indicator) score with confidence + verbatim evidence. The
# HK50 overlay scores its sub-metrics on the SAME 0-5 rubric scaffold, so
# there is no reason to re-type these.
from src.research_ideas.complacency.schemas import (  # noqa: F401  (re-exported)
    QualEvidence,
    QualIndicatorScore,
)


# AICT tier is "—" for every non-software/internet name (AICT not applicable).
AICTLabel = str
LeadScreen = Literal["Growth", "Dividend"]
# Dynamic-universe membership status (decided by lead_score threshold + hysteresis
# in src/research_ideas/hk50/selection.py — see HK50TickerResult.in_cohort):
#   member    — in the displayed cohort and was in the prior run
#   promoted  — in the displayed cohort, newly added this run (cleared ENTER)
#   relegated — NOT in the cohort now, but was in the prior run
#   bench     — scored in the eligible pool but below the displayed line
MembershipStatus = Literal["member", "promoted", "relegated", "bench"]

# ─── Qualitative-overlay tier ladders ─────────────────────────────────────
# Policy posture (dimension 1): higher = stronger state tailwind for a LONG.
PolicyTier = Literal["Tailwind", "Favorable", "Neutral", "Headwind", "Crackdown", "—"]
# Moat / competitive position (dimension 5): AICT names generalised to every
# sector (banks / energy / consumer / pharma / insurers), higher = wider moat.
MoatTier = Literal["Fortress", "Castle", "Chapel", "Stone", "Wood", "—"]
QualDimensionName = Literal["policy", "moat"]
QualSource = Literal["unscored", "curated", "llm", "hybrid"]
# Combined quant×qualitative conviction overlay — NEVER blended into the two
# pure 0-100 quant screens; surfaced as a parallel column + optional gate.
HK50Conviction = Literal[
    "HIGH-CONVICTION",   # strong screen score AND favorable policy AND wide moat
    "SOLID",             # screen + qual both supportive, not top-decile
    "QUANT-RICH",        # screen looks great but policy/moat is a red flag (trap)
    "QUAL-SUPPORT",      # policy/moat strong but screen is middling
    "WATCH",             # mixed / nothing decisive
    "POLICY-RISK",       # active crackdown / Wood moat overrides a good screen
    "UNSCORED",          # qualitative not yet run
]


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
    base_eps: Optional[float] = None         # E0 fed to IV15 (Method-E adjusted for ADR/FMP names)
    # ── Method-E owner-earnings adjustment (ADR/FMP names with material SBC) ──
    e0_raw: Optional[float] = None           # E0 before the SBC retention haircut
    oe_retention: Optional[float] = None     # (N - C - max(0, SBC - B)) / N, clamped [0.30, 1.00]
    unfunded_comp: Optional[float] = None     # max(0, SBC - B), reporting currency ($)
    sbc_pct_ni: Optional[float] = None       # SBC / NI (materiality)
    is_net_diluter: Optional[bool] = None    # buybacks < SBC
    oe_cash_tax_estimated: Optional[bool] = None  # True = C used SBC*0.37 estimate
    stage1_growth: Optional[float] = None    # g1
    iv_gordon: Optional[float] = None        # IV_A
    iv_buffett: Optional[float] = None       # IV_B
    iv15: Optional[float] = None             # blended IV15 (per share, quote ccy)
    price: Optional[float] = None
    p_iv15: Optional[float] = None
    valuation_points: Optional[int] = None   # Vpts on the P/IV15 ladder (max 35)


class HK50QualDimension(BaseModel):
    """One rolled-up qualitative dimension (policy OR moat) for a ticker.

    score_0_100 is the confidence-aware weighted average of the dimension's
    sub-metrics (each 0-5), renormalised over whichever sub-metrics were
    actually scored, then scaled to 0-100. tier is the named ladder bucket.
    """
    dimension: QualDimensionName
    score_0_100: Optional[float] = None
    tier: str = "—"                          # PolicyTier or MoatTier label
    n_scored: int = 0                        # how many sub-metrics resolved
    mean_confidence: float = 0.0             # avg LLM self-confidence across sub-metrics
    summary: str = ""                        # one-line dimension takeaway
    indicators: dict[str, QualIndicatorScore] = Field(default_factory=dict)


class HK50Qualitative(BaseModel):
    """The two-dimension qualitative overlay for one HK50 name.

    Deliberately PARALLEL to the two quant screens — it is surfaced as its own
    column + an optional conviction gate, and is NEVER summed into growth_score
    / dividend_score (mirrors how AICT modulates IV15 transparently rather than
    silently moving the composite).
    """
    hk_ticker: str
    sector: str = "?"
    policy: Optional[HK50QualDimension] = None
    moat: Optional[HK50QualDimension] = None
    conviction: HK50Conviction = "UNSCORED"
    source: QualSource = "unscored"          # curated seed | LLM deep-research | hybrid
    flags: list[str] = Field(default_factory=list)  # human-readable risk/edge notes
    assessed_at: Optional[str] = None
    cost_usd: float = 0.0
    incomplete: bool = False


class HK50TickerResult(BaseModel):
    ticker: str                              # the REPORTED ticker (ADR or HK)
    hk_ticker: str                           # canonical HK ticker (or native ADR)
    name: str
    route_label: str = ""                    # "ADR/FMP" | "HK/AKShare+yf"
    currency: str = "?"

    growth_score: float = 0.0                # High-Growth screen (0-100)
    dividend_score: float = 0.0              # High-Dividend screen (0-100)
    lead: LeadScreen = "Growth"              # which screen leads (max of the two)
    lead_score: float = 0.0                  # max(growth_score, dividend_score) — ranks the pool
    aict_tier: AICTLabel = "—"

    # ── Dynamic-universe membership (output of the screen, NOT hand-curated) ──
    in_cohort: bool = False                  # True = inside the displayed top-50
    cohort_rank: Optional[int] = None        # 1..N within the displayed cohort (None on bench)
    membership: MembershipStatus = "bench"   # member | promoted | relegated | bench

    price: Optional[float] = None
    iv15: Optional[float] = None             # per-share IV15 (quote ccy)
    p_iv15: Optional[float] = None

    metrics: dict[str, MetricResult] = Field(default_factory=dict)  # all 10 screener metrics
    iv15_detail: IV15Detail = Field(default_factory=IV15Detail)

    growth_rank: Optional[int] = None        # 1..N within the Growth screen
    dividend_rank: Optional[int] = None      # 1..N within the Dividend screen

    # Qualitative overlay (dimensions 1 Policy + 5 Moat) — parallel to the two
    # pure quant screens, populated by curated seed and/or LLM deep-research.
    qualitative: Optional[HK50Qualitative] = None
    error: Optional[str] = None


class HK50CohortResult(BaseModel):
    run_id: str
    created_at: str
    ticker_count: int = 0                    # = displayed_count (back-compat for list views)
    avg_growth: Optional[float] = None       # averaged over the DISPLAYED cohort only
    avg_dividend: Optional[float] = None      # averaged over the DISPLAYED cohort only
    median_p_iv15: Optional[float] = None     # median over the DISPLAYED cohort only
    lead_growth_count: int = 0               # how many DISPLAYED names lead on Growth

    # ── Dynamic-universe membership summary ──────────────────────────────────
    eligible_count: int = 0                  # names scored this run (the full pool, ~100)
    displayed_count: int = 0                 # names in the cohort (<= 50)
    enter_threshold: float = 0.0             # lead_score a NON-member must clear to join
    stay_threshold: float = 0.0             # lead_score a member must hold to stay
    promoted: list[dict] = Field(default_factory=list)   # [{ticker, name, lead_score}]
    relegated: list[dict] = Field(default_factory=list)  # [{ticker, name, lead_score, reason}]

    failed_tickers: list[dict] = Field(default_factory=list)  # [{ticker, reason}]
    results: list[HK50TickerResult] = Field(default_factory=list)  # ALL scored names, ranked
