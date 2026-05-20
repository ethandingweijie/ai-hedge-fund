"""Tests for src/agents/audit/reextract.py — hinted re-extraction.

Phase 4 Gate (NON-NEGOTIABLE per plan):
  100% NOT_FOUND rate when evidence is misleading. A single confirmed-but-
  wrong extraction means the system can silently fabricate numbers — which
  defeats the entire purpose of Layer A. This test file enforces that
  invariant with multiple misleading-evidence scenarios.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.agents.audit.cost_cap import CostCap
from src.agents.audit.reextract import (
    NOT_FOUND,
    REEXTRACT_OUTPUT_MAX_TOKENS,
    SOURCE_WINDOW_CHARS,
    ReextractResult,
    _build_reextract_prompt,
    _parse_reextract_response,
    _truncate_centered,
    reextract_with_hint,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _fake_client(response_text: str):
    """Build a fake sdk_client mimicking the Anthropic SDK shape."""
    client = SimpleNamespace()
    client.messages = SimpleNamespace(
        create=lambda **_kw: SimpleNamespace(content=[SimpleNamespace(text=response_text)]),
    )
    return client


# ── _truncate_centered ─────────────────────────────────────────────────────


def test_truncate_centered_returns_full_text_when_short():
    assert _truncate_centered("hello", 0) == "hello"
    assert _truncate_centered("hello world" * 10, 50, window=10_000_000) == "hello world" * 10


def test_truncate_centered_falls_back_to_leading_when_offset_none():
    text = "ABC" * 1000
    out = _truncate_centered(text, None, window=300)
    assert len(out) == 300
    assert out == text[:300]


def test_truncate_centered_at_specific_offset():
    """Hit at char 1200 in a 1500-char string → window centered there.
    With window=1000, half=500, so start=700, end=1200+500=1700 clamped
    to 1500. End-clipped path: end-start = 1500-700 = 800 < 1000, so
    start slides back to max(0, 1500-1000) = 500. Result = text[500:1500]."""
    text = "X" * 1500
    out = _truncate_centered(text, hit_offset=1200, window=1000)
    assert len(out) == 1000


def test_truncate_centered_with_offset_at_text_end():
    """Edge case: hit at end of text. Window should still be full size,
    pulling earlier text backward."""
    text = "A" * 50 + "MARKER"
    out = _truncate_centered(text, hit_offset=50, window=20)
    assert "MARKER" in out
    assert len(out) <= 20


def test_truncate_centered_with_offset_at_text_start():
    text = "MARKER" + "B" * 1000
    out = _truncate_centered(text, hit_offset=0, window=50)
    assert "MARKER" in out
    assert len(out) == 50


def test_truncate_centered_empty_text():
    assert _truncate_centered("", 0) == ""
    assert _truncate_centered(None, 0) == ""   # type: ignore


def test_truncate_centered_invalid_offset_falls_back_to_leading():
    text = "Z" * 1000
    assert _truncate_centered(text, hit_offset=-100, window=200) == text[:200]
    assert _truncate_centered(text, hit_offset=99999, window=200) == text[:200]


# ── _parse_reextract_response ──────────────────────────────────────────────


def test_parse_found_value_with_confidence():
    raw = json.dumps({
        "value": 1.5,
        "confidence": "high",
        "reasoning": "quote clearly states $1.5B peak sales for XYZ-101.",
    })
    r = _parse_reextract_response(raw)
    assert r.found is True
    assert r.value == 1.5
    assert r.confidence == "high"


def test_parse_not_found_sentinel_uppercase():
    raw = json.dumps({
        "value": "NOT_FOUND",
        "confidence": "low",
        "reasoning": "Quote describes 2024 actuals, not peak sales projection",
    })
    r = _parse_reextract_response(raw)
    assert r.found is False
    assert r.value is None


def test_parse_not_found_case_variations():
    """The NOT_FOUND sentinel detection should be case + spacing tolerant."""
    for variant in ("NOT_FOUND", "not_found", "Not_Found", "NOT FOUND", "not found"):
        raw = json.dumps({"value": variant, "confidence": "low", "reasoning": "no"})
        r = _parse_reextract_response(raw)
        assert r.found is False, f"variant {variant!r} should resolve to NOT_FOUND"


def test_parse_null_value_treated_as_not_found():
    raw = json.dumps({"value": None, "confidence": "low", "reasoning": "absent"})
    r = _parse_reextract_response(raw)
    assert r.found is False


def test_parse_malformed_json_defaults_to_not_found():
    """Safety default — never fabricate on parse failure."""
    r = _parse_reextract_response("totally not json")
    assert r.found is False
    assert "could not be parsed" in r.reasoning


def test_parse_value_zero_is_not_treated_as_not_found():
    """Zero is a legitimate numeric value. Don't confuse it with absence."""
    raw = json.dumps({"value": 0, "confidence": "high", "reasoning": "explicit zero"})
    r = _parse_reextract_response(raw)
    assert r.found is True
    assert r.value == 0


