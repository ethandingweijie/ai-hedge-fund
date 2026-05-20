"""Hinted re-extraction for the Card QA Agent — Phase 4.

When the LLM judge classifies a missing field as EXTRACTOR_DROPPED, we
re-extract using the judge's evidence_quote as a HIGH-PRECISION HINT
rather than blind-retrying the original extractor prompt.

Why hinted vs blind:
  * Blind retry repeats the original failure mode (~$0.08, low success)
  * Hinted retry narrows scope to one quote (~$0.02, high success)
  * Hinted prompt is FALSIFIABLE — if the evidence_quote doesn't actually
    contain the data (judge was wrong), the extractor returns NOT_FOUND
    and we accept that gracefully. Critical for avoiding hallucinated values.

Non-negotiable invariant (per plan Phase 4 Gate):
  When the evidence_quote is misleading (e.g. wrong field, wrong year,
  type mismatch), this module MUST return NOT_FOUND — never fabricate.
  A single false-positive value defeats the entire purpose of Layer A.

The 1000-char windowed truncation (centered on evidence_offset) feeds
the LLM a tight slice of deep_research around the alleged hit, so the
re-extractor has enough context to verify but not enough to wander.

Public surface:
  reextract_with_hint(field_name, evidence_quote, judge_reasoning,
                      deep_research, hit_offset, cost_cap) → ReextractResult
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.agents.audit.cost_cap import CostCap
from src.agents.audit.llm_judge import (
    BUDGET_EXHAUSTED_SENTINEL,
    call_qwen_capped,
)

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

# Source window centered on the judge's hit_offset. 1000 chars is roughly
# 200 tokens of context — enough for the re-extractor to verify whether
# the quote ACTUALLY contains the field's value vs. a same-shaped number
# for a different field (e.g. 2027 peak vs. 2024 actual revenue).
SOURCE_WINDOW_CHARS: int = 1000

# Output cap. Re-extraction returns a value + 1-sentence reasoning, so
# 200 tokens is plenty. Strict cap protects budget vs. a verbose Qwen.
REEXTRACT_OUTPUT_MAX_TOKENS: int = 200

# Sentinel emitted by the LLM when the evidence_quote doesn't actually
# contain the value. Detecting this string is what protects against
# hallucinated extractions.
NOT_FOUND = "NOT_FOUND"


# ── Result dataclass ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReextractResult:
    """Outcome of one hinted re-extraction.

    The orchestrator (Phase 5) uses `found` to decide whether to mutate
    state[field_path] with `value`. If `found` is False, the orchestrator
    persists the failure to human_review_flags WITHOUT touching state.
    """

    found: bool                    # True only if extractor confidently extracted a value
    value: Any                     # extracted value (string/number/dict). Defined only when found=True.
    confidence: str                # 'high' | 'medium' | 'low' | 'budget_exhausted'
    reasoning: str                 # 1-sentence explanation
    cost_usd: float                # cost of this call
    budget_exhausted: bool         # True iff budget heartbeat refused the call


# ── Source windowing ────────────────────────────────────────────────────────


def _truncate_centered(
    text: str,
    hit_offset: int | None,
    window: int = SOURCE_WINDOW_CHARS,
) -> str:
    """Return a `window`-char slice of `text` centered on `hit_offset`.

    Fallback: if `hit_offset` is None or out-of-range, returns the first
    `window` chars (the leading-prefix fallback the plan explicitly calls
    out). Always returns at most `window` chars.

    Why centered: in Biopharma narratives, a single value like "$1.5B"
    can appear in multiple contexts (peak vs. current revenue vs. cap).
    A leading-prefix window often cuts off the immediate disambiguating
    sentence. Centering preserves the BEFORE and AFTER context the
    re-extractor needs to verify the right field.
    """
    if not text:
        return ""
    if len(text) <= window:
        return text
    if hit_offset is None or hit_offset < 0 or hit_offset >= len(text):
        return text[:window]
    half = window // 2
    start = max(0, hit_offset - half)
    end = min(len(text), start + window)
    # If the right edge ran off the end, slide start back so we still
    # get a full `window`-char slice.
    if end - start < window and start > 0:
        start = max(0, end - window)
    return text[start:end]


# ── Prompt template ─────────────────────────────────────────────────────────


_REEXTRACT_PROMPT_TEMPLATE = """\
You are a precision financial-data extractor.

An earlier extractor missed the field:
    field_name: {field_name}

A reviewer LLM identified that the data MAY appear in this verbatim quote
from the source:
    "{evidence_quote}"

Reviewer's reasoning: {judge_reasoning}

YOUR TASK
Extract the value of '{field_name}' if (and only if) the evidence_quote
actually contains it. Be strict — DO NOT extract a number that looks
similar but is for a different field, time period, or unit.

If the evidence_quote does NOT contain a value for '{field_name}' \
(reviewer was wrong, or the quote describes a different field), \
respond with value="{not_found_sentinel}". \
Better to return NOT_FOUND than fabricate a plausible-looking value — \
this entire system depends on you being honest about this case.

