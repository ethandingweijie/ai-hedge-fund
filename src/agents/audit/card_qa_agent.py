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


# ── Phase 4: value-sanity bounds ────────────────────────────────────────────
#
# Plausibility ceilings per display `format`, expressed in the SAME unit the
# value is stored in (see _FORMAT_META in sector_kpi_framework). These catch
# PRESENT-BUT-WRONG values — unit errors, format-tag drift, garbage extractions
# — that the empty-field walk is structurally blind to (a populated field
# passes it regardless of magnitude). This is the safety net for the
# 16600%-take-rate / 4570%-margin class.
#
# Bands are deliberately GENEROUS: a flag means "a human should glance at this",
# not "this is definitely broken". Formats absent from this map (usd, usd_b,
# int, string) have no defensible universal ceiling and are skipped.
_SANE_ABS_MAX: dict[str, float] = {
    "pct":    3.0,        # 0–1 contract, ×100 on render → 3.0 == 300%
    "pct100": 300.0,      # already 0–100 on the wire
    "bps":    200_000.0,  # 200k bps == 2000%
    "x":      100.0,      # a 100× multiple is already extreme
}


def _value_sanity_flags(state: dict, ticker: str) -> list[dict]:
    """Walk the ticker's rendered KPI card and flag values whose magnitude is
    implausible for their declared format. Advisory only — emits
    human_review_flags, never a hard fail. Returns [] when there's no card to
    render (no profile, legacy profile, unresolved profile).

    Drives off the explicit `format` Phase 1 added, so the check knows the
    intended unit. Would have flagged PYPL (take_rate 16600%), FRSH
    (profitability 4570%), and rule-of-40 (4500%)."""
    flags: list[dict] = []
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    profile_name = ((data.get("profile_names") or {}).get(ticker) or "")
    if not profile_name:
        return flags
    try:
        # Lazy import: keeps the heavy sector framework off the audit module's
        # import path and avoids any circular-import risk at load time.
        from src.data.sector_kpi_framework import render_card_payload
        payload = render_card_payload(profile_name, state, ticker)
    except Exception as exc:
        logger.warning(
            "card_qa_agent[%s]: value-sanity render_card_payload failed: %s",
            ticker, exc,
        )
        return flags
    if not isinstance(payload, dict):
        return flags
    for group in payload.get("groups") or []:
        for kpi in (group.get("kpis") or []):
            value = kpi.get("value")
            # Reject non-numerics and bools (bool is an int subclass).
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value != value:  # NaN
                continue
            limit = _SANE_ABS_MAX.get(kpi.get("format"))
            if limit is None:
                continue
            if abs(value) > limit:
                flags.append({
                    "card":           "sector_valuation_card",
                    "field":          kpi.get("key"),
                    "reason":         "value_out_of_sane_range",
                    "context": (
                        f"{kpi.get('key')}={value} exceeds the plausible range "
                        f"for format {kpi.get('format')!r} (|value| > {limit}); "
                        f"likely a unit / format error."
                    ),
                    "evidence_quote": "",
                })
    return flags


def _per_row_completeness_flags(
    state: dict, ticker: str, card_name: str, schema: CardSchema,
) -> list[dict]:
    """For a card whose mandatory data is a LIST of row dicts, flag each row
    missing a required sub-field. Closes the MRNA blind spot: a non-empty
    pipeline_assets list passes the empty-field walk even though every row is
    missing peak_sales_usd. Flag-and-report only (no auto-fix — _set_path can't
    write into list elements). Returns [] for cards without a row contract or
    when the list itself is empty (that's the mandatory-path walk's job)."""
    flags: list[dict] = []
    if not schema.row_path or not schema.row_required_keys:
        return flags
    rows = _get_path(state, schema.row_path, ticker)
    if not isinstance(rows, list) or not rows:
        return flags
    base = schema.row_path.replace("{ticker}", ticker)
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            flags.append({
                "card":           card_name,
                "field":          f"{base}[{idx}]",
                "reason":         "pipeline_row_not_a_dict",
                "context":        f"row {idx} is {type(row).__name__}, expected dict",
                "evidence_quote": "",
            })
            continue
        for key in schema.row_required_keys:
            if _is_empty_value(row.get(key)):
                row_label = row.get("name") or f"row[{idx}]"
                flags.append({
                    "card":           card_name,
                    "field":          f"{base}[{idx}].{key}",
                    "reason":         "pipeline_row_missing_field",
                    "context": (
                        f"pipeline asset {row_label!r} (row {idx}) is missing "
                        f"required field {key!r}"
                    ),
                    "evidence_quote": "",
                })
    return flags


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

    # Phase 4: per-row completeness. Runs REGARDLESS of the mandatory-walk
    # result — the list can be non-empty (so the mandatory path passes) yet
    # individual rows can be missing required sub-fields (the MRNA case).
    flags.extend(_per_row_completeness_flags(state, ticker, card_name, schema))

    if not missing_mandatory:
        # Clean (mandatory-wise) — no judge call needed. Saves budget.
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
            # Card audits are short-circuited when Meta-Check fails, so the
            # Phase 4 sweeps never run on a suspect classification → 0/0.
            "kpis_out_of_sane_range": 0,
            "rows_missing_subfields": 0,
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

    # Phase 4: value-sanity sweep — once per ticker over the rendered sector
    # card (not per CardSchema). Advisory flags; the empty-field walk can't
    # see these present-but-wrong magnitudes.
    human_review_flags.extend(_value_sanity_flags(state, ticker))

    # Phase 4 observability counters — surface "N out-of-range / N rows missing
    # sub-fields" per run so regressions are visible without re-opening cards.
    kpis_out_of_sane_range = sum(
        1 for f in human_review_flags if f.get("reason") == "value_out_of_sane_range"
    )
    rows_missing_subfields = sum(
        1 for f in human_review_flags
        if f.get("reason") in ("pipeline_row_missing_field", "pipeline_row_not_a_dict")
    )
    if kpis_out_of_sane_range or rows_missing_subfields:
        logger.warning(
            "card_qa_agent[%s]: %d KPI(s) out-of-sane-range, %d pipeline row(s) "
            "missing sub-fields", ticker, kpis_out_of_sane_range, rows_missing_subfields,
        )

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
        "kpis_out_of_sane_range": kpis_out_of_sane_range,
        "rows_missing_subfields": rows_missing_subfields,
        "qa_cost_estimate_usd": round(cost_cap.accumulated_usd, 4),
        "qa_budget_hit":        qa_budget_hit,
    }
