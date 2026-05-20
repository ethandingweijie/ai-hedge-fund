"""Phase 5 integration tests for src/agents/audit/card_qa_agent.py.

Exercises the full orchestrator: Meta-Check (10.5a) → card audits (10.5b)
→ judge → reextract → audit persistence. Uses mocked Qwen responses to
drive specific verdict paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.audit.card_qa_agent import (
    _set_path,
    run_card_qa_agent,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "audit" / "fixtures"


def _load_fixture(filename: str) -> dict:
    with (FIXTURE_DIR / filename).open(encoding="utf-8") as f:
        return json.load(f)


def _scripted_client(responses: list[str]):
    """SDK client mock that returns the given responses in order.
    Each call to messages.create() consumes the next response."""
    idx = {"i": 0}

    def _create(**_kw):
        i = idx["i"]
        idx["i"] += 1
        text = responses[i] if i < len(responses) else "{}"
        return SimpleNamespace(content=[SimpleNamespace(text=text)])

    client = SimpleNamespace()
    client.messages = SimpleNamespace(create=_create)
    return client


def _always_client(text: str):
    """SDK client mock that returns the same response for every call."""
    client = SimpleNamespace()
    client.messages = SimpleNamespace(
        create=lambda **_kw: SimpleNamespace(content=[SimpleNamespace(text=text)]),
    )
    return client


# ── _set_path helper ────────────────────────────────────────────────────────


def test_set_path_creates_intermediate_dicts():
    state = {"data": {}}
    ok = _set_path(state, "data.dcf_range.{ticker}.base.intrinsic_value", "MRNA", 12.5)
    assert ok is True
    assert state["data"]["dcf_range"]["MRNA"]["base"]["intrinsic_value"] == 12.5


def test_set_path_overwrites_existing_value():
    state = {"data": {"x": {"y": "old"}}}
    ok = _set_path(state, "data.x.y", "_", "new")
    assert ok is True
    assert state["data"]["x"]["y"] == "new"


def test_set_path_fails_on_non_dict_intermediate():
    """If an intermediate node is a non-dict (e.g. list), refuse to set."""
    state = {"data": {"x": ["not", "a", "dict"]}}
    ok = _set_path(state, "data.x.y", "_", "value")
    assert ok is False


# ── Phase 5 Gate: ZTS short-circuit ─────────────────────────────────────────


def test_zts_meta_check_fails_no_card_audits_attempted():
    """Phase 5 Gate: ZTS's misclassification trips Meta-Check, which MUST
    short-circuit card audits. No LLM call should fire — the fix is at
    the upstream classification layer, not per-card.
    """
    fixture = _load_fixture("ZTS__b91aa9b4.json")
    spy = {"calls": 0}

    def _create(**_kw):
        spy["calls"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text='{"verdict": "GENUINELY_ABSENT"}')])

    client = SimpleNamespace(); client.messages = SimpleNamespace(create=_create)

    audit = run_card_qa_agent(fixture, "ZTS", sdk_client=client)
    # Meta-Check failed
    assert audit["meta_check"]["passed"] is False
    # No card audits ran
    assert audit["cards_inspected"] == []
    # Single classification flag
    assert len(audit["human_review_flags"]) == 1
    flag = audit["human_review_flags"][0]
    assert flag["reason"] == "classification_likely_wrong"
    assert flag["suggested_profile"] == "Large Cap Pharma"
    # CRUCIAL: no LLM calls fired (short-circuit worked)
    assert spy["calls"] == 0
    # No budget consumed
    assert audit["qa_cost_estimate_usd"] == 0.0


# ── Phase 5 Gate: MRNA card audit with mocked judge ────────────────────────


def test_mrna_extractor_dropped_path_writes_remediation():
    """End-to-end: MRNA's dcf_range gap is judged EXTRACTOR_DROPPED, the
    reextractor finds a value, and the orchestrator writes it back to state.
    Verifies the auto-remediation loop closes."""
    fixture = _load_fixture("MRNA__0182e126.json")
    # mutate the fixture's dcf_range to be CLEARLY empty so the audit fires
    assert fixture["data"]["dcf_range"].get("MRNA") == {}   # sanity

    judge_resp = json.dumps({
        "verdict":          "EXTRACTOR_DROPPED",
        "reasoning":        "DCF projections are present in deep_research",
        "evidence_quote":   "Pipeline rNPV estimated at $50/share",
        "evidence_offset":  100,
    })
    reextract_resp = json.dumps({
        "value":      {"base": {"intrinsic_value": 50.0}},
        "confidence": "high",
        "reasoning":  "Quote explicitly states the value",
    })
    client = _scripted_client([judge_resp, reextract_resp])

    audit = run_card_qa_agent(fixture, "MRNA", sdk_client=client)
    assert audit["meta_check"]["passed"] is True

    # The biopharma_pipeline_rnpv card should be in cards_inspected
    cards = {c["card"]: c for c in audit["cards_inspected"]}
    assert "biopharma_pipeline_rnpv" in cards
    card = cards["biopharma_pipeline_rnpv"]
    assert card["judge_verdict"] == "EXTRACTOR_DROPPED"
    assert card["remediation_attempted"] is True
    assert card["remediation_success"] is True

    # Remediation appears in auto_remediations
    assert len(audit["auto_remediations"]) == 1
    rem = audit["auto_remediations"][0]
    assert rem["card"] == "biopharma_pipeline_rnpv"
    assert rem["field"] == "data.dcf_range.MRNA"
    assert rem["method"] == "hinted_reextract"

    # State was actually mutated
    assert fixture["data"]["dcf_range"]["MRNA"] != {}


def test_mrna_genuinely_absent_path_flags_but_no_remediation():
    """When judge says GENUINELY_ABSENT, no reextract fires; the field is
    flagged for transparency but accepted as expected-empty.

    Post-Phase-7 (universal cards added): MRNA's empty dcf_range trips
    BOTH `biopharma_pipeline_rnpv` AND `dcf_range_summary` cards (same
    underlying field path). Each returns one GENUINELY_ABSENT flag.
    Pipeline_assets is populated and scenario/decisions are populated,
    so they don't fire."""
    fixture = _load_fixture("MRNA__0182e126.json")
    judge_resp = json.dumps({
        "verdict":         "GENUINELY_ABSENT",
        "reasoning":       "DCF is computed by an upstream agent, not extractable from text",
        "evidence_quote":  "",
        "evidence_offset": None,
    })
    client = _always_client(judge_resp)

    audit = run_card_qa_agent(fixture, "MRNA", sdk_client=client)
    cards = {c["card"]: c for c in audit["cards_inspected"]}
    card = cards["biopharma_pipeline_rnpv"]
    assert card["judge_verdict"] == "GENUINELY_ABSENT"
    assert card["remediation_attempted"] is False
    # Both biopharma_pipeline_rnpv AND dcf_range_summary flag the same
    # empty dcf_range path. They produce one flag each → 2 flags total.
    assert all(f["reason"] == "genuinely_absent_per_judge"
               for f in audit["human_review_flags"])
    assert {f["card"] for f in audit["human_review_flags"]} == {
        "biopharma_pipeline_rnpv", "dcf_range_summary",
    }
    # No remediation on the GENUINELY_ABSENT path
    assert audit["auto_remediations"] == []