Respond with a JSON object ONLY (no preamble, no postamble):
{{
  "value": <extracted value> or "{not_found_sentinel}",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<1 sentence — why you chose this value, OR why NOT_FOUND>"
}}

== source context (centered window from deep_research) ==
{source_window}
== end source context ==
"""


def _build_reextract_prompt(
    field_name: str,
    evidence_quote: str,
    judge_reasoning: str,
    source_window: str,
) -> str:
    return _REEXTRACT_PROMPT_TEMPLATE.format(
        field_name=field_name,
        evidence_quote=evidence_quote.replace('"', "'"),   # avoid breaking the inner quote
        judge_reasoning=judge_reasoning or "(none provided)",
        source_window=source_window,
        not_found_sentinel=NOT_FOUND,
    )


# ── Response parsing ────────────────────────────────────────────────────────


def _parse_reextract_response(raw: str) -> ReextractResult:
    """Extract structured ReextractResult from Qwen's raw text.

    Defaults to (found=False, NOT_FOUND) on any parse failure — same
    safety as the judge's parser. Never raises.
    """
    from src.agents.industry.deep_research import _parse_llm_json   # type: ignore

    parsed = _parse_llm_json(raw, extractor_name="card_qa_reextract")
    if not isinstance(parsed, dict):
        return ReextractResult(
            found=False, value=None, confidence="low",
            reasoning="re-extract response could not be parsed as JSON",
            cost_usd=0.0, budget_exhausted=False,
        )

    raw_value = parsed.get("value")
    confidence = str(parsed.get("confidence", "low")).strip().lower()
    reasoning = str(parsed.get("reasoning", "")).strip()

    # NOT_FOUND sentinel detection — strict + case-insensitive. Any of:
    #   "NOT_FOUND", "not_found", "Not_Found", "NOTFOUND" (with underscore tolerance)
    is_not_found = (
        raw_value is None
        or (isinstance(raw_value, str) and raw_value.strip().upper().replace(" ", "_") == NOT_FOUND)
    )
    if is_not_found:
        return ReextractResult(
            found=False, value=None, confidence=confidence,
            reasoning=reasoning or "extractor returned NOT_FOUND",
            cost_usd=0.0, budget_exhausted=False,
        )

    return ReextractResult(
        found=True, value=raw_value, confidence=confidence,
        reasoning=reasoning, cost_usd=0.0, budget_exhausted=False,
    )


# ── Public entry point ─────────────────────────────────────────────────────


def reextract_with_hint(
    *,
    field_name: str,
    evidence_quote: str,
    judge_reasoning: str,
    deep_research: str,
    hit_offset: int | None,
    cost_cap: CostCap,
    sdk_client: Any = None,
    model_name: str | None = None,
) -> ReextractResult:
    """Re-extract `field_name` using the judge's evidence as a hint.

    Returns a ReextractResult. `found=False` for ANY failure mode (parse
    failure, budget exhausted, NOT_FOUND sentinel, empty evidence quote).
    The caller MUST check `found` before reading `value`.

    Args:
      field_name:       e.g. "data.pipeline_assets.MRNA[0].peak_sales_usd"
      evidence_quote:   verbatim snippet returned by the judge
      judge_reasoning:  judge's 1-2 sentence reasoning (forwarded to extractor)
      deep_research:    full deep_research text (will be windowed)
      hit_offset:       judge's evidence_offset; window is centered on this
      cost_cap:         shared per-ticker CostCap
    """
    # Defensive: empty evidence_quote means the judge had no real hint
    # (shouldn't happen if the judge respects its contract, but Qwen can
    # be unpredictable). Skip the LLM call entirely.
    if not evidence_quote or not evidence_quote.strip():
        logger.info(
            "reextract: skipping for field=%s — empty evidence_quote (judge may have hallucinated)",
            field_name,
        )
        return ReextractResult(
            found=False, value=None, confidence="low",
            reasoning="empty evidence_quote — judge produced no usable hint",
            cost_usd=0.0, budget_exhausted=False,
        )

    source_window = _truncate_centered(deep_research, hit_offset)
    prompt = _build_reextract_prompt(
        field_name=field_name,
        evidence_quote=evidence_quote,
        judge_reasoning=judge_reasoning,
        source_window=source_window,
    )

    raw, cost = call_qwen_capped(
        prompt=prompt,
        cost_cap=cost_cap,
        extractor_name=f"card_qa_reextract.{field_name[:60]}",
        max_tokens=REEXTRACT_OUTPUT_MAX_TOKENS,
        sdk_client=sdk_client,
        model_name=model_name,
    )

    if raw == BUDGET_EXHAUSTED_SENTINEL:
        return ReextractResult(
            found=False, value=None, confidence="budget_exhausted",
            reasoning="QA budget exhausted before this field could be re-extracted",
            cost_usd=0.0, budget_exhausted=True,
        )

    result = _parse_reextract_response(raw)
    # Inject the actual cost since _parse_reextract_response can't know it.
    return ReextractResult(
        found=result.found,
        value=result.value,
        confidence=result.confidence,
        reasoning=result.reasoning,
        cost_usd=cost,
        budget_exhausted=False,
    )
