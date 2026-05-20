"""Tests for src/agents/audit/llm_judge.py.

Tests use mocked Qwen responses (no real network calls). The Phase 2 gate
of "run against MRNA fixture with judge enabled" is exercised separately
via a `@pytest.mark.live` integration test (gated behind RUN_LIVE_QWEN=1
to avoid burning DashScope quota in CI).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents.audit.cost_cap import CostCap
from src.agents.audit.llm_judge import (
    BUDGET_EXHAUSTED_SENTINEL,
    ESTIMATED_COST_PER_CALL_USD,
    MAX_EVIDENCE_CHARS,
    VALID_VERDICTS,
    JudgeVerdict,
    _build_judge_prompt,
    _parse_judge_response,
    _truncate_evidence,
    call_qwen_capped,
    judge_missing_field,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _fake_qwen_response(text: str) -> SimpleNamespace:
    """Build an object shaped like an Anthropic SDK response."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _fake_client(text: str):
    """Build a fake sdk_client that returns the given response text."""
    client = SimpleNamespace()
    client.messages = SimpleNamespace(create=lambda **_kw: _fake_qwen_response(text))
    return client


# ── _truncate_evidence ─────────────────────────────────────────────────────


def test_truncate_evidence_none_returns_empty_string():
    assert _truncate_evidence(None) == ""


def test_truncate_evidence_short_passes_through():
    assert _truncate_evidence("short quote") == "short quote"


def test_truncate_evidence_long_is_clipped():
    long = "X" * (MAX_EVIDENCE_CHARS + 200)
    out = _truncate_evidence(long)
    assert len(out) == MAX_EVIDENCE_CHARS


def test_truncate_evidence_at_exact_limit():
    exact = "X" * MAX_EVIDENCE_CHARS
    assert _truncate_evidence(exact) == exact


def test_truncate_evidence_coerces_non_string():
    """Defensive: judge could return a number / list. Coerce to str."""
    assert _truncate_evidence(12345) == "12345"


# ── _parse_judge_response ──────────────────────────────────────────────────


def test_parse_judge_extractor_dropped_with_evidence_offset():
    raw = json.dumps({
        "verdict": "EXTRACTOR_DROPPED",
        "reasoning": "Peak sales of $1.5B is mentioned for asset XYZ.",
        "evidence_quote": "Peak sales for XYZ are estimated at $1.5B by 2028.",
        "evidence_offset": 4823,
    })
    verdict, reasoning, quote, offset = _parse_judge_response(raw)
    assert verdict == "EXTRACTOR_DROPPED"
    assert "Peak sales" in reasoning
    assert "Peak sales for XYZ" in quote
    assert offset == 4823


def test_parse_judge_wrong_profile_no_evidence():
    raw = json.dumps({
        "verdict": "WRONG_PROFILE",
        "reasoning": "ZTS is veterinary pharma; Managed Care card does not apply.",
        "evidence_quote": "",
        "evidence_offset": None,
    })
    verdict, reasoning, quote, offset = _parse_judge_response(raw)
    assert verdict == "WRONG_PROFILE"
    assert "veterinary" in reasoning.lower()
    assert quote == ""
    assert offset is None


def test_parse_judge_genuinely_absent():
    raw = json.dumps({
        "verdict": "GENUINELY_ABSENT",
        "reasoning": "No NRR figure exists for this private-equity-backed firm.",
        "evidence_quote": "",
        "evidence_offset": None,
    })
    verdict, _, _, _ = _parse_judge_response(raw)
    assert verdict == "GENUINELY_ABSENT"


def test_parse_judge_with_qwen_preamble_postamble():
    """Qwen sometimes wraps JSON in narrative — _parse_llm_json must recover."""
    raw = (
        "Here is my classification of the missing field:\n\n"
        '{"verdict": "EXTRACTOR_DROPPED", "reasoning": "data is present", '
        '"evidence_quote": "Peak sales: $1.5B", "evidence_offset": 100}\n\n'
        "Note: figures approximate."
    )
    verdict, _, quote, _ = _parse_judge_response(raw)
    assert verdict == "EXTRACTOR_DROPPED"
    assert "Peak sales" in quote