def test_mrna_wrong_profile_path_flags_for_review():
    """Judge says WRONG_PROFILE → flag, no remediation. (Note: this is a
    second line of defense; Meta-Check should catch most wrong-profile
    cases upstream. This test verifies the per-card path still works.)

    Two universal cards (biopharma_pipeline_rnpv + dcf_range_summary)
    flag for MRNA's empty dcf_range — both get WRONG_PROFILE flags."""
    fixture = _load_fixture("MRNA__0182e126.json")
    judge_resp = json.dumps({
        "verdict":         "WRONG_PROFILE",
        "reasoning":       "MRNA shouldn't actually use this card (test override)",
        "evidence_quote":  "",
        "evidence_offset": None,
    })
    client = _always_client(judge_resp)

    audit = run_card_qa_agent(fixture, "MRNA", sdk_client=client)
    flags = audit["human_review_flags"]
    assert len(flags) >= 1
    assert all(f["reason"] == "wrong_profile_per_judge" for f in flags)
    assert audit["auto_remediations"] == []


def test_mrna_reextract_not_found_flags_judge_was_wrong():
    """Judge says EXTRACTOR_DROPPED but reextract returns NOT_FOUND.
    Result: flag the field as 'reextract_returned_not_found' and DO NOT
    mutate state. This is the protection against fabricated values.
    """
    fixture = _load_fixture("MRNA__0182e126.json")
    judge_resp = json.dumps({
        "verdict":         "EXTRACTOR_DROPPED",
        "reasoning":       "I think the data is at offset 100",
        "evidence_quote":  "Some misleading quote",
        "evidence_offset": 100,
    })
    # Reextractor correctly recognizes the evidence is misleading
    reextract_resp = json.dumps({
        "value":      "NOT_FOUND",
        "confidence": "low",
        "reasoning":  "evidence does not contain the requested value",
    })
    client = _scripted_client([judge_resp, reextract_resp])

    # snapshot state to verify NO mutation
    snapshot = json.dumps(fixture["data"].get("dcf_range", {}))
    audit = run_card_qa_agent(fixture, "MRNA", sdk_client=client)
    assert json.dumps(fixture["data"].get("dcf_range", {})) == snapshot

    flags = audit["human_review_flags"]
    assert any(f["reason"] == "reextract_returned_not_found" for f in flags)
    assert audit["auto_remediations"] == []


