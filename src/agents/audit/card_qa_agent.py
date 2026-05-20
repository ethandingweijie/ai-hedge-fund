"""Card QA Agent — Phase 10.5 orchestrator (full version, Phases 1-5).

Run order per ticker:
  1. Meta-Check (Phase 10.5a) — validate upstream classification.
     If it fails, persist single human_review_flag and SKIP card audits.
  2. Card audits (Phase 10.5b) — for each card whose applies_when matches:
     a. Deterministic walk: are mandatory_state_paths populated?
     b. If missing, call LLM judge (budget-capped)
     c. If judge says EXTRACTOR_DROPPED, call hinted re-extractor
     d. On successful re-extraction, mutate state[field] in place
     e. On WRONG_PROFILE or GENUINELY_ABSENT, persist a human_review_flag
  3. Return audit dict matching the persistence schema (plan §persistence).

The orchestrator NEVER raises. Any per-card failure is logged and the
audit dict still gets returned (the pipeline integration in Phase 6
wraps this in try/except as defense in depth, but the orchestrator
itself should always produce a valid dict).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.agents.audit.card_schemas import CARD_SCHEMAS, CardSchema
from src.agents.audit.cost_cap import CostCap, DEFAULT_BUDGET_USD
from src.agents.audit.llm_judge import (
    BUDGET_EXHAUSTED_SENTINEL,
    JudgeVerdict,
    judge_missing_field,
)
from src.agents.audit.meta_check import run_meta_check
from src.agents.audit.reextract import ReextractResult, reextract_with_hint

logger = logging.getLogger(__name__)


QA_VERSION = "v1"
DEFAULT_QA_MODEL = "qwen3.6-plus"


# ── Path-walking helpers ────────────────────────────────────────────────────

def _get_path(state: dict, path: str, ticker: str) -> Any:
    """Walk a dot-notation path against state, substituting {ticker}.

    Returns None on any missing intermediate segment or non-dict node.
    """
    parts = path.replace("{ticker}", ticker).split(".")
    node: Any = state
    for p in parts:
        if isinstance(node, dict):
            node = node.get(p)
        else:
            return None
        if node is None:
            return None
    return node


def _set_path(state: dict, path: str, ticker: str, value: Any) -> bool:
    """Set state[path] = value, creating intermediate dicts as needed.

    Returns True on success, False if a path segment refused mutation
    (e.g. encountered a non-dict at some intermediate level).

    Used by Phase 5 to apply successful re-extractions back to state.
    Only handles dict-only paths — array indexing (e.g. `[0]`) is a
    Phase 7 concern when biopharma_pipeline_table adds per-asset paths.
    """
    parts = path.replace("{ticker}", ticker).split(".")
    if not parts:
        return False
    node: Any = state
    for p in parts[:-1]:
        if not isinstance(node, dict):
            return False
        if p not in node:
            # Missing intermediate — safe to create a fresh dict
            node[p] = {}
        elif not isinstance(node[p], dict):
            # Refuse to overwrite an existing non-dict (e.g. list, scalar).
            # The auto-remediation should not destroy unrelated data; if
            # the path doesn't lead to a dict, the original extractor
            # produced an incompatible shape and we must flag instead.
            return False
        node = node[p]
    if not isinstance(node, dict):
        return False
    node[parts[-1]] = value
    return True


def _is_empty_value(v: Any) -> bool:
    """A path's resolved value counts as 'missing' if None or empty container."""
    if v is None:
        return True
    if isinstance(v, (list, dict, str)) and len(v) == 0:
        return True
    return False


# ── Per-card audit (Phase 10.5b inner loop) ────────────────────────────────