def test_parse_judge_malformed_json_defaults_to_genuinely_absent():
    """Safer default — no remediation fires on a parse failure."""
    verdict, reasoning, quote, offset = _parse_judge_response("totally not JSON")
    assert verdict == "GENUINELY_ABSENT"
    assert "could not be parsed" in reasoning
    assert quote == ""
    assert offset is None


def test_parse_judge_unknown_verdict_defaults_safely():
    """If Qwen returns e.g. 'CONFUSED', we should not crash or auto-remediate."""
    raw = json.dumps({"verdict": "CONFUSED", "reasoning": "uncertain"})
    verdict, reasoning, _, _ = _parse_judge_response(raw)
    assert verdict == "GENUINELY_ABSENT"
    assert "unknown verdict" in reasoning.lower()


def test_parse_judge_truncates_oversized_evidence():
    raw = json.dumps({
        "verdict": "EXTRACTOR_DROPPED",
        "reasoning": "ok",
        "evidence_quote": "Y" * (MAX_EVIDENCE_CHARS + 1000),
        "evidence_offset": 0,
    })
    _, _, quote, _ = _parse_judge_response(raw)
    assert len(quote) == MAX_EVIDENCE_CHARS


def test_parse_judge_invalid_offset_becomes_none():
    raw = json.dumps({
        "verdict": "EXTRACTOR_DROPPED",
        "reasoning": "ok",
        "evidence_quote": "x",
        "evidence_offset": "not-a-number",
    })
    _, _, _, offset = _parse_judge_response(raw)
    assert offset is None


# ── _build_judge_prompt ────────────────────────────────────────────────────


def test_prompt_includes_required_fields():
    prompt = _build_judge_prompt(
        ticker="MRNA",
        card_name="biopharma_pipeline_rnpv",
        missing_field="data.dcf_range.MRNA",
        qa_prompt_hint="Pipeline rNPV needs dcf_range[ticker]",
        source_text="MRNA pipeline assets: XYZ-101 peak sales $1.5B.",
    )
    assert "MRNA" in prompt
    assert "biopharma_pipeline_rnpv" in prompt
    assert "data.dcf_range.MRNA" in prompt
    assert "Pipeline rNPV needs" in prompt
    assert "pipeline assets" in prompt
    # All three verdict options must appear so the judge has the menu
    for v in VALID_VERDICTS:
        assert v in prompt


def test_prompt_truncates_oversized_source():
    """Source text past JUDGE_MAX_SOURCE_CHARS is trimmed to control cost."""
    from src.agents.audit.llm_judge import JUDGE_MAX_SOURCE_CHARS
    huge = "Z" * (JUDGE_MAX_SOURCE_CHARS + 10_000)
    prompt = _build_judge_prompt(
        ticker="X", card_name="c", missing_field="f", qa_prompt_hint="h",
        source_text=huge,
    )
    # The visible Z's in the prompt should equal JUDGE_MAX_SOURCE_CHARS,
    # not the original (much larger) length.
    z_count = prompt.count("Z")
    assert z_count == JUDGE_MAX_SOURCE_CHARS


# ── call_qwen_capped — budget heartbeat ────────────────────────────────────


def test_qwen_capped_returns_sentinel_when_budget_exhausted():
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    cap.add(0.50)   # fully exhausted
    raw, cost = call_qwen_capped(
        prompt="anything",
        cost_cap=cap,
        sdk_client=_fake_client('{"verdict": "EXTRACTOR_DROPPED"}'),
    )
    assert raw == BUDGET_EXHAUSTED_SENTINEL
    assert cost == 0.0