# ── Phase 5 Gate: healthy ticker → zero LLM cost ───────────────────────────


def test_healthy_aapl_no_llm_calls_zero_cost():
    """Phase 5 Gate: AAPL is Tech (and the fixture has dcf_range +
    scenario_analysis + decisions all populated). Universal cards APPLY
    but every mandatory path resolves to a non-empty value → no judge
    call fires. Cost stays at $0.00."""
    fixture = _load_fixture("AAPL__8a81be97.json")
    spy = {"calls": 0}

    def _create(**_kw):
        spy["calls"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text="{}")])

    client = SimpleNamespace(); client.messages = SimpleNamespace(create=_create)

    audit = run_card_qa_agent(fixture, "AAPL", sdk_client=client)
    assert audit["meta_check"]["passed"] is True
    # Universal cards apply, but ALL their mandatory paths are populated
    # (AAPL fixture has dcf_range, scenario_analysis, decisions all set).
    inspected = {c["card"] for c in audit["cards_inspected"]}
    assert inspected == {"dcf_range_summary", "scenario_analysis_card", "decisions_panel"}
    # Every card should have no missing fields
    for c in audit["cards_inspected"]:
        assert c["missing_mandatory"] == [], (
            f"Unexpected missing field on AAPL card {c['card']}: {c['missing_mandatory']}"
        )
    # No judge calls fire (clean cards short-circuit before LLM)
    assert audit["qa_cost_estimate_usd"] == 0.0
    assert spy["calls"] == 0


# ── Phase 5 Gate: budget cap exhaustion ────────────────────────────────────


def test_budget_cap_fires_and_marks_qa_budget_hit():
    """Synthetic stress test: very low budget + always-needs-judge state.
    Budget should fire and qa_budget_hit should be True. Remaining cards
    should be skipped (verified by spy counting LLM calls)."""
    # Build a state where MRNA's biopharma_pipeline_rnpv card applies
    # (sectors=Biopharma + profile=Pre-approval Biotech) and dcf_range
    # is empty so it needs a judge call.
    state = {
        "data": {
            "sectors":       {"MRNA": "Biopharma"},
            "profile_names": {"MRNA": "Pre-approval Biotech"},
            "dcf_range":     {"MRNA": {}},
            "deep_research": "irrelevant",
        }
    }
    judge_resp = json.dumps({"verdict": "GENUINELY_ABSENT", "reasoning": "ok"})
    client = _always_client(judge_resp)

    # Budget = 0.06 → effective cap = 0.06 - 0.05 (default headroom) = 0.01.
    # First call costs ~0.01 → accumulated = 0.01. Next check_headroom:
    # 0.01 < 0.01 → False, so future calls would be refused.
    audit = run_card_qa_agent(
        state, "MRNA", sdk_client=client,
        budget_usd=0.06,
    )
    # The single judge call fires (within budget), then qa_budget_hit
    # flips True for any subsequent work.
    assert audit["qa_budget_hit"] is True
    assert audit["qa_cost_estimate_usd"] > 0


