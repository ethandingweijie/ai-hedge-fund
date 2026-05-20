"""Phase 1 skeleton tests for src/agents/audit/card_qa_agent.py.

Verifies the deterministic-walk path against the Phase 0 fixtures saved
under tests/audit/fixtures/. No LLM mocks needed — Phase 1 makes no LLM
calls. Phase 2 will add tests/test_llm_judge.py with proper mocking.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.audit.card_qa_agent import (
    QA_VERSION,
    _get_path,
    _is_empty_value,
    run_card_qa_agent,
)
from src.agents.audit.card_schemas import CARD_SCHEMAS

FIXTURE_DIR = Path(__file__).resolve().parent / "audit" / "fixtures"


def _load_fixture(filename: str) -> dict:
    """Load a Phase 0 fixture by filename (e.g. 'MRNA__0182e126.json')."""
    with (FIXTURE_DIR / filename).open(encoding="utf-8") as f:
        return json.load(f)


# ── _get_path ───────────────────────────────────────────────────────────────


def test_get_path_resolves_nested_value():
    state = {"data": {"sectors": {"MRNA": "Biopharma"}}}
    assert _get_path(state, "data.sectors.{ticker}", "MRNA") == "Biopharma"


def test_get_path_returns_none_on_missing_segment():
    state = {"data": {}}
    assert _get_path(state, "data.sectors.{ticker}", "MRNA") is None


def test_get_path_returns_none_on_missing_leaf():
    state = {"data": {"sectors": {}}}
    assert _get_path(state, "data.sectors.{ticker}", "MRNA") is None


def test_get_path_handles_non_dict_intermediate():
    """If an intermediate node is e.g. a list, we shouldn't crash."""
    state = {"data": {"sectors": "Biopharma"}}   # str, not dict
    assert _get_path(state, "data.sectors.{ticker}", "MRNA") is None


def test_get_path_with_no_ticker_placeholder():
    """Paths without {ticker} should still work."""
    state = {"data": {"summary": "ok"}}
    assert _get_path(state, "data.summary", "AAPL") == "ok"


# ── _is_empty_value ─────────────────────────────────────────────────────────


def test_is_empty_value_true_cases():
    assert _is_empty_value(None)
    assert _is_empty_value({})
    assert _is_empty_value([])
    assert _is_empty_value("")


def test_is_empty_value_false_cases():
    """Zero and False are LEGITIMATE values, not 'missing'."""
    assert not _is_empty_value(0)
    assert not _is_empty_value(0.0)
    assert not _is_empty_value(False)
    assert not _is_empty_value({"a": 1})
    assert not _is_empty_value(["x"])
    assert not _is_empty_value("anything")


# ── Audit dict metadata ─────────────────────────────────────────────────────


def test_audit_dict_carries_phase1_metadata():
    """Phase 8 SQL aggregations depend on qa_ran_at + qa_model +
    qa_schema_versions being present from day 1. This guards against
    a regression where someone removes them as 'unused' in Phase 1."""
    audit = run_card_qa_agent({"data": {}}, "AAPL")

    assert audit["qa_version"] == QA_VERSION
    assert audit["qa_model"]                       # non-empty string
    assert "qa_ran_at" in audit
    assert audit["qa_ran_at"].endswith("+00:00")   # UTC ISO
    assert audit["qa_schema_versions"]             # at least one card


def test_audit_dict_has_all_placeholder_fields():
    """Future-phase fields must exist as placeholders so consumers (e.g.
    persistence layer) don't crash when they don't find them."""
    audit = run_card_qa_agent({"data": {}}, "AAPL")
    for k in (
        "meta_check", "cards_inspected", "auto_remediations",
        "human_review_flags", "qa_cost_estimate_usd", "qa_budget_hit",
    ):
        assert k in audit, f"missing placeholder field: {k}"


def test_qa_schema_versions_mirrors_card_definitions():
    audit = run_card_qa_agent({"data": {}}, "AAPL")
    for card_name, schema in CARD_SCHEMAS.items():
        assert audit["qa_schema_versions"][card_name] == schema.schema_version


# ── Fixture-driven behavioural tests ───────────────────────────────────────


def test_mrna_fixture_flags_dcf_range_empty():
    """The canonical broken MRNA fixture must trigger biopharma_pipeline_rnpv
    with dcf_range.MRNA in missing_mandatory."""
    fixture = _load_fixture("MRNA__0182e126.json")
    audit = run_card_qa_agent(fixture, "MRNA")

    cards = {c["card"]: c for c in audit["cards_inspected"]}
    assert "biopharma_pipeline_rnpv" in cards
    card = cards["biopharma_pipeline_rnpv"]
    assert card["applies_when_passed"] is True
    assert "data.dcf_range.MRNA" in card["missing_mandatory"]


def test_mrna_historical_fixture_also_flags():
    """The 2nd MRNA fixture (historical) should reproduce the same bug.
    Phase 8 aggregations rely on this being deterministic — two MRNA runs
    with the same failure → one cluster, not two."""
    fixture = _load_fixture("MRNA__70b7d8b1.json")
    audit = run_card_qa_agent(fixture, "MRNA")
    cards = {c["card"]: c for c in audit["cards_inspected"]}
    assert "biopharma_pipeline_rnpv" in cards
    assert "data.dcf_range.MRNA" in cards["biopharma_pipeline_rnpv"]["missing_mandatory"]


def test_zts_fixture_does_not_apply_biopharma_card():
    """ZTS's persisted classification is HealthcareServices/Managed Care.
    The biopharma predicate honours the current (wrong) classification —
    catching the misclassification itself is Meta-Check's job in Phase 3.
    """
    fixture = _load_fixture("ZTS__b91aa9b4.json")
    audit = run_card_qa_agent(fixture, "ZTS")
    card_names = {c["card"] for c in audit["cards_inspected"]}
    assert "biopharma_pipeline_rnpv" not in card_names


@pytest.mark.parametrize("fixture_file,ticker", [
    ("AAPL__8a81be97.json", "AAPL"),
    ("MSFT__f616514d.json", "MSFT"),
    ("JPM__f58865fb.json",  "JPM"),
    ("DLR__e4ecbe13.json",  "DLR"),
    ("NVO__3a5d11f5.json",  "NVO"),
    ("INTU__869c6dfe.json", "INTU"),
    ("MOH__cebfa77e.json",  "MOH"),
])
def test_biopharma_card_silent_on_non_biopharma_fixtures(fixture_file, ticker):
    """Phase 1 GATE: biopharma_pipeline_rnpv must produce zero output on
    any non-pre-approval-biotech fixture. False positives at this stage
    would taint Phase 10's eval metrics."""
    fixture = _load_fixture(fixture_file)
    audit = run_card_qa_agent(fixture, ticker)
    card_names = {c["card"] for c in audit["cards_inspected"]}
    assert "biopharma_pipeline_rnpv" not in card_names, (
        f"False positive: biopharma_pipeline_rnpv fired on {fixture_file}"
    )


def test_run_does_not_mutate_input_state():
    """The QA agent must be a pure read of state. Mutating state here
    would risk corrupting downstream persistence (the same dict gets
    saved to web_runs after Phase 10.5 returns).
    """
    fixture = _load_fixture("MRNA__0182e126.json")
    snapshot = json.dumps(fixture, sort_keys=True)
    run_card_qa_agent(fixture, "MRNA")
    assert json.dumps(fixture, sort_keys=True) == snapshot