def _audit_one_card(
    *,
    state: dict,
    ticker: str,
    card_name: str,
    schema: CardSchema,
    cost_cap: CostCap,
    sdk_client: Any,
    model_name: str | None,
    deep_research: str,
) -> tuple[dict, list[dict], list[dict]]:
    """Audit ONE card for ONE ticker.

    Returns (card_entry, remediations, flags) — the orchestrator appends
    these to the run-level audit dict.
    """
    remediations: list[dict] = []
    flags: list[dict] = []

    # Step 1: deterministic walk
    missing_mandatory: list[str] = []
    for path in schema.mandatory_state_paths:
        v = _get_path(state, path, ticker)
        if _is_empty_value(v):
            missing_mandatory.append(path.replace("{ticker}", ticker))

    card_entry = {
        "card":                  card_name,
        "applies_when_passed":   True,
        "missing_mandatory":     missing_mandatory,
        "missing_opportunistic": [],
        "judge_verdict":         None,
        "judge_reasoning":       None,
        "judge_evidence_quote":  None,
        "remediation_attempted": False,
        "remediation_success":   None,
    }

    if not missing_mandatory:
        # Clean — no judge call needed. Saves budget.
        return card_entry, remediations, flags

    # Step 2: invoke judge on the FIRST missing field. (Multiple missing
    # fields in one card → only judge the first; the other fields are
    # almost always symptoms of the same root cause and we'd waste budget
    # asking the LLM about each. The fix in Phase 4 re-extracts each one
    # independently if EXTRACTOR_DROPPED is the verdict.)
    primary_missing = missing_mandatory[0]
    verdict: JudgeVerdict = judge_missing_field(
        ticker=ticker,
        card_name=card_name,
        missing_field=primary_missing,
        qa_prompt_hint=schema.qa_prompt_hint,
        deep_research=deep_research,
        cost_cap=cost_cap,
        sdk_client=sdk_client,
        model_name=model_name,
    )
    card_entry["judge_verdict"] = verdict.verdict
    card_entry["judge_reasoning"] = verdict.reasoning
    card_entry["judge_evidence_quote"] = verdict.evidence_quote

    if verdict.verdict == BUDGET_EXHAUSTED_SENTINEL:
        flags.append({
            "card":           card_name,
            "field":          primary_missing,
            "reason":         "budget_exhausted_mid_run",
            "context":        verdict.reasoning,
            "evidence_quote": "",
        })
        return card_entry, remediations, flags

    if verdict.verdict == "WRONG_PROFILE":
        # The judge thinks this card semantically doesn't apply. Flag
        # but do NOT attempt re-extraction (there's nothing to extract).
        flags.append({
            "card":           card_name,
            "field":          primary_missing,
            "reason":         "wrong_profile_per_judge",
            "context":        verdict.reasoning,
            "evidence_quote": verdict.evidence_quote,
        })
        return card_entry, remediations, flags

    if verdict.verdict == "GENUINELY_ABSENT":
        # Data doesn't exist for this ticker. Mark explicit + accept.
        # Flag so the user can see it on the report, but don't escalate.
        flags.append({
            "card":           card_name,
            "field":          primary_missing,
            "reason":         "genuinely_absent_per_judge",
            "context":        verdict.reasoning,
            "evidence_quote": verdict.evidence_quote,
        })
        return card_entry, remediations, flags

    # verdict.verdict == "EXTRACTOR_DROPPED" → try hinted re-extraction
    card_entry["remediation_attempted"] = True
    for field_path in missing_mandatory:
        rresult: ReextractResult = reextract_with_hint(
            field_name=field_path,
            evidence_quote=verdict.evidence_quote,
            judge_reasoning=verdict.reasoning,
            deep_research=deep_research,
            hit_offset=verdict.evidence_offset,
            cost_cap=cost_cap,
            sdk_client=sdk_client,
            model_name=model_name,
        )
        if rresult.budget_exhausted:
            flags.append({
                "card": card_name, "field": field_path,
                "reason": "budget_exhausted_mid_run",
                "context": "budget cap hit during re-extraction",
                "evidence_quote": "",
            })
            break
        if rresult.found:
            ok = _set_path(state, field_path, ticker, rresult.value)
            if ok:
                remediations.append({
                    "card":      card_name,
                    "field":     field_path,
                    "method":    "hinted_reextract",
                    "value_set": rresult.value,
                })
                card_entry["remediation_success"] = True
            else:
                # State path couldn't be set (e.g. non-dict intermediate)
                flags.append({
                    "card": card_name, "field": field_path,
                    "reason": "remediation_state_write_failed",
                    "context": f"path {field_path!r} could not be set in state",
                    "evidence_quote": verdict.evidence_quote,
                })
        else:
            # Re-extractor returned NOT_FOUND — judge was wrong, accept it.
            flags.append({
                "card": card_name, "field": field_path,
                "reason": "reextract_returned_not_found",
                "context": rresult.reasoning,
                "evidence_quote": verdict.evidence_quote,
            })
            # remediation_success stays None (attempted but no value)
            if card_entry["remediation_success"] is None:
                card_entry["remediation_success"] = False

    return card_entry, remediations, flags


# ── Orchestrator (Phase 10.5 top-level) ────────────────────────────────────


