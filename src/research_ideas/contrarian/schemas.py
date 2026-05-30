"""Pydantic schemas for Contrarian "Research Idea of the Day"."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class ContrarianSource(BaseModel):
    """A source citation backing a claim in the idea card."""
    title: str = Field(description="Source title (publisher / article headline)")
    url: Optional[str] = Field(default=None, description="URL if known")
    date: Optional[str] = Field(default=None, description="yyyy-mm-dd if known")


class ContrarianIdea(BaseModel):
    """
    A single "Research Idea of the Day" hypothesis card.

    Fields are tuned for the three pillars: deep value, asymmetric return,
    contrarian. The LLM must populate all three with concrete numbers and
    citations — generic prose gets flagged.
    """
    idea_id: str = Field(description="UUID for this idea")
    ticker: str = Field(description="Primary ticker. US-listed preferred (incl. ADRs).")
    company_name: str = Field(description="Full company name")
    sector: Optional[str] = Field(default=None)
    industry: Optional[str] = Field(default=None)
    market_cap_usd: Optional[float] = Field(default=None, description="Current market cap in USD")

    # ── Mode + thematic context (new) ─────────────────────────────────
    # Lets the agent generate top-down ideas (geographic / sectoral)
    # alongside the current bottom-up contrarian picks. All optional so
    # ideas generated before this addition still validate.
    idea_mode: Optional[str] = Field(
        default="deep_value",
        description=(
            "Generation methodology. One of: "
            "'deep_value' (current bottom-up contrarian US pick), "
            "'thematic_geographic' (country/region rotation → stock pick), "
            "'thematic_sector' (industry trend → stock pick), "
            "'special_situation' (spin-off / M&A arb / restructuring)."
        ),
    )
    theme: Optional[str] = Field(
        default=None,
        description=(
            "Top-down thesis for thematic ideas (≤300 chars). E.g. "
            "'China consumption recovery as PBOC pivots dovish + property "
            "stabilization' or 'US small-cap biotech FDA approval cycle'."
        ),
    )
    region: Optional[str] = Field(
        default=None,
        description="Geographic region: 'US', 'China', 'Japan', 'Europe', 'EM', 'Korea', etc.",
    )
    industry_theme: Optional[str] = Field(
        default=None,
        description="Industry-level theme description for thematic_sector mode.",
    )
    expression_vehicle: Optional[str] = Field(
        default="stock",
        description="How the user expresses the thesis: 'stock' (default), 'adr', 'etf'.",
    )

    # ── Headline / hypothesis ─────────────────────────────────────────
    hypothesis: str = Field(
        description="One-sentence ≤200 char thesis statement. Must mention "
                    "WHY now and the asymmetric setup."
    )

    # ── Three pillars (each ≤ 400 chars with concrete numbers) ────────
    deep_value_angle: str = Field(
        description="Margin of safety / valuation case. Cite specific "
                    "multiples (EV/EBITDA, P/B, P/TBV, FCF yield) vs sector "
                    "and historical."
    )
    asymmetric_angle: str = Field(
        description="Upside / downside breakdown with explicit price targets "
                    "or % moves. Must show downside is bounded (assets, cash, "
                    "tangible book) while upside is multi-bagger."
    )
    contrarian_angle: str = Field(
        description="Why retail / institutions / sell-side avoid this name. "
                    "Cite analyst sentiment (Sell/Hold-heavy), short interest, "
                    "or narrative aversion."
    )

    # ── Catalyst + risks ──────────────────────────────────────────────
    primary_catalyst: str = Field(
        description="The trigger that closes the gap. Concrete and time-bound."
    )
    catalyst_timeline: Optional[str] = Field(
        default=None, description="e.g., 'Q2 2026 earnings' / 'spin-off completes Jul 2026'"
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="3-5 bullet risks that could invalidate the thesis."
    )

    # ── Conviction scoring (LLM self-scored 1-10) ─────────────────────
    conviction_score: int = Field(
        ge=1, le=10,
        description="LLM self-conviction 1-10. Use 8+ only with strong "
                    "Tier-1 evidence."
    )
    deep_value_score: int = Field(
        ge=1, le=10,
        description="How compelling is the margin-of-safety case (1=weak, 10=screaming bargain)"
    )
    asymmetry_score: int = Field(
        ge=1, le=10,
        description="Upside/downside ratio (1=symmetric, 10=multi-bagger with bounded downside)"
    )
    contrarian_score: int = Field(
        ge=1, le=10,
        description="How avoided is this name (1=consensus long, 10=actively hated)"
    )

    sources: list[ContrarianSource] = Field(
        default_factory=list,
        description="At least 2 Tier-1 sources backing the prelim research."
    )

    # ── Meta ──────────────────────────────────────────────────────────
    generated_at: str = Field(description="ISO timestamp")
    model_used: str = Field(default="qwen3.6-plus")
    cost_usd: Optional[float] = Field(default=None)


class ContrarianChatMessage(BaseModel):
    """One turn in the user-agent discussion thread."""
    message_id: str
    idea_id: str
    role: str  # 'user' | 'assistant'
    content: str
    created_at: str
    cost_usd: Optional[float] = None


class ContrarianShortlistEntry(BaseModel):
    """An idea the user has accepted into the shortlist."""
    idea_id: str
    shortlisted_at: str
    user_note: Optional[str] = None
    # Snapshot of the idea at the time of shortlisting (so it stays
    # readable even if the original is deleted).
    idea_snapshot: dict