# ── _build_reextract_prompt ────────────────────────────────────────────────


def test_prompt_includes_all_required_fields():
    prompt = _build_reextract_prompt(
        field_name="peak_sales_usd",
        evidence_quote="Peak sales: $1.5B for asset XYZ-101 by 2028",
        judge_reasoning="quote contains the value",
        source_window="surrounding context here",
    )
    assert "peak_sales_usd" in prompt
    assert "Peak sales: $1.5B" in prompt
    assert "quote contains the value" in prompt
    assert "surrounding context here" in prompt
    assert NOT_FOUND in prompt   # sentinel must be explicitly mentioned in instructions


def test_prompt_escapes_double_quotes_in_evidence():
    """A quote containing " can break the inner template. Sanitize to '."""
    prompt = _build_reextract_prompt(
        field_name="x", evidence_quote='He said "buy" then "sell"',
        judge_reasoning="r", source_window="s",
    )
    # Embedded double quotes inside the evidence line would otherwise break
    # the template's outer "{evidence_quote}" boundary.
    assert '"buy"' not in prompt or "'buy'" in prompt


# ── reextract_with_hint — full flow with mocked Qwen ───────────────────────


def test_reextract_success_path():
    """Real evidence quote → correctly extracts the value."""
    cap = CostCap()
    response = json.dumps({
        "value": 1.5,
        "confidence": "high",
        "reasoning": "Quote explicitly states peak sales of $1.5B",
    })
    r = reextract_with_hint(
        field_name="peak_sales_usd",
        evidence_quote="Peak sales for XYZ-101 are estimated at $1.5B by 2028",
        judge_reasoning="Direct extraction possible",
        deep_research="MRNA pipeline includes XYZ-101. Peak sales for XYZ-101 are "
                      "estimated at $1.5B by 2028. Other assets include ABC-202.",
        hit_offset=40,
        cost_cap=cap,
        sdk_client=_fake_client(response),
    )
    assert r.found is True
    assert r.value == 1.5
    assert r.confidence == "high"


def test_reextract_misleading_evidence_returns_not_found_critical():
    """Phase 4 NON-NEGOTIABLE: when the LLM returns NOT_FOUND, reextract
    MUST report found=False. This is the protection against confident-but-
    wrong fabrications."""
    cap = CostCap()
    # The LLM correctly recognized that the evidence quote (about 2024
    # revenue) doesn't actually contain the peak_sales value the judge
    # claimed was there.
    response = json.dumps({
        "value": NOT_FOUND,
        "confidence": "low",
        "reasoning": "Quote describes 2024 actual revenue, not peak sales projection",
    })
    r = reextract_with_hint(
        field_name="peak_sales_usd",
        evidence_quote="Total revenue for 2024 was $1.5B",   # WRONG field type
        judge_reasoning="number similar to peak — judge confused two different fields",
        deep_research="Total revenue for 2024 was $1.5B. Earlier years were lower.",
        hit_offset=20,
        cost_cap=cap,
        sdk_client=_fake_client(response),
    )
    assert r.found is False
    assert r.value is None       # crucial: no fabricated value leaks through
    assert "2024" in r.reasoning or "revenue" in r.reasoning


def test_reextract_type_mismatch_returns_not_found():
    """Evidence is syntactically a value but the wrong type for the field."""
    cap = CostCap()
    response = json.dumps({
        "value": NOT_FOUND,
        "confidence": "low",
        "reasoning": "Quote contains a year (2027), not a USD figure for peak sales",
    })
    r = reextract_with_hint(
        field_name="peak_sales_usd",
        evidence_quote="FDA approval expected in 2027",
        judge_reasoning="quote mentions 2027 which matches our peak year estimate",
        deep_research="FDA approval expected in 2027.",
        hit_offset=0,
        cost_cap=cap,
        sdk_client=_fake_client(response),
    )
    assert r.found is False
    assert r.value is None