# ── Audit dict shape contract ───────────────────────────────────────────────


def test_audit_dict_carries_full_persistence_schema():
    """Every audit must have ALL fields from the plan's persistence schema,
    even when empty. Phase 8 SQL aggregations depend on this shape stability."""
    audit = run_card_qa_agent({"data": {}}, "AAPL")
    expected_keys = {
        "qa_version", "qa_ran_at", "qa_model", "qa_schema_versions",
        "meta_check", "cards_inspected", "auto_remediations",
        "human_review_flags", "qa_cost_estimate_usd", "qa_budget_hit",
    }
    assert expected_keys.issubset(audit.keys())


def test_orchestrator_does_not_crash_on_empty_state():
    """Defensive: even with an empty / weird state shape, return a valid
    audit dict (Phase 6's try/except is defense-in-depth but the orchestrator
    itself shouldn't rely on it).

    Post-Phase-7: empty state still triggers the 3 universal cards
    (dcf_range_summary, scenario_analysis_card, decisions_panel). All
    paths resolve to None → judge fires; without an sdk_client and no
    DEEP_RESEARCH_API_KEY, the wrapper returns "" → judge defaults to
    GENUINELY_ABSENT. So we expect 3 cards in cards_inspected, all
    flagged."""
    audit = run_card_qa_agent({}, "XYZ")
    assert audit["qa_version"] == "v1"
    # All 3 universal cards applied
    inspected = {c["card"] for c in audit["cards_inspected"]}
    assert inspected == {"dcf_range_summary", "scenario_analysis_card", "decisions_panel"}


def test_orchestrator_does_not_crash_on_unknown_ticker():
    """A ticker we've never seen → Meta-Check passes (no override conflict),
    universal cards apply but everything's None → all flagged."""
    audit = run_card_qa_agent({"data": {}}, "FAKETICKER_999")
    assert audit["meta_check"]["passed"] is True
    # Universal cards fire (3 of them); all missing → flagged GENUINELY_ABSENT
    # by the default-on-empty-response judge path
    inspected = {c["card"] for c in audit["cards_inspected"]}
    assert inspected == {"dcf_range_summary", "scenario_analysis_card", "decisions_panel"}
    assert all(c["missing_mandatory"] for c in audit["cards_inspected"])


# ── Sanity: cost stays at zero when no remediation ──────────────────────────


def test_passed_card_no_judge_call_no_cost():
    """A card whose mandatory paths are POPULATED (no missing fields)
    should not invoke the judge — saves budget.

    Post-Phase-7: populate ALL universal-card paths too so no card
    spuriously fires the judge."""
    state = {
        "data": {
            "sectors":          {"MRNA": "Biopharma"},
            "profile_names":    {"MRNA": "Pre-approval Biotech"},
            "dcf_range":        {"MRNA": {"base": {"intrinsic_value": 42.0}}},
            "pipeline_assets":  {"MRNA": [{"name": "X", "peak_sales_usd": 1.5}]},
            "scenario_analysis":{"MRNA": {"bear": 30, "base": 50, "bull": 70}},
            "decisions":        {"MRNA": {"action": "BUY"}},
            "deep_research":    "...",
        }
    }
    spy = {"calls": 0}

    def _create(**_kw):
        spy["calls"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text="{}")])

    client = SimpleNamespace(); client.messages = SimpleNamespace(create=_create)

    audit = run_card_qa_agent(state, "MRNA", sdk_client=client)
    cards = {c["card"]: c for c in audit["cards_inspected"]}
    # All cards that apply should have clean mandatory checks
    for name, card in cards.items():
        assert card["missing_mandatory"] == [], (
            f"Card {name} unexpectedly missing fields: {card['missing_mandatory']}"
        )
        assert card["judge_verdict"] is None, (
            f"Card {name} called the judge despite clean state"
        )
    assert spy["calls"] == 0
    assert audit["qa_cost_estimate_usd"] == 0.0