def run_card_qa_agent(
    state: dict,
    ticker: str,
    *,
    qa_model: str = DEFAULT_QA_MODEL,
    budget_usd: float = DEFAULT_BUDGET_USD,
    sdk_client: Any = None,
    model_name: str | None = None,
) -> dict:
    """Audit one ticker's cards. See module docstring for the full flow.

    Args:
      state:      full pipeline state dict
      ticker:     ticker to audit
      qa_model:   identifier recorded in audit dict; LLM model_name override below
      budget_usd: per-ticker hard cap (defaults to DEFAULT_BUDGET_USD = $0.50)
      sdk_client: optional Anthropic-compat SDK client (DI for tests)
      model_name: Qwen model id (defaults to DEEP_RESEARCH_MODEL env)

    Returns audit dict matching persistence schema. Never raises.
    """
    now = datetime.now(timezone.utc).isoformat()
    cost_cap = CostCap(max_usd=budget_usd)
    schema_versions = {n: s.schema_version for n, s in CARD_SCHEMAS.items()}

    # Phase 10.5a: Meta-Check.
    try:
        meta_check = run_meta_check(state, ticker)
    except Exception as exc:
        logger.exception("card_qa_agent[%s]: meta_check crashed: %s", ticker, exc)
        meta_check = {
            "passed": True, "checks_run": [], "issues": [],
            "suggested_profile": None,
            "_error": f"meta_check_crashed: {type(exc).__name__}",
        }

    # If Meta-Check failed, short-circuit: no card audits, single flag.
    if not meta_check.get("passed", True):
        flag = {
            "card":           None,
            "field":          None,
            "reason":         "classification_likely_wrong",
            "context":        "; ".join(meta_check.get("issues", [])),
            "evidence_quote": "",
            "suggested_profile": meta_check.get("suggested_profile"),
        }
        return {
            "qa_version":           QA_VERSION,
            "qa_ran_at":            now,
            "qa_model":             qa_model,
            "qa_schema_versions":   schema_versions,
            "meta_check":           meta_check,
            "cards_inspected":      [],
            "auto_remediations":    [],
            "human_review_flags":   [flag],
            "qa_cost_estimate_usd": cost_cap.accumulated_usd,
            "qa_budget_hit":        False,
        }

    # Phase 10.5b: per-card audits.
    deep_research = ""
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    dr = data.get("deep_research") if isinstance(data, dict) else None
    if isinstance(dr, str):
        deep_research = dr

    cards_inspected: list[dict] = []
    auto_remediations: list[dict] = []
    human_review_flags: list[dict] = []

    for card_name, schema in CARD_SCHEMAS.items():
        try:
            applies = schema.applies_when(state, ticker)
        except Exception as exc:
            logger.warning(
                "card_qa_agent[%s]: applies_when raised for card=%s: %s",
                ticker, card_name, exc,
            )
            applies = False

        if not applies:
            continue

        try:
            entry, remediations, flags = _audit_one_card(
                state=state, ticker=ticker,
                card_name=card_name, schema=schema,
                cost_cap=cost_cap,
                sdk_client=sdk_client, model_name=model_name,
                deep_research=deep_research,
            )
        except Exception as exc:
            logger.exception(
                "card_qa_agent[%s]: card %s audit crashed: %s",
                ticker, card_name, exc,
            )
            # Defensive: keep going on the next card.
            entry = {
                "card": card_name, "applies_when_passed": True,
                "missing_mandatory": [],
                "missing_opportunistic": [],
                "judge_verdict": None, "judge_reasoning": None,
                "judge_evidence_quote": None,
                "remediation_attempted": False, "remediation_success": None,
                "_error": f"audit_crashed: {type(exc).__name__}",
            }
            remediations, flags = [], []

        cards_inspected.append(entry)
        auto_remediations.extend(remediations)
        human_review_flags.extend(flags)

    qa_budget_hit = not cost_cap.check_headroom()
    return {
        "qa_version":           QA_VERSION,
        "qa_ran_at":            now,
        "qa_model":             qa_model,
        "qa_schema_versions":   schema_versions,
        "meta_check":           meta_check,
        "cards_inspected":      cards_inspected,
        "auto_remediations":    auto_remediations,
        "human_review_flags":   human_review_flags,
        "qa_cost_estimate_usd": round(cost_cap.accumulated_usd, 4),
        "qa_budget_hit":        qa_budget_hit,
    }
