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

    Post-Phase-7 expansion (31 cards): MRNA's empty dcf_range + missing
    power_law_analysis + missing value_trap_analysis + missing
    raw_financials etc. trigger the judge across multiple universal
    cards. Each returns GENUINELY_ABSENT → multiple flags. We assert the
    BEHAVIOR (all flags are genuinely_absent + no remediation) rather
    than counting exact cards."""
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
    # The canonical Phase 1 card is still in the set
    assert "biopharma_pipeline_rnpv" in cards
    card = cards["biopharma_pipeline_rnpv"]
    assert card["judge_verdict"] == "GENUINELY_ABSENT"
    assert card["remediation_attempted"] is False
    # Every flag in human_review is genuinely_absent (no other verdicts)
    assert all(f["reason"] == "genuinely_absent_per_judge"
               for f in audit["human_review_flags"])
    # biopharma_pipeline_rnpv must be among the flagged cards
    flagged_cards = {f["card"] for f in audit["human_review_flags"]}
    assert "biopharma_pipeline_rnpv" in flagged_cards
    assert "dcf_range_summary" in flagged_cards
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


def test_healthy_aapl_inspects_universal_cards():
    """Post-expansion: AAPL fixture has dcf_range/scenario/decisions
    populated BUT lacks power_law_analysis/value_trap_analysis/
    raw_financials/etc. in the fixture (older runs). All 9 universal
    cards apply; some flag missing fields. Test verifies the orchestration
    works without crashing — exact pass/flag counts depend on fixture
    vintage."""
    fixture = _load_fixture("AAPL__8a81be97.json")
    spy = {"calls": 0}

    def _create(**_kw):
        spy["calls"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text='{"verdict":"GENUINELY_ABSENT","reasoning":"absent"}')])

    client = SimpleNamespace(); client.messages = SimpleNamespace(create=_create)

    audit = run_card_qa_agent(fixture, "AAPL", sdk_client=client)
    assert audit["meta_check"]["passed"] is True
    inspected = {c["card"] for c in audit["cards_inspected"]}
    # Universal cards apply
    assert "dcf_range_summary" in inspected
    assert "scenario_analysis_card" in inspected
    assert "decisions_panel" in inspected
    # The 3 cards that DO have populated AAPL paths should pass cleanly
    for c in audit["cards_inspected"]:
        if c["card"] in {"dcf_range_summary", "scenario_analysis_card", "decisions_panel"}:
            assert c["missing_mandatory"] == [], (
                f"Card {c['card']} unexpectedly flagged: {c['missing_mandatory']}"
            )


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


_EXPECTED_UNIVERSALS = {
    "dcf_range_summary", "scenario_analysis_card", "decisions_panel",
    "power_law_card", "value_trap_card", "agent_signals_card",
    "industry_intelligence_brief_card", "citation_registry_card",
    "financial_statements_card",
}


def test_orchestrator_does_not_crash_on_empty_state():
    """Defensive: even with an empty / weird state shape, return a valid
    audit dict.

    Post-expansion: empty state triggers all 9 universal cards. All
    mandatory paths resolve to None → flagged. The judge default on
    no-API-key returns GENUINELY_ABSENT for each."""
    audit = run_card_qa_agent({}, "XYZ")
    assert audit["qa_version"] == "v1"
    inspected = {c["card"] for c in audit["cards_inspected"]}
    assert inspected == _EXPECTED_UNIVERSALS


def test_orchestrator_does_not_crash_on_unknown_ticker():
    """A ticker we've never seen → Meta-Check passes (no override conflict),
    universal cards apply but everything's None → all flagged."""
    audit = run_card_qa_agent({"data": {}}, "FAKETICKER_999")
    assert audit["meta_check"]["passed"] is True
    inspected = {c["card"] for c in audit["cards_inspected"]}
    assert inspected == _EXPECTED_UNIVERSALS
    assert all(c["missing_mandatory"] for c in audit["cards_inspected"])


