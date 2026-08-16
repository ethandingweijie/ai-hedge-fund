"""
tests/test_llm_retry.py
=======================
R1 reliability batch — shared LLM error classification + hardened call_llm.

Covers:
  * llm_retry.classify_llm_error / compute_backoff / retry_after_from
  * llm_retry.is_parse_error (malformed-LLM-output detection)
  * call_llm retry behaviour against a stub LLM:
      - retryable errors back off and retry until success
      - parse errors (OutputParserException) retry like transient failures
      - non-retryable errors fail fast (no attempt-burning)
      - exhausted retries still inject the synthetic default, but loudly
      - ALIBABA-provider calls go through the qwen throttle (acquire + report)
  * deep_research._call_llm_with_rate_retry keeps its proven behaviour while
    delegating classification to the shared helper (parse errors still
    propagate to its higher-level nudge/repair loops).
"""
import logging

import pytest
from pydantic import BaseModel

from src.utils import llm_retry
from src.utils import llm as llm_mod


class _Out(BaseModel):
    text: str = ""


# ── classification matrix ─────────────────────────────────────────────────────

class APITimeoutError(Exception):
    """Mimics the openai SDK class whose NAME carries the signal."""


class OutputParserException(Exception):
    """Mimics langchain's OutputParserException (matched by class name)."""


class ValidationError(Exception):
    """Mimics pydantic's ValidationError (matched by class name)."""


@pytest.mark.parametrize(
    "exc, retryable, kind",
    [
        # rate-limit family — incl. DashScope's 403-as-rate-limit quirks
        (Exception("Error code: 429 - rate limit exceeded"), True, "rate_limit"),
        (Exception("Rate limit exceeded"), True, "rate_limit"),
        (Exception("DashScope: AccessDenied — Requests throttled"), True, "rate_limit"),
        (Exception("You exceeded your current quota, please try again later"), True, "rate_limit"),
        # timeout/connection family
        (APITimeoutError("request hung past client timeout"), True, "timeout"),
        (Exception("Request timed out."), True, "timeout"),
        (Exception("Connection reset by peer"), True, "timeout"),
        # non-retryable
        (Exception("Invalid API key provided"), False, "other"),
        (Exception("Bad request: malformed JSON in messages"), False, "other"),
        (KeyError("OPENAI_API_KEY"), False, "other"),
    ],
)
def test_classify_llm_error(exc, retryable, kind):
    assert llm_retry.classify_llm_error(exc) == (retryable, kind)


# ── parse errors: separate, retryable concern ────────────────────────────────

@pytest.mark.parametrize(
    "exc",
    [
        OutputParserException("Failed to parse AdvancedInvestorSignal from '{...}'"),
        ValidationError("2 validation errors for AdvancedInvestorSignal"),
        Exception("Failed to parse model output"),
        Exception("Invalid JSON received from model"),
    ],
)
def test_is_parse_error_positive(exc):
    assert llm_retry.is_parse_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        Exception("Error code: 429 - rate limit exceeded"),
        Exception("Invalid API key provided"),
        APITimeoutError("request hung past client timeout"),
    ],
)
def test_is_parse_error_negative(exc):
    assert llm_retry.is_parse_error(exc) is False


def test_classify_stays_infrastructure_only_parse_is_separate():
    """classify_llm_error must NOT learn parse detection — deep_research
    relies on its exact semantics (parse failures propagate to its nudge
    loops). call_llm layers is_parse_error on top instead."""
    exc = OutputParserException("Failed to parse AdvancedInvestorSignal")
    assert llm_retry.classify_llm_error(exc) == (False, "other")
    assert llm_retry.is_parse_error(exc) is True


# ── backoff + Retry-After ─────────────────────────────────────────────────────

def test_backoff_curve_defaults():
    assert [llm_retry.compute_backoff(a) for a in range(6)] == \
        [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]  # capped at 30s


