"""CARD_SCHEMAS — catalog of frontend cards the QA agent inspects.

Each schema entry describes ONE frontend card's data contract:
  * applies_when            — predicate gating whether to audit this card
                              for a given (state, ticker)
  * mandatory_state_paths   — dot-notation paths that MUST resolve to a
                              non-empty value. Phase 2 LLM judge fires
                              only when a mandatory path is empty.
  * opportunistic_state_paths — best-effort paths; absence flagged but
                              never escalated to human review
  * qa_prompt_hint          — card-specific guidance the LLM judge uses
                              when classifying the failure
  * schema_version          — bump when ANY field above changes; Phase 8
                              Pattern Analysis filters audits by version
                              so schema drift doesn't poison aggregations

Phase 1 ships ONE card (biopharma_pipeline_rnpv) so the orchestration
plumbing is proven before scaling to the full ~12-card catalog in Phase 7.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# Type alias for `applies_when` predicates. Takes the full pipeline
# `state` dict (NOT state["data"]) and a ticker symbol.
AppliesWhen = Callable[[dict, str], bool]


@dataclass(frozen=True)
class CardSchema:
    """Audit contract for one frontend card."""

    schema_version: int
    applies_when: AppliesWhen
    mandatory_state_paths: tuple[str, ...]
    opportunistic_state_paths: tuple[str, ...]
    qa_prompt_hint: str


# ── Predicate helpers ───────────────────────────────────────────────────────

def _data(state: dict) -> dict:
    """Safe accessor for state['data']."""
    d = state.get("data")
    return d if isinstance(d, dict) else {}


def _is_biopharma_pre_approval(state: dict, ticker: str) -> bool:
    """True when ticker is classified as a pre-approval / clinical-stage
    biotech — the cohort for which Pipeline rNPV is the dominant valuation
    method. Large-cap pharma (e.g. NVO, PFE) uses different cards.

    Predicate uses the CURRENT persisted classification, even if it might
    be wrong. Catching wrong classifications is Meta-Check's job (Phase 3),
    not this predicate's. Keeping the two responsibilities separate prevents
    the QA agent from second-guessing every applies_when decision via LLM.
    """
    data = _data(state)
    sectors = data.get("sectors") or {}
    profile_names = data.get("profile_names") or {}
    if sectors.get(ticker) != "Biopharma":
        return False
    profile = profile_names.get(ticker) or ""
    return "Pre-approval Biotech" in profile or "Clinical-Stage" in profile


# ── Card catalog ────────────────────────────────────────────────────────────

CARD_SCHEMAS: dict[str, CardSchema] = {
    "biopharma_pipeline_rnpv": CardSchema(
        schema_version=1,
        applies_when=_is_biopharma_pre_approval,
        mandatory_state_paths=(
            "data.dcf_range.{ticker}",
        ),
        opportunistic_state_paths=(
            "data.dcf_range.{ticker}.base.intrinsic_value",
            "data.dcf_range.{ticker}.bear.intrinsic_value",
            "data.dcf_range.{ticker}.bull.intrinsic_value",
        ),
        qa_prompt_hint=(
            "This is the Pipeline rNPV / Share card for a pre-approval "
            "Biopharma ticker. It needs a populated dcf_range[ticker] "
            "with bear/base/bull intrinsic_value entries computed by the "
            "DCF agent. If dcf_range[ticker] is empty {{}}, the DCF agent "
            "likely crashed — check state.data.dcf_engine_error first. "
            "If both dcf_range AND dcf_engine_error are absent, the run "
            "pre-dates the diagnostic landing and the cause cannot be "
            "directly verified from this state alone."
        ),
    ),
    # Phase 7 will add the remaining 9-14 cards (biopharma_pipeline_table,
    # managed_care_sector_card, tech_saas_card, bank_card, reit_card, ...).
}