# ── Sanity: cost stays at zero when no remediation ──────────────────────────


def test_passed_card_no_judge_call_no_cost():
    """A card whose mandatory paths are POPULATED (no missing fields)
    should not invoke the judge — saves budget.

    Post-expansion: populate ALL 31 cards' mandatory paths so no card
    spuriously fires the judge."""
    state = {
        "data": {
            "sectors":             {"MRNA": "Biopharma"},
            "profile_names":       {"MRNA": "Pre-approval Biotech"},
            "dcf_range":           {"MRNA": {"base": {"intrinsic_value": 42.0}}},
            "pipeline_assets":     {"MRNA": [{"name": "X", "peak_sales_usd": 1.5}]},
            "scenario_analysis":   {"MRNA": {"bear": 30, "base": 50, "bull": 70}},
            "decisions":           {"MRNA": {"action": "BUY"}},
            "power_law_analysis":  {"MRNA": {"score": 7.5}},
            "value_trap_analysis": {"MRNA": {"score": 2}},
            "analyst_signals":     {"buffett": {"MRNA": {"signal": "BUY"}}},
            "industry_brief":      "Substantial industry brief text...",
            "citation_registry":   [{"ref_id": 1, "url": "..."}],
            "raw_financials":      {"MRNA": {"FY2024": {"revenue": 1e9}}},
            "deep_research_sections": {"2a": "...", "2c": "...", "2d": "..."},
            "deep_research":       "...",
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


# ── Phase 4: value-sanity bounds ────────────────────────────────────────────


import src.data.sector_kpi_framework as _skf  # noqa: E402
from src.agents.audit.card_qa_agent import (  # noqa: E402
    _per_row_completeness_flags,
    _value_sanity_flags,
)
from src.agents.audit.card_schemas import CARD_SCHEMAS  # noqa: E402


def _patch_card(monkeypatch, kpis: list[dict]) -> None:
    """Make render_card_payload return a synthetic single-group card so the
    band logic can be tested without coupling to a specific real profile."""
    payload = {"groups": [{"title": "Profitability", "kpis": kpis}]}
    monkeypatch.setattr(_skf, "render_card_payload", lambda *a, **k: payload)


def _state_with_profile(ticker: str = "FRSH", profile: str = "Growth SaaS") -> dict:
    return {"data": {"profile_names": {ticker: profile}}}


def test_value_sanity_flags_insane_pct(monkeypatch):
    """A pct (0–1 contract) value of 45.7 would render 4570% — the FRSH bug.
    It is PRESENT, so the empty-field walk passes it; value-sanity must flag it."""
    _patch_card(monkeypatch, [
        {"key": "profitability_pct", "value": 45.7, "format": "pct"},  # 4570% — insane
        {"key": "gross_margin_pct",  "value": 0.62, "format": "pct"},  # 62% — fine
    ])
    flags = _value_sanity_flags(_state_with_profile(), "FRSH")
    fields = {f["field"] for f in flags}
    assert "profitability_pct" in fields
    assert "gross_margin_pct" not in fields
    assert all(f["reason"] == "value_out_of_sane_range" for f in flags)


def test_value_sanity_flags_bps_band(monkeypatch):
    """take_rate_bps=166 is fine in bps; a 300k-bps value is out of band."""
    _patch_card(monkeypatch, [
        {"key": "take_rate_bps",      "value": 166.0,     "format": "bps"},  # fine
        {"key": "blown_rate_bps",     "value": 300_000.0, "format": "bps"},  # insane
    ])
    flags = _value_sanity_flags(_state_with_profile(), "FRSH")
    fields = {f["field"] for f in flags}
    assert fields == {"blown_rate_bps"}


def test_value_sanity_flags_multiple_band(monkeypatch):
    """net_debt_to_ebitda=-0.43 is fine (×); an 800× multiple is the ltv_cac bug."""
    _patch_card(monkeypatch, [
        {"key": "net_debt_to_ebitda", "value": -0.4326, "format": "x"},  # fine
        {"key": "ltv_cac_ratio",      "value": 800.0,   "format": "x"},  # insane
    ])
    flags = _value_sanity_flags(_state_with_profile(), "FRSH")
    fields = {f["field"] for f in flags}
    assert fields == {"ltv_cac_ratio"}


def test_value_sanity_ignores_unbounded_formats_and_nonnumerics(monkeypatch):
    """usd / int / string have no universal ceiling; None / NaN / bool are skipped."""
    _patch_card(monkeypatch, [
        {"key": "peak_sales_usd",    "value": 5.0e10, "format": "usd"},     # huge but valid $
        {"key": "headcount",         "value": 999999, "format": "int"},     # huge but valid count
        {"key": "next_catalyst",     "value": "2027", "format": "string"},  # textual
        {"key": "missing_kpi",       "value": None,   "format": "pct"},     # absent
        {"key": "flag_kpi",          "value": True,   "format": "x"},       # bool, not a real multiple
    ])
    assert _value_sanity_flags(_state_with_profile(), "FRSH") == []


def test_value_sanity_no_profile_returns_empty(monkeypatch):
    """No profile_name → no card to render → no flags (don't even import skf)."""
    assert _value_sanity_flags({"data": {}}, "FRSH") == []


# ── Phase 4: per-row list completeness ──────────────────────────────────────


_PIPELINE_SCHEMA = CARD_SCHEMAS["biopharma_pipeline_table"]


def test_per_row_completeness_flags_missing_peak():
    """The MRNA case: list non-empty (mandatory passes) but one row missing
    peak_sales_usd → renders '—' for that row's Peak cell. Must be flagged."""
    state = {"data": {"pipeline_assets": {"MRNA": [
        {"name": "mRNA-1010", "phase": "ph3", "peak_sales_usd": 2.0e9},
        {"name": "mRNA-1345", "phase": "ph3"},  # missing peak_sales_usd
    ]}}}
    flags = _per_row_completeness_flags(state, "MRNA", "biopharma_pipeline_table", _PIPELINE_SCHEMA)
    assert len(flags) == 1
    assert flags[0]["reason"] == "pipeline_row_missing_field"
    assert flags[0]["field"].endswith("[1].peak_sales_usd")


def test_per_row_completeness_flags_all_complete():
    state = {"data": {"pipeline_assets": {"MRNA": [
        {"name": "X", "phase": "ph3", "peak_sales_usd": 1.0e9},
    ]}}}
    assert _per_row_completeness_flags(
        state, "MRNA", "biopharma_pipeline_table", _PIPELINE_SCHEMA) == []


def test_per_row_completeness_empty_list_is_not_our_job():
    """An empty list is the mandatory-path walk's responsibility, not the
    per-row walk's — return [] so we don't double-flag."""
    state = {"data": {"pipeline_assets": {"MRNA": []}}}
    assert _per_row_completeness_flags(
        state, "MRNA", "biopharma_pipeline_table", _PIPELINE_SCHEMA) == []


def test_per_row_completeness_flags_non_dict_row():
    state = {"data": {"pipeline_assets": {"MRNA": ["not-a-dict"]}}}
    flags = _per_row_completeness_flags(state, "MRNA", "biopharma_pipeline_table", _PIPELINE_SCHEMA)
    assert len(flags) == 1
    assert flags[0]["reason"] == "pipeline_row_not_a_dict"


def test_per_row_completeness_no_contract_returns_empty():
    """A card without row_path/row_required_keys is a no-op for this walk."""
    schema = CARD_SCHEMAS["biopharma_pipeline_rnpv"]  # has no row contract
    state = {"data": {"pipeline_assets": {"MRNA": [{"name": "X"}]}}}
    assert _per_row_completeness_flags(
        state, "MRNA", "biopharma_pipeline_rnpv", schema) == []
