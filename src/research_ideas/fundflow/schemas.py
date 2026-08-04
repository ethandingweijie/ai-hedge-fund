"""
src/research_ideas/fundflow/schemas.py
=======================================
Pydantic models for the geographic fund-flow screen.

Scoring is SIGNED, on the same shape as the Sectors (US) momentum engine, so
the two screens read identically side by side:

  three pillars (pressure / turn / acceleration) each in [-2, +2]
  composite = sum -> [-6, +6]   (positive = inflow, negative = outflow)

The vocabulary changes because the underlying quantity does — a sector
composite says "the trend is up", a flow composite says "money is arriving".
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


FlowVerdict = Literal[
    "Accelerating-Inflow",   # established accumulation gaining pace
    "Turning-Inflow",        # fresh switch from distribution to accumulation
    "Neutral",               # no actionable flow signal
    "Turning-Outflow",       # fresh switch from accumulation to distribution
    "Accelerating-Outflow",  # established distribution deepening
]

FlowDirection = Literal["INFLOW", "OUTFLOW", "NEUTRAL"]

# How far the implied creation/redemption feed can be trusted for this row.
ImpliedQuality = Literal["good", "partial", "stale", "none"]


class FlowSparkPoint(BaseModel):
    """
    One point on the region's flow-pressure trace: de-biased CMF in standard
    deviations off that region's own baseline. Unitless on purpose, so every
    region's sparkline shares one vertical scale and can be read comparatively
    at a glance — a cumulative-dollar trace could not, since the US series
    would be three orders of magnitude taller than Indonesia's.
    """
    d: str                                     # ISO date
    v: float                                   # flow pressure, sigma


class FundFlowRegionResult(BaseModel):
    # ── Identity ──────────────────────────────────────────────────────────
    region: str                                # "JP"
    label: str                                 # "Japan"
    emoji: Optional[str] = None
    bloc: Optional[str] = None                 # "Developed" | "Asia EM" | "Benchmark"
    etf: str                                   # primary vehicle
    basket: list[str] = Field(default_factory=list)
    is_benchmark: bool = False
    bars: int = 0

    # ── Money ─────────────────────────────────────────────────────────────
    # tape_flow_* is conviction-weighted turnover in dollars, NOT a
    # creation/redemption ledger. It is deliberately never divided by assets
    # — see scoring.py. Percent-of-assets belongs only to implied_flow_*.
    aum: Optional[float] = None                # total basket AUM, USD
    tape_flow_5d: Optional[float] = None       # signed accumulation dollars
    tape_flow_21d: Optional[float] = None
    tape_flow_63d: Optional[float] = None
    tape_flow_126d: Optional[float] = None
    tape_flow_252d: Optional[float] = None
    avg_daily_turnover: Optional[float] = None # 21d mean gross USD volume

    # ── Flow reads ────────────────────────────────────────────────────────
    # The composite is scored on the 1-month window; 3/6/12-month reads are
    # reported alongside so a month can be judged against the longer trend
    # rather than in isolation.
    cmf_21: Optional[float] = None             # -1..+1 raw conviction
    cmf_5: Optional[float] = None
    cmf_63: Optional[float] = None
    cmf_126: Optional[float] = None
    cmf_252: Optional[float] = None
    cmf_z_21: Optional[float] = None           # CMF in sigma off own baseline
    cmf_z_63: Optional[float] = None
    cmf_z_126: Optional[float] = None
    cmf_z_252: Optional[float] = None
    cmf_z_delta_21: Optional[float] = None     # change in that over ~1m
    mfi_14: Optional[float] = None             # 0..100
    flow_breadth_21: Optional[float] = None    # share of up-flow sessions, 0..1
    turnover_surge: Optional[float] = None     # 21d turnover / 63d turnover
    flow_z_21: Optional[float] = None          # 21d net flow z-score vs own year
    flow_slope_21: Optional[float] = None      # normalised accumulation slope
    days_since_turn: Optional[int] = None      # freshness of the driving inflection

    # ── Signed pillars (-2..+2) ───────────────────────────────────────────
    pressure_score: float = 0.0                # where flow stands now
    turn_score: float = 0.0                    # is it inflecting
    accel_score: float = 0.0                   # is it strengthening
    composite: float = 0.0                     # -6..+6
    signal_strength: float = 0.0               # |composite| / 6 * 100

    # Where the composite stood N sessions ago, recomputed by re-scoring the
    # truncated series. Gives a "change over" read at each horizon without
    # depending on stored earlier runs, so it works on the very first run.
    composite_1m: Optional[float] = None       # composite ~21 sessions ago
    composite_3m: Optional[float] = None       # composite ~63 sessions ago
    composite_6m: Optional[float] = None       # composite ~126 sessions ago
    composite_12m: Optional[float] = None      # composite ~252 sessions ago

    verdict: FlowVerdict = "Neutral"
    direction: FlowDirection = "NEUTRAL"
    passes_gate: bool = False

    # ── Rotation overlay ──────────────────────────────────────────────────
    # Flow pressure measured AGAINST the global benchmark (ACWI). This is the
    # axis that answers "which geography is winning share of the world's bid",
    # which a per-region reading cannot: in a broad risk-on tape every region
    # shows inflow, and only the relative number separates a real allocation
    # preference from the global tide lifting everything.
    rel_flow_z: Optional[float] = None         # own cmf_z - world cmf_z, sigma
    rel_flow_z_1m: Optional[float] = None      # same, as of ~1m ago
    rel_flow_z_delta: Optional[float] = None   # rel_flow_z - rel_flow_z_1m

    # ── Price overlay (confirmation vs divergence) ───────────────────────
    price_composite: Optional[float] = None    # -6..+6 from the momentum engine
    price_verdict: Optional[str] = None
    r_21d: Optional[float] = None              # ETF total return, 1m (USD)
    r_63d: Optional[float] = None
    r_126d: Optional[float] = None
    r_252d: Optional[float] = None
    fx_drag_21d: Optional[float] = None        # unhedged 1m return - hedged 1m return
    divergence: Optional[str] = None           # "confirming" | "flow-leads" | "price-leads"

    # ── Implied creation/redemption (measured, not estimated) ────────────
    # Change in shares outstanding valued at the close — the number an issuer
    # reports. Null wherever the share-count feed is stale, which is why it
    # corroborates the composite instead of feeding it.
    implied_flow_21d: Optional[float] = None   # USD
    implied_flow_63d: Optional[float] = None
    implied_flow_126d: Optional[float] = None
    implied_flow_252d: Optional[float] = None
    implied_flow_21d_pct_aum: Optional[float] = None
    implied_flow_63d_pct_aum: Optional[float] = None
    implied_flow_126d_pct_aum: Optional[float] = None
    implied_flow_252d_pct_aum: Optional[float] = None
    implied_quality: ImpliedQuality = "none"

    # ── Narrative ─────────────────────────────────────────────────────────
    spark: list[FlowSparkPoint] = Field(default_factory=list)
    flag_notes: list[str] = Field(default_factory=list)
    justification: Optional[str] = None
    data_notes: list[str] = Field(default_factory=list)


class FundFlowSummary(BaseModel):
    """The top-of-page brief: what moved, what changed, what it implies."""
    headline: str
    regime: str                                # one-line characterisation of the tape
    net_implied_flow_21d: Optional[float] = None   # summed measured flow, USD
    implied_coverage: int = 0                  # regions with a usable share feed
    inflow_count: int = 0
    outflow_count: int = 0
    key_flows: list[str] = Field(default_factory=list)
    key_changes: list[str] = Field(default_factory=list)
    implications: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    # "deepseek" once the narrator has rewritten the deterministic draft;
    # "deterministic" whenever the model was unavailable, errored, or returned
    # something unusable. Surfaced in the UI so the reader always knows which
    # they are looking at.
    summary_source: Literal["deepseek", "deterministic"] = "deterministic"
    model_used: Optional[str] = None


class FundFlowCohortResult(BaseModel):
    run_id: str
    created_at: str
    as_of: Optional[str] = None                # screen date (None = live/today)
    universe: str = "fundflow-geographic"
    region_count: int = 0
    inflow_count: int = 0
    outflow_count: int = 0
    summary: Optional[FundFlowSummary] = None
    regions: list[FundFlowRegionResult] = Field(default_factory=list)
    benchmarks: list[FundFlowRegionResult] = Field(default_factory=list)
    failed_regions: list[dict] = Field(default_factory=list)
