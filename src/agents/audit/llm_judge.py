"""LLM judge for the Card QA Agent.

Classifies missing card fields into one of three verdicts:

  EXTRACTOR_DROPPED  — data IS in deep_research but the extractor missed
                       it. Triggers hinted re-extraction (Phase 4).
  WRONG_PROFILE      — card's applies_when predicate matched but the card
                       semantically doesn't apply. Flag for human review.
                       (Meta-Check should catch most of these in Phase 3,
                       but the judge is the second line of defense for
                       per-card edge cases meta-check missed.)
  GENUINELY_ABSENT   — data doesn't exist in source material for this
                       ticker. Mark explicit + accept; do not re-extract.

The judge call goes through `call_qwen_capped` which checks the CostCap
heartbeat BEFORE invoking the LLM. If budget is exhausted, returns a
sentinel verdict (BUDGET_EXHAUSTED) so the orchestrator can flag the
unaudited cards and stop calling.

Returned `evidence_quote` is truncated to MAX_EVIDENCE_CHARS (500) for
storage. Phase 4 uses a different code path (1000-char window centered
on hit position) when feeding the re-extractor.

Reuses utilities from `src/agents/industry/deep_research.py`:
  * _call_llm_with_rate_retry — DashScope 403/429 handling
  * _parse_llm_json           — robust JSON extraction from Qwen output
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from src.agents.audit.cost_cap import CostCap

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

MAX_EVIDENCE_CHARS: int = 500   # storage truncation; per plan §persistence schema
JUDGE_OUTPUT_MAX_TOKENS: int = 400
JUDGE_MAX_SOURCE_CHARS: int = 8000  # pre-trim deep_research before sending to Qwen
                                    # (full deep_research is ~35-50KB; sending it all
                                    #  would waste budget AND hit Qwen's 128K limit
                                    #  with extra context)

VALID_VERDICTS = ("EXTRACTOR_DROPPED", "WRONG_PROFILE", "GENUINELY_ABSENT")

# Sentinel returned when budget heartbeat refuses a new LLM call. The
# orchestrator (Phase 5) detects this and persists the card with
# reason="budget_exhausted_mid_run" in human_review_flags.
BUDGET_EXHAUSTED_SENTINEL = "BUDGET_EXHAUSTED"

# Per-call cost estimate when DashScope response doesn't expose usage.
# Conservative — actual Qwen-plus pricing is ~$0.001 per judge call but
# we err on the high side so budget exhaustion is predictable.
ESTIMATED_COST_PER_CALL_USD: float = 0.01


# ── Verdict dataclass ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeVerdict:
    """Structured output of one judge call.

    Carries enough context for downstream code to:
      * decide whether to fire re-extraction (verdict == EXTRACTOR_DROPPED)
      * flag for human review with actionable text (reasoning + evidence)
      * persist to card_qa_audit without further enrichment
    """

    verdict: str                   # one of VALID_VERDICTS or BUDGET_EXHAUSTED_SENTINEL
    reasoning: str                 # 1-2 sentences from the judge
    evidence_quote: str            # <= MAX_EVIDENCE_CHARS; "" when verdict in (WRONG_PROFILE,
                                   # GENUINELY_ABSENT, BUDGET_EXHAUSTED)
    evidence_offset: int | None    # approximate char offset in deep_research where the data
                                   # was found. None if judge couldn't pinpoint. Phase 4
                                   # re-extractor uses this to window source text.
    cost_usd: float                # estimated cost of this call
    raw_response: str              # for logging / debugging only (NOT persisted)


# ── Prompt template ─────────────────────────────────────────────────────────


_JUDGE_PROMPT_TEMPLATE = """\
You are a data-quality auditor for a financial analysis system.

A frontend card was supposed to display data for ticker {ticker}, but the
following field is missing from the persisted state:

    field: {missing_field}
    card:  {card_name}

Card-specific context (what the card is and what it expects):
{qa_prompt_hint}

Below is the deep_research narrative the analysis used as its source. \
Determine WHY the field is missing by classifying into exactly ONE of:

  1. "EXTRACTOR_DROPPED" — the data IS present in the deep_research text \
below but the extractor failed to capture it. (We will retry extraction \
with a hinted prompt; your evidence_quote will guide that retry.)

  2. "WRONG_PROFILE" — the card semantically doesn't apply to this ticker. \
(e.g. the card expects a SaaS metric but the ticker is a bank.)

  3. "GENUINELY_ABSENT" — the data does not exist in the source material \
