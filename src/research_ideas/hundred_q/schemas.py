"""
src/research_ideas/hundred_q/schemas.py
=========================================
Pydantic models for the 100-Question screener.

Composite is a FLAT aggregation: composite_pct = yes_answers / answered_questions
(no pillar weighting — the source questionnaire only specifies a flat
pass/reject threshold, so this doesn't invent weights that aren't backed
by the reference material). `answered_questions` excludes any question
whose answer is None (data_unavailable).

Tiers: Active Pass >= 0.65, On-Deck 0.55-0.649, Cool-off < 0.55.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Reused verbatim from the sibling screener — identical evidence/confidence
# shape, no reason to duplicate.
from src.research_ideas.complacency.schemas import QualEvidence, QualIndicatorScore

Tier = Literal["active_pass", "on_deck", "cooloff", "not_evaluated"]
QuestionType = Literal["quant", "qual"]

PILLAR_IDS = ("P1", "P2", "P3", "P4", "P5", "P6")
PILLAR_LABELS: dict[str, str] = {
    "P1": "Financial Performance & Quality of Earnings",
    "P2": "Balance Sheet Strength",
    "P3": "Moat & Competitive Position",
    "P4": "Management & Governance",
    "P5": "Valuation & Margin of Safety",
    "P6": "Catalysts, Risks & Market Dynamics",
}


class QuestionAnswer(BaseModel):
    """One question's answer for one ticker, at one point in time."""
    question_id: str                    # e.g. "P1.1"
    pillar: str                         # e.g. "P1"
    label: str                          # human-readable question text
    q_type: QuestionType
    answer: Optional[bool] = None       # None = data_unavailable, excluded from denominator
    raw_value: Optional[str] = None     # JSON-encoded raw number(s)/text backing the check
    threshold_desc: Optional[str] = None
    source: Optional[str] = None        # 'fmp_ttm' | 'fmp_line_items' | 'edgar_form4' | 'llm_qwen' | ...
    evaluated_at: Optional[str] = None
    # Qualitative-only enrichment (unused in Phase 0)
    confidence: Optional[float] = None
    evidence: list[QualEvidence] = Field(default_factory=list)


class PillarScore(BaseModel):
    pillar: str
    label: str
    questions_answered: int = 0
    questions_yes: int = 0
    pillar_pct: Optional[float] = None   # None if nothing answered in this pillar


class HundredQTickerResult(BaseModel):
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None

    question_ledger: list[QuestionAnswer] = Field(default_factory=list)
    pillar_scores: list[PillarScore] = Field(default_factory=list)

    quant_composite_pct: Optional[float] = None
    qual_composite_pct: Optional[float] = None   # None in Phase 0 (no qual layer yet)
    composite_pct: Optional[float] = None         # flat aggregation across all answered questions

    tier: Tier = "not_evaluated"
    rank: Optional[int] = None

    evaluated_at: Optional[str] = None
    error: Optional[str] = None


class HundredQCohortResult(BaseModel):
    run_id: str
    created_at: str
    run_type: str = "quarterly_full"    # 'quarterly_full' | 'weekly_quant' | 'event_triggered' | 'adhoc'
    ticker_count: int = 0
    tier_counts: dict[str, int] = Field(default_factory=dict)
    failed_tickers: list[dict] = Field(default_factory=list)
    results: list[HundredQTickerResult] = Field(default_factory=list)


__all__ = [
    "Tier",
    "QuestionType",
    "PILLAR_IDS",
    "PILLAR_LABELS",
    "QuestionAnswer",
    "PillarScore",
    "HundredQTickerResult",
    "HundredQCohortResult",
    "QualEvidence",
    "QualIndicatorScore",
]