def test_qwen_capped_makes_call_when_headroom_available():
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    raw, cost = call_qwen_capped(
        prompt="anything",
        cost_cap=cap,
        sdk_client=_fake_client('{"verdict": "GENUINELY_ABSENT", "reasoning": "x"}'),
    )
    assert "GENUINELY_ABSENT" in raw
    assert cost == ESTIMATED_COST_PER_CALL_USD
    assert cap.accumulated_usd == ESTIMATED_COST_PER_CALL_USD


def test_qwen_capped_heartbeat_fires_BEFORE_call_not_after():
    """Critical: if the budget is exhausted, the SDK must NOT be invoked.
    Validates the 'inside the wrapper' invariant called out in the plan."""
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    cap.add(0.50)   # exhausted
    call_count = {"n": 0}

    def _spy_create(**_kw):
        call_count["n"] += 1
        return _fake_qwen_response("should not be reached")

    client = SimpleNamespace()
    client.messages = SimpleNamespace(create=_spy_create)

    raw, cost = call_qwen_capped(prompt="x", cost_cap=cap, sdk_client=client)
    assert raw == BUDGET_EXHAUSTED_SENTINEL
    assert call_count["n"] == 0     # heartbeat fired BEFORE the SDK call


# ── judge_missing_field — public entry point ───────────────────────────────


def test_judge_returns_verdict_dataclass():
    cap = CostCap()
    response_json = json.dumps({
        "verdict": "EXTRACTOR_DROPPED",
        "reasoning": "data is in deep_research",
        "evidence_quote": "Peak sales for XYZ: $1.5B",
        "evidence_offset": 1200,
    })
    verdict = judge_missing_field(
        ticker="MRNA",
        card_name="biopharma_pipeline_rnpv",
        missing_field="data.dcf_range.MRNA",
        qa_prompt_hint="hint",
        deep_research="MRNA pipeline includes XYZ-101 with peak sales of $1.5B.",
        cost_cap=cap,
        sdk_client=_fake_client(response_json),
    )
    assert isinstance(verdict, JudgeVerdict)
    assert verdict.verdict == "EXTRACTOR_DROPPED"
    assert verdict.evidence_offset == 1200
    assert verdict.cost_usd == ESTIMATED_COST_PER_CALL_USD


def test_judge_returns_budget_sentinel_when_exhausted():
    cap = CostCap(max_usd=0.10, headroom_usd=0.02)
    cap.add(0.10)
    verdict = judge_missing_field(
        ticker="MRNA",
        card_name="biopharma_pipeline_rnpv",
        missing_field="data.dcf_range.MRNA",
        qa_prompt_hint="hint",
        deep_research="...",
        cost_cap=cap,
        sdk_client=_fake_client('{"verdict": "EXTRACTOR_DROPPED"}'),
    )
    assert verdict.verdict == BUDGET_EXHAUSTED_SENTINEL
    assert verdict.cost_usd == 0.0
    assert verdict.evidence_quote == ""


def test_judge_handles_qwen_malformed_response_gracefully():
    """Realistic: Qwen sometimes returns garbled output. Judge must not raise."""
    cap = CostCap()
    verdict = judge_missing_field(
        ticker="MRNA", card_name="x", missing_field="y", qa_prompt_hint="z",
        deep_research="...", cost_cap=cap,
        sdk_client=_fake_client("not even close to JSON"),
    )
    assert verdict.verdict == "GENUINELY_ABSENT"
    assert "could not be parsed" in verdict.reasoning


def test_judge_empty_deep_research_does_not_crash():
    cap = CostCap()
    verdict = judge_missing_field(
        ticker="X", card_name="c", missing_field="f", qa_prompt_hint="h",
        deep_research="",
        cost_cap=cap,
        sdk_client=_fake_client(json.dumps({"verdict": "GENUINELY_ABSENT", "reasoning": "no source"})),
    )
    assert verdict.verdict == "GENUINELY_ABSENT"
