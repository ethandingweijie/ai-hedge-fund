"""Tests for src/agents/dd/dd_agent.py — real LLM DD agent.

Coverage:
  - Prompt routing per (direction × prior × reason) tuple
  - DdReport Pydantic schema validation + coercion of partial responses
  - _parse_dd_report robust JSON extraction (preamble/postamble noise)
  - run_dd_agent end-to-end with mocked Qwen response (no real API calls)
  - Graceful degradation when web search returns nothing / model errors
  - Filings backfill from pre-fetched SEC data when LLM omits them

All tests mock the Anthropic SDK + data fetchers so no network calls happen.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.agents.dd.dd_agent import (
    DdReport,
    DDAgentError,
    NewsDriver,
    Filing,
    _parse_dd_report,
    _build_qwen_client,
    run_dd_agent,
)
from src.agents.dd.prompts import (
    select_prompt,
    build_user_message,
    PROMPT_DROP_CRISIS,
    PROMPT_PUMP_CATALYST,
    PROMPT_DROP_AFTER_PUMP,
    PROMPT_REVERSAL_RECOVERY,
    PROMPT_DROP_CONTINUATION,
    PROMPT_PUMP_EXTENSION,
    PROMPT_DROP_FRESH_DAY,
    PROMPT_PUMP_FRESH_DAY,
)
from src.agents.dd.recent_filings import RecentFiling


# ── Prompt routing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("direction,prior,reason,expected_prompt_body", [
    ("DROP", None,   "first_breach",                          PROMPT_DROP_CRISIS),
    ("DROP", "DROP", "high_water_mark(+16% from trigger)",    PROMPT_DROP_CONTINUATION),
    ("DROP", "DROP", "cooldown_expired",                      PROMPT_DROP_FRESH_DAY),
    ("DROP", "PUMP", "direction_flip_PUMP_to_DROP",           PROMPT_DROP_AFTER_PUMP),
    ("PUMP", None,   "first_breach",                          PROMPT_PUMP_CATALYST),
    ("PUMP", "PUMP", "high_water_mark(+18% from trigger)",    PROMPT_PUMP_EXTENSION),
    ("PUMP", "PUMP", "cooldown_expired",                      PROMPT_PUMP_FRESH_DAY),
    ("PUMP", "DROP", "direction_flip_DROP_to_PUMP",           PROMPT_REVERSAL_RECOVERY),
])
def test_select_prompt_routes_correctly(direction, prior, reason, expected_prompt_body):
    """All 8 (direction × prior × reason) tuples route to the correct prompt body."""
    full, _id = select_prompt(direction, prior, reason)
    # First ~500 chars of the prompt body should appear in the full prompt
    assert expected_prompt_body[:300] in full


def test_select_prompt_admin_force_override_treated_as_first_breach():
    """admin_force_override (from the admin trigger force=true path) should
    collapse to the first_breach prompt for the given direction."""
    full, _id = select_prompt("DROP", None, "admin_force_override")
    assert PROMPT_DROP_CRISIS[:300] in full


def test_select_prompt_includes_output_contract_and_research_protocol():
    full, _id = select_prompt("DROP", None, "first_breach")
    assert "OUTPUT FORMAT (STRICT)" in full
    assert "RESEARCH PROTOCOL" in full
    assert "cause_summary" in full
    assert "news_drivers" in full


def test_select_prompt_unknown_combo_falls_back_to_first_breach():
    """Defensive: an unmapped tuple shouldn't raise; it falls back to the
    matching first_breach prompt for the current direction."""
    full, prompt_id = select_prompt("PUMP", "DROP", "made_up_reason")
    # made_up_reason → falls into first_breach category, which for PUMP+prior=DROP
    # isn't in the table → fallback to PUMP first_breach.
    assert PROMPT_PUMP_CATALYST[:300] in full
    assert "fallback" in prompt_id


# ── User message assembly ───────────────────────────────────────────────────


def test_build_user_message_includes_all_data_blocks():
    msg = build_user_message(
        ticker="PEGA",
        direction="DROP",
        pct_change=-0.11,
        current_price=89.0,
        prior_direction=None,
        reason="first_breach",
        price_context_30d="30-day move: -8%",
        insider_summary="2 insider sells totaling $1M",
        recent_filings_summary="- **8-K** (2026-05-08) — https://...",
    )
    assert "PEGA" in msg
    assert "-11.0%" in msg
    assert "DROP" in msg
    assert "$89.00" in msg
    assert "first_breach" in msg
    assert "30-day move" in msg
    assert "insider sells" in msg
    assert "8-K" in msg


def test_build_user_message_omits_empty_sections():
    """Sections with no data should be quietly omitted, not included as empty."""
    msg = build_user_message(
        ticker="PEGA",
        direction="DROP",
        pct_change=-0.11,
        current_price=89.0,
        prior_direction=None,
        reason="first_breach",
        price_context_30d=None,
        insider_summary=None,
        recent_filings_summary=None,
    )
    assert "## 30-day price context" not in msg
    assert "## Insider activity" not in msg
    assert "## Recent filings" not in msg
    # But the case file header + task block must still be there
    assert "Alert case file" in msg
    assert "Your task" in msg


# ── DdReport schema ─────────────────────────────────────────────────────────


def test_dd_report_accepts_minimal_payload():
    r = DdReport(cause_summary="x", thesis_impact="y", recommended_action="z")
    assert r.cause_summary == "x"
    assert r.news_drivers == []
    assert r.filings == []
    assert r.insider_signal == "n/a"


def test_dd_report_news_driver_partial_fields_ok():
    """LLMs sometimes omit url or publishedDate — Pydantic should accept that."""
    r = DdReport(news_drivers=[
        {"title": "Headline only"},
        {"title": "With URL", "url": "https://example.com"},
    ])
    assert len(r.news_drivers) == 2
    assert r.news_drivers[0].url is None


def test_dd_report_round_trips_via_model_dump():
    """The route's _upsert_dd_web_run JSON-dumps the report — the schema
    must round-trip cleanly through model_dump()."""
    r = DdReport(
        cause_summary="cause",
        thesis_impact="impact",
        recommended_action="HOLD",
        news_drivers=[NewsDriver(title="t", url="https://u", publishedDate="2026-05-10")],
        filings=[Filing(form="8-K", filing_date="2026-05-08", url="https://f", summary="s")],
        insider_signal="quiet",
    )
    dumped = r.model_dump()
    assert dumped["cause_summary"] == "cause"
    assert dumped["news_drivers"][0]["title"] == "t"
    assert dumped["filings"][0]["form"] == "8-K"


# ── _parse_dd_report robust JSON ────────────────────────────────────────────


def test_parse_dd_report_clean_json():
    raw = '{"cause_summary":"clean","thesis_impact":"i","recommended_action":"HOLD","news_drivers":[],"filings":[],"insider_signal":"n/a"}'
    r = _parse_dd_report(raw, "PEGA")
    assert r.cause_summary == "clean"


def test_parse_dd_report_strips_markdown_fence():
    raw = '```json\n{"cause_summary":"fenced","thesis_impact":"i","recommended_action":"HOLD"}\n```'
    r = _parse_dd_report(raw, "PEGA")
    assert r.cause_summary == "fenced"


def test_parse_dd_report_strips_preamble():
    raw = (
        "Here is my analysis of PEGA:\n\n"
        '{"cause_summary":"with preamble","thesis_impact":"i","recommended_action":"HOLD"}\n\n'
        "Hope this helps!"
    )
    r = _parse_dd_report(raw, "PEGA")
    assert r.cause_summary == "with preamble"


def test_parse_dd_report_coerces_string_to_empty_list():
    """Some models return news_drivers='no relevant news' instead of []. The
    parser should coerce rather than raise."""
    raw = '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z","news_drivers":"none","filings":"none"}'
    r = _parse_dd_report(raw, "PEGA")
    assert r.news_drivers == []
    assert r.filings == []


def test_parse_dd_report_raises_on_unparseable_text():
    with pytest.raises(DDAgentError):
        _parse_dd_report("totally not json at all", "PEGA")


def test_parse_dd_report_raises_on_non_object_json():
    """If the model returns a JSON ARRAY instead of an OBJECT, raise — we
    can't recover meaningfully."""
    with pytest.raises(DDAgentError):
        _parse_dd_report("[1,2,3]", "PEGA")