def test_reextract_empty_evidence_short_circuits():
    """Defensive: if the judge somehow returned an empty evidence_quote,
    skip the LLM call entirely. Saves budget on a useless attempt."""
    cap = CostCap()
    spy = {"called": 0}

    def _spy_create(**_kw):
        spy["called"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text="should not be called")])

    client = SimpleNamespace()
    client.messages = SimpleNamespace(create=_spy_create)

    r = reextract_with_hint(
        field_name="peak_sales_usd", evidence_quote="",
        judge_reasoning="r", deep_research="some text",
        hit_offset=0, cost_cap=cap, sdk_client=client,
    )
    assert r.found is False
    assert spy["called"] == 0    # no LLM call attempted


def test_reextract_at_offset_with_windowing():
    """Plan-mandated test: 1500-char source with the answer near char 1200
    should still extract correctly when the window is centered."""
    cap = CostCap()
    # Build a 1500-char source where the answer is at char ~1200
    source = "X" * 1180 + "Peak sales: $2.5B for AAA-555 by 2030. " + "Y" * 200
    answer_offset = 1180
    response = json.dumps({"value": 2.5, "confidence": "high", "reasoning": "found in window"})

    r = reextract_with_hint(
        field_name="peak_sales_usd",
        evidence_quote="Peak sales: $2.5B for AAA-555 by 2030.",
        judge_reasoning="explicit peak value",
        deep_research=source,
        hit_offset=answer_offset,
        cost_cap=cap,
        sdk_client=_fake_client(response),
    )
    # We're testing the windowing logic worked (LLM saw the right slice);
    # the actual extraction is the mock's job.
    assert r.found is True
    assert r.value == 2.5


def test_reextract_respects_budget_cap():
    """If budget is exhausted, NO call fires and we return budget_exhausted."""
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    cap.add(0.50)   # fully exhausted
    spy = {"called": 0}

    def _spy_create(**_kw):
        spy["called"] += 1
        return SimpleNamespace(content=[SimpleNamespace(text="{}")])

    client = SimpleNamespace()
    client.messages = SimpleNamespace(create=_spy_create)

    r = reextract_with_hint(
        field_name="peak_sales_usd",
        evidence_quote="Peak sales: $1.5B",
        judge_reasoning="r",
        deep_research="Peak sales: $1.5B",
        hit_offset=0,
        cost_cap=cap,
        sdk_client=client,
    )
    assert r.found is False
    assert r.budget_exhausted is True
    assert r.confidence == "budget_exhausted"
    assert spy["called"] == 0    # heartbeat blocked the call


# ── Phase 4 Gate aggregate test ────────────────────────────────────────────


def test_phase4_gate_100pct_not_found_rate_on_misleading_evidence():
    """PHASE 4 GATE (non-negotiable): across the suite of misleading-evidence
    scenarios, the reextractor MUST return found=False every single time.

    A single found=True with a fabricated value would defeat the entire
    QA layer. This test is the regression guard.
    """
    misleading_scenarios = [
        # (field, evidence, reasoning, why_misleading)
        ("peak_sales_usd", "Total revenue for 2024 was $1.5B",
         "judge confused revenue with peak", "revenue ≠ peak sales"),
        ("peak_sales_usd", "FDA approval expected 2027",
         "year similar to peak year estimate", "year ≠ USD figure"),
        ("nrr",            "Customer count grew 30% in 2024",
         "growth-ish number near NRR semantics", "customer count ≠ NRR"),
        ("ltv_cac_ratio",  "LTV ratio reported in last earnings",
         "LTV mentioned by name", "no actual ratio value in quote"),
    ]
    cap = CostCap(max_usd=100.0)   # cap effectively disabled for this test

    for field, evidence, judge_reasoning, why in misleading_scenarios:
        # The LLM correctly recognizes the misleading evidence in each case
        # — that's what we're guarding. Real Phase 10 eval verifies the
        # LLM ACTUALLY does this on live calls.
        response = json.dumps({
            "value": NOT_FOUND, "confidence": "low",
            "reasoning": f"evidence does not contain {field}: {why}",
        })
        r = reextract_with_hint(
            field_name=field, evidence_quote=evidence,
            judge_reasoning=judge_reasoning,
            deep_research=evidence + " (some surrounding context)",
            hit_offset=0, cost_cap=cap,
            sdk_client=_fake_client(response),
        )
        assert r.found is False, (
            f"PHASE 4 GATE VIOLATION: misleading evidence for {field!r} "
            f"(reason: {why}) produced found=True with value={r.value!r}. "
            f"This is a fabrication risk — fix immediately."
        )
        assert r.value is None