for this ticker. The field can be marked "n/a" and accepted.

Respond with a JSON object ONLY (no preamble, no postamble):
{{
  "verdict": "EXTRACTOR_DROPPED" | "WRONG_PROFILE" | "GENUINELY_ABSENT",
  "reasoning": "<1-2 sentence justification>",
  "evidence_quote": "<verbatim snippet from deep_research showing the data,\
 or empty string if verdict != EXTRACTOR_DROPPED>",
  "evidence_offset": <integer char-offset in deep_research where evidence_quote \
starts, or null if unknown>
}}

== deep_research (truncated to first {source_chars} chars) ==
{source_text}
== end deep_research ==
"""


def _build_judge_prompt(
    ticker: str,
    card_name: str,
    missing_field: str,
    qa_prompt_hint: str,
    source_text: str,
) -> str:
    """Render the judge prompt. Truncates source_text to JUDGE_MAX_SOURCE_CHARS
    so the prompt stays within token budget."""
    trimmed = source_text[:JUDGE_MAX_SOURCE_CHARS]
    return _JUDGE_PROMPT_TEMPLATE.format(
        ticker=ticker,
        card_name=card_name,
        missing_field=missing_field,
        qa_prompt_hint=qa_prompt_hint,
        source_text=trimmed,
        source_chars=len(trimmed),
    )


# ── Response parsing ────────────────────────────────────────────────────────


def _truncate_evidence(quote: Any) -> str:
    """Coerce + truncate evidence_quote to MAX_EVIDENCE_CHARS.

    Why truncate at the judge boundary: Phase 8 SQL aggregations join
    on `evidence_quote` substrings; an unbounded quote could blow up
    web_runs storage (~80% bloat per audit row). 500 chars is enough
    for a reviewer to verify the judge's reasoning without storage cost.
    """
    if quote is None:
        return ""
    s = str(quote)
    if len(s) <= MAX_EVIDENCE_CHARS:
        return s
    return s[:MAX_EVIDENCE_CHARS]


def _parse_judge_response(raw: str) -> tuple[str, str, str, int | None]:
    """Extract (verdict, reasoning, evidence_quote, evidence_offset) from raw
    Qwen text. Returns ("GENUINELY_ABSENT", "<parse failed>", "", None) when
    the response is malformed — safest default since no remediation fires.
    """
    # Late import to keep llm_judge importable without deep_research
    from src.agents.industry.deep_research import _parse_llm_json   # type: ignore

    parsed = _parse_llm_json(raw, extractor_name="card_qa_judge")
    if not isinstance(parsed, dict):
        return "GENUINELY_ABSENT", "judge response could not be parsed as JSON", "", None

    verdict = str(parsed.get("verdict", "")).strip().upper()
    if verdict not in VALID_VERDICTS:
        return (
            "GENUINELY_ABSENT",
            f"judge returned unknown verdict {verdict!r}; defaulted to GENUINELY_ABSENT",
            "",
            None,
        )

    reasoning = str(parsed.get("reasoning", "")).strip()
    evidence_quote = _truncate_evidence(parsed.get("evidence_quote", ""))

    offset_raw = parsed.get("evidence_offset")
    evidence_offset: int | None
    try:
        evidence_offset = int(offset_raw) if offset_raw is not None else None
    except (ValueError, TypeError):
        evidence_offset = None

    return verdict, reasoning, evidence_quote, evidence_offset


# ── LLM call wrapper with budget heartbeat ──────────────────────────────────


def call_qwen_capped(
    *,
    prompt: str,
    cost_cap: CostCap,
    extractor_name: str = "card_qa_judge",
    max_tokens: int = JUDGE_OUTPUT_MAX_TOKENS,
    sdk_client: Any = None,
    model_name: str | None = None,
) -> tuple[str, float]:
    """Single Qwen call with pre-flight budget check (the "heartbeat").

    Returns (raw_response_text, estimated_cost_usd). The cost is recorded
    on the CostCap BEFORE this function returns so callers don't need to
    remember to do it.

    When budget is exhausted, returns (BUDGET_EXHAUSTED_SENTINEL, 0.0) WITHOUT
    making the LLM call. Callers detect this and skip downstream work.

    Why the wrapper exists (vs callers checking cost_cap directly):
      * One choke point for the budget check → can't be bypassed.
      * Future caching can hook here without touching call sites.
      * Test mocks override this single function instead of mocking the
        entire DashScope client.

    Args:
      prompt:          user-content string sent to Qwen
      cost_cap:        the run's CostCap instance
      extractor_name:  for logging/retry context
      max_tokens:      output token cap (Qwen)
      sdk_client:      optional preconstructed anthropic.Anthropic client. If None,
                       builds one from env vars (DEEP_RESEARCH_API_KEY/_BASE_URL).
                       Tests inject a Mock here.
      model_name:      Qwen model id; defaults to DEEP_RESEARCH_MODEL env or
                       'qwen3.6-plus'.
    """
    # Heartbeat: refuse the call if budget exhausted.
    if not cost_cap.check_headroom():
        logger.info(
            "call_qwen_capped: budget exhausted (accumulated=%.4f, max=%.2f) — "
            "returning BUDGET_EXHAUSTED sentinel",
            cost_cap.accumulated_usd, cost_cap.max_usd,
        )
        return BUDGET_EXHAUSTED_SENTINEL, 0.0

    # Build client lazily so tests can inject without importing anthropic.
    if sdk_client is None:
        import anthropic                                    # type: ignore
        api_key  = os.environ.get("DEEP_RESEARCH_API_KEY")
        base_url = os.environ.get(
            "DEEP_RESEARCH_BASE_URL",
            "https://dashscope-intl.aliyuncs.com/apps/anthropic",
        )
        if not api_key:
            logger.warning(
                "call_qwen_capped: DEEP_RESEARCH_API_KEY not set — returning empty "
                "response as if judge couldn't decide"
            )
            cost_cap.add(0.0)
            return "", 0.0
        sdk_client = anthropic.Anthropic(
            api_key=api_key, base_url=base_url, timeout=60.0, max_retries=1,
        )

    if model_name is None:
        model_name = os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL") \
            or os.environ.get("DEEP_RESEARCH_MODEL") \
            or "qwen3.6-plus"

    # Late import to avoid pulling deep_research module on every audit import
    from src.agents.industry.deep_research import _call_llm_with_rate_retry  # type: ignore

    resp = _call_llm_with_rate_retry(
        sdk_client,
        extractor_name=extractor_name,
        model=model_name,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract text from Anthropic-style response (DashScope mirrors this).
    raw = ""
    try:
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception:
        raw = str(resp)

    cost_cap.add(ESTIMATED_COST_PER_CALL_USD)
    return raw, ESTIMATED_COST_PER_CALL_USD


# ── Public judge entry point ────────────────────────────────────────────────


def judge_missing_field(
    *,
    ticker: str,
    card_name: str,
    missing_field: str,
    qa_prompt_hint: str,
    deep_research: str,
    cost_cap: CostCap,
    sdk_client: Any = None,
    model_name: str | None = None,
) -> JudgeVerdict:
    """Run the LLM judge on one (card, missing_field) pair.

    Returns a JudgeVerdict regardless of outcome — never raises. On any
    error path (parse failure, budget exhausted, network) it returns a
    sensible default verdict so the orchestrator can keep auditing other
    cards without crashing.
    """
    prompt = _build_judge_prompt(
        ticker=ticker,
        card_name=card_name,
        missing_field=missing_field,
        qa_prompt_hint=qa_prompt_hint,
        source_text=deep_research or "",
    )

    raw, cost = call_qwen_capped(
        prompt=prompt,
        cost_cap=cost_cap,
        extractor_name=f"card_qa_judge.{card_name}",
        sdk_client=sdk_client,
        model_name=model_name,
    )

    if raw == BUDGET_EXHAUSTED_SENTINEL:
        return JudgeVerdict(
            verdict=BUDGET_EXHAUSTED_SENTINEL,
            reasoning="QA budget exhausted before this card could be judged",
            evidence_quote="",
            evidence_offset=None,
            cost_usd=0.0,
            raw_response="",
        )

    verdict, reasoning, evidence_quote, evidence_offset = _parse_judge_response(raw)
    return JudgeVerdict(
        verdict=verdict,
        reasoning=reasoning,
        evidence_quote=evidence_quote,
        evidence_offset=evidence_offset,
        cost_usd=cost,
        raw_response=raw,
    )
