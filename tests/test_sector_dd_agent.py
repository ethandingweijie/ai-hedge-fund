"""Tests for src/agents/dd/sector_dd_agent.py — sector-cluster LLM agent.

All tests mock Anthropic SDK + sector ETF fetch so no network calls happen.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.agents.dd.dd_agent import DDAgentError
from src.agents.dd.sector_dd_agent import (
    SectorDdReport,
    run_sector_dd_agent,
)
from src.agents.dd.sector_prompts import (
    PROMPT_SECTOR_DROP_CLUSTER,
    PROMPT_SECTOR_PUMP_CLUSTER,
    select_sector_prompt,
    build_sector_user_message,
)


# ── Prompt routing ──────────────────────────────────────────────────────────


def test_select_sector_prompt_drop():
    full, pid = select_sector_prompt("DROP")
    assert PROMPT_SECTOR_DROP_CLUSTER[:200] in full
    assert pid == "sector_drop_cluster"
    assert "OUTPUT FORMAT" in full
    assert "RESEARCH PROTOCOL" in full


def test_select_sector_prompt_pump():
    full, pid = select_sector_prompt("PUMP")
    assert PROMPT_SECTOR_PUMP_CLUSTER[:200] in full
    assert pid == "sector_pump_cluster"


def test_select_sector_prompt_case_insensitive():
    full, pid = select_sector_prompt("drop")
    assert pid == "sector_drop_cluster"


# ── User message ────────────────────────────────────────────────────────────


def test_build_sector_user_message_includes_all_members():
    msg = build_sector_user_message(
        sector="Semiconductor",
        direction="DROP",
        members=[("NVDA", -0.12, 89.0), ("AMD", -0.11, 90.0), ("AVGO", -0.10, 91.0)],
        median_pct=-0.11,
    )
    assert "Semiconductor" in msg
    assert "DROP" in msg
    assert "3 tickers" in msg
    assert "NVDA" in msg and "AMD" in msg and "AVGO" in msg
    assert "decline" in msg.lower()


def test_build_sector_user_message_pump_phrasing():
    msg = build_sector_user_message(
        sector="Tech", direction="PUMP",
        members=[("CRM", 0.12, 200.0)],
        median_pct=0.12,
    )
    assert "rally" in msg.lower()


# ── SectorDdReport schema ──────────────────────────────────────────────────


def test_sector_dd_report_defaults():
    r = SectorDdReport()
    assert r.sector == ""
    assert r.direction == ""
    assert r.cluster_members == []
    assert r.news_drivers == []
    assert r.filings == []
    assert "n/a" in r.insider_signal


def test_sector_dd_report_round_trip_via_model_dump():
    r = SectorDdReport(
        sector="Tech",
        direction="DROP",
        cluster_members=["CRM", "NOW"],
        cause_summary="x",
        thesis_impact="y",
        recommended_action="HOLD",
        news_drivers=[{"title": "headline", "url": "https://u"}],
        insider_signal="quiet",
    )
    dumped = r.model_dump()
    assert dumped["sector"] == "Tech"
    assert dumped["cluster_members"] == ["CRM", "NOW"]
    assert dumped["news_drivers"][0]["title"] == "headline"


# ── run_sector_dd_agent end-to-end (mocked) ────────────────────────────────


def _mock_qwen_response(json_text: str, n_search: int = 2):
    text_block = MagicMock(type="text", text=json_text, citations=[])
    blocks = [text_block]
    for i in range(n_search):
        b = MagicMock(type="server_tool_use", name="web_search", input={"query": f"q{i}"})
        del b.text
        blocks.append(b)
    resp = MagicMock(content=blocks, stop_reason="end_turn")
    return resp


@pytest.fixture
def qwen_env(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "sk-fake")
    monkeypatch.setenv("DEEP_RESEARCH_BASE_URL", "https://example.com/anthropic")


def test_run_sector_dd_agent_happy_path(qwen_env):
    """Mocked Qwen returns clean JSON → validated SectorDdReport."""
    valid_json = (
        '{"cause_summary":"Sector-wide rate cut anticipation drove '
        'banks-up-tech-down rotation today.",'
        '"thesis_impact":"Transient flow; durable thesis intact.",'
        '"recommended_action":"HOLD high-quality names; trim crowded longs.",'
        '"news_drivers":[{"title":"Fed dovish minutes","url":"https://x","publishedDate":"2026-05-10"}],'
        '"filings":[],'
        '"insider_signal":"Concentrated buys at AAPL across last week."}'
    )
    fake_resp = _mock_qwen_response(valid_json)
    members = [("CRM", -0.11, 200.0), ("NOW", -0.12, 800.0), ("NET", -0.10, 60.0)]

    with patch("src.agents.dd.sector_dd_agent._call_llm_with_rate_retry",
               return_value=fake_resp), \
         patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context",
               return_value="XLK -1.5% today"):
        report = run_sector_dd_agent(sector="Tech", direction="DROP", members=members)

    assert report.sector == "Tech"
    assert report.direction == "DROP"
    assert report.cluster_members == ["CRM", "NOW", "NET"]
    assert "rate cut anticipation" in report.cause_summary
    assert len(report.news_drivers) == 1
    assert "Concentrated buys" in report.insider_signal


def test_run_sector_dd_agent_uses_drop_prompt(qwen_env):
    """Verify direction routes to the DROP system prompt."""
    valid_json = '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z","news_drivers":[],"filings":[],"insider_signal":"n"}'
    fake_resp = _mock_qwen_response(valid_json)
    members = [("A", -0.11, 100.0), ("B", -0.12, 100.0), ("C", -0.13, 100.0)]

    with patch("src.agents.dd.sector_dd_agent._call_llm_with_rate_retry",
               return_value=fake_resp) as mock_call, \
         patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context", return_value=None):
        run_sector_dd_agent(sector="Tech", direction="DROP", members=members)

    system = mock_call.call_args.kwargs["system"]
    assert "SECTOR DECLINE BRIEF" in system


def test_run_sector_dd_agent_uses_pump_prompt(qwen_env):
    valid_json = '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z","news_drivers":[],"filings":[],"insider_signal":"n"}'
    fake_resp = _mock_qwen_response(valid_json)
    members = [("A", 0.11, 100.0), ("B", 0.12, 100.0), ("C", 0.13, 100.0)]

    with patch("src.agents.dd.sector_dd_agent._call_llm_with_rate_retry",
               return_value=fake_resp) as mock_call, \
         patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context", return_value=None):
        run_sector_dd_agent(sector="Tech", direction="PUMP", members=members)

    system = mock_call.call_args.kwargs["system"]
    assert "SECTOR RALLY BRIEF" in system


def test_run_sector_dd_agent_raises_on_empty_members(qwen_env):
    with pytest.raises(DDAgentError, match="empty"):
        run_sector_dd_agent(sector="Tech", direction="DROP", members=[])


def test_run_sector_dd_agent_raises_on_qwen_failure(qwen_env):
    members = [("A", -0.11, 100.0), ("B", -0.12, 100.0), ("C", -0.13, 100.0)]
    with patch("src.agents.dd.sector_dd_agent._call_llm_with_rate_retry",
               side_effect=Exception("DashScope 500")), \
         patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context", return_value=None):
        with pytest.raises(DDAgentError, match="Qwen API call failed"):
            run_sector_dd_agent(sector="Tech", direction="DROP", members=members)


def test_run_sector_dd_agent_raises_when_env_missing(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DEEP_RESEARCH_BASE_URL", raising=False)
    members = [("A", -0.11, 100.0), ("B", -0.12, 100.0), ("C", -0.13, 100.0)]
    with patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context", return_value=None):
        with pytest.raises(DDAgentError, match="DEEP_RESEARCH_API_KEY"):
            run_sector_dd_agent(sector="Tech", direction="DROP", members=members)


def test_run_sector_dd_agent_backfills_cluster_metadata(qwen_env):
    """LLM didn't include sector/direction/members in its JSON → orchestrator
    backfills from input args before validation."""
    incomplete_json = (
        '{"cause_summary":"x","thesis_impact":"y","recommended_action":"z",'
        '"news_drivers":[],"filings":[],"insider_signal":"n"}'
    )
    fake_resp = _mock_qwen_response(incomplete_json)
    members = [("A", -0.11, 100.0), ("B", -0.12, 100.0), ("C", -0.13, 100.0)]

    with patch("src.agents.dd.sector_dd_agent._call_llm_with_rate_retry",
               return_value=fake_resp), \
         patch("src.agents.dd.sector_dd_agent._fetch_sector_etf_context", return_value=None):
        report = run_sector_dd_agent(sector="Banks", direction="DROP", members=members)

    assert report.sector == "Banks"
    assert report.direction == "DROP"
    assert report.cluster_members == ["A", "B", "C"]