# ── _build_qwen_client ──────────────────────────────────────────────────────


def test_build_qwen_client_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DEEP_RESEARCH_BASE_URL", raising=False)
    with pytest.raises(DDAgentError, match="DEEP_RESEARCH_API_KEY"):
        _build_qwen_client()


def test_build_qwen_client_uses_default_model(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "sk-fake")
    monkeypatch.setenv("DEEP_RESEARCH_BASE_URL", "https://example.com")
    monkeypatch.delenv("DD_AGENT_MODEL", raising=False)
    monkeypatch.delenv("DEEP_RESEARCH_MODEL", raising=False)
    client, model = _build_qwen_client()
    assert model == "qwen3.6-plus"


def test_build_qwen_client_respects_dd_agent_model_override(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "sk-fake")
    monkeypatch.setenv("DEEP_RESEARCH_BASE_URL", "https://example.com")
    monkeypatch.setenv("DD_AGENT_MODEL", "qwen3-max")
    _client, model = _build_qwen_client()
    assert model == "qwen3-max"


# ── run_dd_agent end-to-end (mocked LLM + data) ─────────────────────────────


def _mock_qwen_response(json_text: str, n_search_blocks: int = 2):
    """Build a fake Anthropic-SDK response object that behaves like a real
    one for our reads (.content, .stop_reason, block.type, block.text)."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json_text
    text_block.citations = []   # no citations in mock

    # n_search_blocks fake server_tool_use entries to verify we count searches
    blocks: list = []
    for i in range(n_search_blocks):
        b = MagicMock()
        b.type = "server_tool_use"
        b.name = "web_search"
        b.input = {"query": f"fake query {i}"}
        # Importantly: NO .text attribute on tool blocks
        del b.text
        blocks.append(b)
    blocks.append(text_block)

    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = "end_turn"
    return resp


@pytest.fixture
def qwen_env(monkeypatch):
    """Set the DashScope env vars + remove DD_AGENT_MODEL override."""
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "sk-fake")
    monkeypatch.setenv("DEEP_RESEARCH_BASE_URL", "https://dashscope-intl.aliyuncs.com/apps/anthropic")
    monkeypatch.delenv("DD_AGENT_MODEL", raising=False)
    monkeypatch.delenv("DEEP_RESEARCH_MODEL", raising=False)


def test_run_dd_agent_happy_path(qwen_env):
    """Real agent run — mocked Qwen returns a clean DD JSON, all data
    fetchers return None (graceful degradation tested elsewhere). Verifies
    the orchestrator threads everything together correctly."""
    valid_json = (
        '{"cause_summary":"PEGA disclosed Q1 miss + lowered FY guide.",'
        '"thesis_impact":"Material — growth thesis weakened.",'
        '"recommended_action":"TRIM 25% on bounce.",'
        '"news_drivers":[{"title":"PEGA Q1 miss","url":"https://news/x","publishedDate":"2026-05-10"}],'
        '"filings":[{"form":"8-K","filing_date":"2026-05-09","url":"https://sec/8k","summary":"Q1 results"}],'
        '"insider_signal":"CEO sold 50k shares 2026-05-09."}'
    )
    fake_resp = _mock_qwen_response(valid_json, n_search_blocks=3)

    with patch("src.agents.dd.dd_agent._fetch_price_context", return_value="30-day: -8%"), \
         patch("src.agents.dd.dd_agent._fetch_insider_summary", return_value="quiet"), \
         patch("src.agents.dd.dd_agent.get_recent_filings", return_value=[]), \
         patch("src.agents.dd.dd_agent._call_llm_with_rate_retry", return_value=fake_resp) as mock_call:
        report = run_dd_agent(
            ticker="PEGA",
            direction="DROP",
            pct_change=-0.11,
            current_price=89.0,
            prior_direction=None,
            reason="first_breach",
        )

    assert report.cause_summary.startswith("PEGA disclosed Q1 miss")
    assert report.recommended_action == "TRIM 25% on bounce."
    assert len(report.news_drivers) == 1
    assert report.news_drivers[0].url == "https://news/x"
    assert len(report.filings) == 1
    assert report.filings[0].form == "8-K"
    assert "CEO sold" in report.insider_signal
    # Verify the model + prompt were threaded through
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model"] == "qwen3.6-plus"
    assert call_kwargs["max_tokens"] == 4096
    # Tools list contains web_search
    assert any(t.get("name") == "web_search" for t in call_kwargs["tools"])


def test_run_dd_agent_backfills_filings_from_sec_when_llm_omits(qwen_env):
    """LLM returned filings=[] but we pre-fetched 2 SEC filings — those
    should backfill into the final report."""
    valid_json_no_filings = (
        '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z",'
        '"news_drivers":[],"filings":[],"insider_signal":"n/a"}'
    )
    sec_filings = [
        RecentFiling(form="8-K", filing_date="2026-05-08", url="https://sec/8k1", accession="0001-26-123"),
        RecentFiling(form="10-Q", filing_date="2026-04-25", url="https://sec/10q", accession="0001-26-100"),
    ]
    fake_resp = _mock_qwen_response(valid_json_no_filings, n_search_blocks=1)

    with patch("src.agents.dd.dd_agent._fetch_price_context", return_value=None), \
         patch("src.agents.dd.dd_agent._fetch_insider_summary", return_value=None), \
         patch("src.agents.dd.dd_agent.get_recent_filings", return_value=sec_filings), \
         patch("src.agents.dd.dd_agent._call_llm_with_rate_retry", return_value=fake_resp):
        report = run_dd_agent(
            ticker="PEGA", direction="DROP", pct_change=-0.11,
            current_price=89.0, prior_direction=None, reason="first_breach",
        )
    assert len(report.filings) == 2
    assert report.filings[0].form == "8-K"
    assert report.filings[1].form == "10-Q"


def test_run_dd_agent_raises_on_qwen_api_failure(qwen_env):
    """API failure → DDAgentError → caller falls back to synthetic."""
    with patch("src.agents.dd.dd_agent._fetch_price_context", return_value=None), \
         patch("src.agents.dd.dd_agent._fetch_insider_summary", return_value=None), \
         patch("src.agents.dd.dd_agent.get_recent_filings", return_value=[]), \
         patch("src.agents.dd.dd_agent._call_llm_with_rate_retry",
               side_effect=Exception("DashScope 500")):
        with pytest.raises(DDAgentError, match="Qwen API call failed"):
            run_dd_agent(
                ticker="PEGA", direction="DROP", pct_change=-0.11,
                current_price=89.0, prior_direction=None, reason="first_breach",
            )


def test_run_dd_agent_raises_on_empty_text_response(qwen_env):
    """Qwen returned a response but with no text blocks — raise so the
    caller can fall back to synthetic instead of writing an empty report."""
    empty_resp = MagicMock()
    empty_resp.content = []   # no blocks at all
    empty_resp.stop_reason = "max_tokens"

    with patch("src.agents.dd.dd_agent._fetch_price_context", return_value=None), \
         patch("src.agents.dd.dd_agent._fetch_insider_summary", return_value=None), \
         patch("src.agents.dd.dd_agent.get_recent_filings", return_value=[]), \
         patch("src.agents.dd.dd_agent._call_llm_with_rate_retry", return_value=empty_resp):
        with pytest.raises(DDAgentError, match="empty text"):
            run_dd_agent(
                ticker="PEGA", direction="DROP", pct_change=-0.11,
                current_price=89.0, prior_direction=None, reason="first_breach",
            )


def test_run_dd_agent_uses_reversal_prompt_for_direction_flip(qwen_env):
    """When prior_direction=DROP and current=PUMP with direction_flip reason,
    the system prompt fed to Qwen should be the REVERSAL_RECOVERY one."""
    valid_json = '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z","news_drivers":[],"filings":[],"insider_signal":"n/a"}'
    fake_resp = _mock_qwen_response(valid_json)

    with patch("src.agents.dd.dd_agent._fetch_price_context", return_value=None), \
         patch("src.agents.dd.dd_agent._fetch_insider_summary", return_value=None), \
         patch("src.agents.dd.dd_agent.get_recent_filings", return_value=[]), \
         patch("src.agents.dd.dd_agent._call_llm_with_rate_retry",
               return_value=fake_resp) as mock_call:
        run_dd_agent(
            ticker="PEGA", direction="PUMP", pct_change=0.11,
            current_price=111.0, prior_direction="DROP",
            reason="direction_flip_DROP_to_PUMP",
        )

    system_prompt = mock_call.call_args.kwargs["system"]
    assert "RECOVERY-NARRATIVE BRIEF" in system_prompt
    assert "previously trending DOWN" in system_prompt
