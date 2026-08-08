"""
src/research_ideas/hundred_q/scoring.py
==========================================
Pure quant scorer — no LLM calls, no I/O beyond what's already in the
bundle/ctx. Runs every QUANT-tagged question in questions_registry.REGISTRY
against one ticker's HundredQBundle and rolls the answers into per-pillar
and overall composite scores.

Composite is a FLAT aggregation (yes_answers / answered_questions) per the
approved plan — no invented pillar weights.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.research_ideas.hundred_q.data_fetch import HundredQBundle
from src.research_ideas.hundred_q.questions_registry import REGISTRY
from src.research_ideas.hundred_q.schemas import PILLAR_LABELS, PillarScore, QuestionAnswer


def aggregate_answers(ledger: list[QuestionAnswer]) -> dict:
    """
    Roll a list of QuestionAnswer (quant and/or qual, mixed) into
    per-pillar scores and a flat overall composite_pct
    (yes_answers / answered_questions across ALL answered questions,
    quant+qual combined — no invented pillar weights, per the approved
    plan). Shared by the quant-only Phase-0 path (score_bundle) and the
    quant+qual merged view (runner.py::assemble_full_ticker_result).
    """
    per_pillar_answered: dict[str, int] = {}
    per_pillar_yes: dict[str, int] = {}
    total_answered = 0
    total_yes = 0

    for qa in ledger:
        if qa.answer is None:
            continue
        per_pillar_answered[qa.pillar] = per_pillar_answered.get(qa.pillar, 0) + 1
        total_answered += 1
        if qa.answer:
            per_pillar_yes[qa.pillar] = per_pillar_yes.get(qa.pillar, 0) + 1
            total_yes += 1

    pillar_scores: list[PillarScore] = []
    for pillar_id, label in PILLAR_LABELS.items():
        answered = per_pillar_answered.get(pillar_id, 0)
        yes = per_pillar_yes.get(pillar_id, 0)
        pillar_scores.append(PillarScore(
            pillar=pillar_id,
            label=label,
            questions_answered=answered,
            questions_yes=yes,
            pillar_pct=(yes / answered) if answered else None,
        ))

    composite_pct: Optional[float] = (total_yes / total_answered) if total_answered else None
    return {"pillar_scores": pillar_scores, "composite_pct": composite_pct}


def score_bundle(bundle: HundredQBundle, ctx: dict) -> dict:
    """
    Score one ticker's bundle against every quant question in the registry.

    Returns:
      {
        "question_ledger": list[QuestionAnswer],
        "pillar_scores": list[PillarScore],
        "quant_composite_pct": float | None,
      }
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ledger: list[QuestionAnswer] = []

    for qdef in REGISTRY.values():
        if qdef.quant_fn is None:
            ledger.append(QuestionAnswer(
                question_id=qdef.question_id,
                pillar=qdef.pillar,
                label=qdef.label,
                q_type="quant",
                answer=None,
                source="data_unavailable",
                threshold_desc=qdef.deferred_reason,
                evaluated_at=now_iso,
            ))
            continue

        try:
            answer, raw_value = qdef.quant_fn(bundle, ctx)
        except Exception as exc:
            answer, raw_value = None, f"quant_fn_error: {exc}"

        ledger.append(QuestionAnswer(
            question_id=qdef.question_id,
            pillar=qdef.pillar,
            label=qdef.label,
            q_type="quant",
            answer=answer,
            raw_value=raw_value,
            threshold_desc=qdef.threshold_desc or None,
            source="fmp_edgar_derived",
            evaluated_at=now_iso,
        ))

    agg = aggregate_answers(ledger)
    return {
        "question_ledger": ledger,
        "pillar_scores": agg["pillar_scores"],
        "quant_composite_pct": agg["composite_pct"],
    }


def tier_for(composite_pct: Optional[float]) -> str:
    if composite_pct is None:
        return "not_evaluated"
    if composite_pct >= 0.65:
        return "active_pass"
    if composite_pct >= 0.55:
        return "on_deck"
    return "cooloff"