def test_backoff_custom_base_and_cap_match_deep_research_history():
    # deep_research's proven curve: 3, 6, 12, 24, 48 (cap 60 not yet reached)
    assert [llm_retry.compute_backoff(a, 3.0, 60.0) for a in range(5)] == \
        [3.0, 6.0, 12.0, 24.0, 48.0]


class _Resp:
    def __init__(self, headers):
        self.headers = headers


class _ExcWithResponse(Exception):
    def __init__(self, headers):
        super().__init__("upstream error")
        self.response = _Resp(headers)


def test_retry_after_extracted_from_headers():
    assert llm_retry.retry_after_from(_ExcWithResponse({"Retry-After": "12"})) == 12.0


def test_retry_after_absent_returns_none():
    assert llm_retry.retry_after_from(Exception("no response attached")) is None


def test_retry_after_garbage_returns_none():
    assert llm_retry.retry_after_from(_ExcWithResponse({"Retry-After": "soon"})) is None


# ── call_llm stub harness ─────────────────────────────────────────────────────

class _StubModel:
    """Stand-in for the LangChain structured-output wrapper."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def bind(self, **kwargs):
        return self

    def with_structured_output(self, model, method=None):
        return self

    def invoke(self, prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Info:
    def has_json_mode(self):
        return True


def _patch_model(monkeypatch, stub):
    monkeypatch.setattr(llm_mod, "get_model_info", lambda name, provider: _Info())
    monkeypatch.setattr(llm_mod, "get_model", lambda name, provider, keys: stub)


@pytest.fixture()
def sleeps(monkeypatch):
    """Record time.sleep calls instead of sleeping."""
    recorded = []
    monkeypatch.setattr(llm_mod.time, "sleep", recorded.append)
    return recorded


# ── call_llm behaviour ────────────────────────────────────────────────────────

def test_call_llm_retryable_error_succeeds_after_backoff(monkeypatch, sleeps):
    stub = _StubModel([Exception("Error code: 429 - rate limit"), _Out(text="ok")])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out)

    assert result.text == "ok"
    assert stub.calls == 2
    assert sleeps == [2.0]  # backoff before retry; no sleep after success


def test_call_llm_retry_after_header_wins_over_backoff(monkeypatch, sleeps):
    stub = _StubModel([Exception("rate limit hit"), _Out(text="ok")])
    _patch_model(monkeypatch, stub)
    monkeypatch.setattr(llm_retry, "retry_after_from", lambda exc: 20.0)

    result = llm_mod.call_llm("p", _Out)

    assert result.text == "ok"
    assert sleeps == [20.0]  # max(retry-after 20, computed backoff 2)


def test_call_llm_parse_error_retried_like_transient_failure(monkeypatch, sleeps):
    """E2E 2026-08-16 regression catch: investor_buffett on qwen3.6-plus
    raised OutputParserException; pre-R1 code retried it, the first R1 cut
    classified it non-retryable and degraded the run on attempt 1. Malformed
    output is transient — the retry usually parses."""
    stub = _StubModel([
        OutputParserException("Failed to parse AdvancedInvestorSignal from '...'"),
        _Out(text="recovered"),
    ])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out, agent_name="investor_buffett")

    assert result.text == "recovered"
    assert stub.calls == 2
    assert sleeps == [2.0]


def test_call_llm_non_retryable_error_fails_fast(monkeypatch, sleeps):
    stub = _StubModel([Exception("Invalid API key provided"), _Out(text="never")])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out)

    assert stub.calls == 1  # no attempt-burning on permanent errors
    assert result.text == "Error in analysis, using default"
    assert sleeps == []


def test_call_llm_exhaustion_injects_default_loudly(monkeypatch, sleeps, caplog):
    stub = _StubModel([Exception("Request timed out.")] * 3)
    _patch_model(monkeypatch, stub)

    with caplog.at_level(logging.WARNING):
        result = llm_mod.call_llm("p", _Out, agent_name="test_agent")

    assert stub.calls == 3
    assert result.text == "Error in analysis, using default"
    assert sleeps == [2.0, 4.0]  # no sleep after the final failed attempt
    assert any("synthetic default" in rec.message for rec in caplog.records)
    assert any("retries exhausted" in rec.message for rec in caplog.records)


def test_call_llm_default_factory_used_on_failure(monkeypatch, sleeps):
    stub = _StubModel([Exception("Bad request")])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out, default_factory=lambda: _Out(text="factory"))

    assert result.text == "factory"


def test_call_llm_alibaba_provider_goes_through_qwen_throttle(monkeypatch, sleeps):
    acquired, reported = [], []
    monkeypatch.setattr(llm_mod, "_qwen_throttle_acquire",
                        lambda: acquired.append(1))
    monkeypatch.setattr(llm_mod, "_qwen_throttle_report_429",
                        lambda exc: reported.append(exc))
    monkeypatch.setattr(llm_mod, "get_agent_model_config",
                        lambda state, agent: ("qwen3.6-plus", "ALIBABA"))

    stub = _StubModel([Exception("Error code: 429 - rate limit"), _Out(text="ok")])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out, agent_name="investor_buffett",
                              state={"metadata": {}})

    assert result.text == "ok"
    assert len(acquired) == 2   # once per attempt
    assert len(reported) == 1   # only the failed attempt reports
    assert "429" in str(reported[0])


def test_call_llm_openai_provider_skips_qwen_throttle(monkeypatch, sleeps):
    acquired = []
    monkeypatch.setattr(llm_mod, "_qwen_throttle_acquire",
                        lambda: acquired.append(1))
    stub = _StubModel([_Out(text="ok")])
    _patch_model(monkeypatch, stub)

    result = llm_mod.call_llm("p", _Out)  # default gpt-4.1/OPENAI

    assert result.text == "ok"
    assert acquired == []


# ── deep_research delegation keeps proven behaviour ───────────────────────────

class _Messages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _SDKClient:
    def __init__(self, outcomes):
        self.messages = _Messages(outcomes)


def test_deep_research_retries_rate_limit_then_succeeds(monkeypatch, sleeps):
    from src.agents.industry import deep_research as DR

    sdk = _SDKClient([Exception("Rate limit exceeded"), "payload"])
    out = DR._call_llm_with_rate_retry(sdk, extractor_name="x", max_retries=2)

    assert out == "payload"
    assert sdk.messages.calls == 2
    assert sleeps == [3.0]  # its own base_backoff=3.0, not the shared default


def test_deep_research_propagates_non_retryable(monkeypatch, sleeps):
    from src.agents.industry import deep_research as DR

    sdk = _SDKClient([Exception("Invalid API key provided")])
    with pytest.raises(Exception, match="Invalid API key"):
        DR._call_llm_with_rate_retry(sdk, extractor_name="x")

    assert sdk.messages.calls == 1
    assert sleeps == []


def test_deep_research_parse_error_still_propagates(monkeypatch, sleeps):
    """deep_research keeps its exact pre-R1 semantics: parse failures are
    NOT retried in the infra layer — they surface to the higher-level
    nudge/repair loops."""
    from src.agents.industry import deep_research as DR

    sdk = _SDKClient([OutputParserException("Failed to parse ExtractorOutput")])
    with pytest.raises(OutputParserException):
        DR._call_llm_with_rate_retry(sdk, extractor_name="x")

    assert sdk.messages.calls == 1
    assert sleeps == []


def test_deep_research_exhausts_then_raises(monkeypatch, sleeps):
    from src.agents.industry import deep_research as DR

    sdk = _SDKClient([Exception("connection reset")] * 3)
    with pytest.raises(Exception, match="connection reset"):
        DR._call_llm_with_rate_retry(sdk, extractor_name="x", max_retries=2)

    assert sdk.messages.calls == 3  # initial + 2 retries
    assert sleeps == [3.0, 6.0]
