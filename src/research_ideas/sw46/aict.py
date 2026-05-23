"""
src/research_ideas/sw46/aict.py
================================
AI Competitive Threat (AICT) Tiering, Cassandra Unchained / Scion.

Five tiers (Fortress / Castle / Chapel / Stone / Wood) capture the
business-model survival outlook in the AI era. Independent of TA and IV15
inputs — but feeds growth haircuts and terminal multiples into IV15.

v1 source: hand-curated JSON (data/sw46_aict_tiers.json).
v2 (future): LLM judgment from deep-research output.
"""
from __future__ import annotations

from src.research_ideas.sw46.schemas import AICTResult, AICTTier
from src.research_ideas.sw46.universe import load_aict_tiers


# AICT tier -> (growth_haircut, terminal_multiple, blend_buffett_weight).
# growth_haircut: fraction subtracted from raw 1-5yr growth in IV15.
# terminal_multiple: applied to Year-15 OE in the Buffett-flavor valuation.
# blend_buffett_weight: weight on the Buffett-multiple valuation in the
#                      final blend (1 - this = DDM weight). Fortress trusts
#                      the multiple more; Wood barely trusts it.
_TIER_TABLE: dict[AICTTier, tuple[float, float, float]] = {
    "Fortress": (0.00, 25.0, 0.50),
    "Castle":   (0.10, 20.0, 0.45),
    "Chapel":   (0.20, 17.0, 0.42),  # softened from (0.25, 15.0, 0.40)
    "Stone":    (0.30, 13.0, 0.35),  # softened from (0.40, 10.0, 0.30) — Stone covers
                                     # both "AI-vulnerable / weak R&D" and "low-AI-threat
                                     # but moderate competitive pressure" per article.
    "Wood":     (0.55,  7.0, 0.15),
}


def classify_aict(ticker: str) -> AICTResult:
    raw = load_aict_tiers().get(ticker, "Stone")
    if raw not in _TIER_TABLE:
        raw = "Stone"
    tier: AICTTier = raw  # type: ignore[assignment]
    haircut, multiple, weight = _TIER_TABLE[tier]
    return AICTResult(
        tier=tier,
        growth_haircut=haircut,
        terminal_multiple=multiple,
        blend_buffett_weight=weight,
    )
