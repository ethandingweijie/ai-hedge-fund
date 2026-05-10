"""Tests for src/agents/dd/digest_agent.py — Phase 2E EOD digest agent."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.agents.dd.dd_agent import DDAgentError
from src.agents.dd.digest_agent import (
    DigestNarrative,
    gather_today_aggregates,
    get_watchlist_size,
    run_digest_agent,
    upsert_digest_row,
    upsert_synthetic_digest,
)
from src.agents.dd.digest_prompts import (
    PROMPT_DIGEST,
    build_digest_user_message,
    select_digest_prompt,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolated SQLite per test."""
    db_path = tmp_path / "digest_test.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    from src.agents.dd import alert_dedup
    alert_dedup._clear_all_alerts_for_test()
    yield


@pytest.fixture
def qwen_env(monkeypatch):
    monkeypatch.setenv("DEEP_RESEARCH_API_KEY", "sk-fake")
    monkeypatch.setenv("DEEP_RESEARCH_BASE_URL", "https://example.com/anthropic")


# ── Prompt + user message ──────────────────────────────────────────────────


def test_select_digest_prompt():
    full, pid = select_digest_prompt()
    assert PROMPT_DIGEST[:100] in full
    assert pid == "digest_eod"
    assert "OUTPUT FORMAT" in full
    assert "RESEARCH PROTOCOL" in full
    assert "narrative" in full
    assert "key_themes" in full


def test_build_user_message_quiet_day():
    """Zero breaches → 'no ±10% breaches' phrasing."""
    msg = build_digest_user_message(
        utc_date="2026-05-11",
        n_drops=0, n_pumps=0, n_clusters=0,
        drops=[], pumps=[], clusters=[],
    )
    assert "No ±10% breaches" in msg
    assert "quiet" in msg.lower()


def test_build_user_message_with_data():
    msg = build_digest_user_message(
        utc_date="2026-05-11",
        n_drops=2, n_pumps=1, n_clusters=1,
        drops=[
            {"ticker": "PEGA", "pct": -0.13, "price": 87.0},
            {"ticker": "CRM",  "pct": -0.11, "price": 200.0},
        ],
        pumps=[{"ticker": "NVDA", "pct": 0.12, "price": 920.0}],
        clusters=[
            {"sector": "Tech", "direction": "DROP", "n": 3, "median_pct": -0.12},
        ],
        watchlist_size=8,
    )
    assert "PEGA" in msg
    assert "NVDA" in msg
    assert "Tech" in msg
    assert "8 tickers" in msg


# ── DigestNarrative schema ─────────────────────────────────────────────────


def test_digest_narrative_defaults():
    n = DigestNarrative()
    assert n.narrative == ""
    assert n.key_themes == []
    assert n.macro_or_micro == "mixed"


def test_digest_narrative_round_trip():
    n = DigestNarrative(
        narrative="Today saw broad-based rotation out of high-beta tech.",
        key_themes=["Fed dovish minutes", "Semis squeezed by export rules"],
        macro_or_micro="macro",
        tomorrow_watch="NVDA earnings post-close.",
    )
    d = n.model_dump()
    assert d["macro_or_micro"] == "macro"
    assert d["key_themes"][0] == "Fed dovish minutes"


# ── gather_today_aggregates ─────────────────────────────────────────────────


def test_gather_aggregates_empty_db_returns_zeros(tmp_db):
    """Fresh DB → zeros across the board."""
    agg = gather_today_aggregates(utc_date="2026-05-11")
    assert agg["n_drops"] == 0
    assert agg["n_pumps"] == 0
    assert agg["n_clusters"] == 0


def test_gather_aggregates_includes_today_only(tmp_db):
    """Yesterday's drops aren't in today's aggregate."""
    from src.agents.dd import alert_dedup
    today = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    alert_dedup.mark_alerted(
        ticker="OLD",  direction="DROP", pct=-0.15, price=85.0,
        tier="t1", reason="first_breach", now=yesterday,
    )
    alert_dedup.mark_alerted(
        ticker="NEW",  direction="DROP", pct=-0.11, price=89.0,
        tier="t1", reason="first_breach", now=today,
    )
    agg = gather_today_aggregates(utc_date=today.date().isoformat())
    tickers = [d["ticker"] for d in agg["drops"]]
    assert "NEW" in tickers
    assert "OLD" not in tickers


def test_gather_aggregates_excludes_cluster_members(tmp_db):
    """Cluster members shouldn't double-count in individual drops."""
    from src.agents.dd import alert_dedup
    today = datetime.now(timezone.utc)
    # Solo individual alert
    alert_dedup.mark_alerted(
        ticker="SOLO", direction="DROP", pct=-0.13, price=87.0,
        tier="t1", reason="first_breach", now=today,
    )
    # Cluster member
    alert_dedup.mark_alerted(
        ticker="MEMBER", direction="DROP", pct=-0.11, price=89.0,
        tier="t1_cluster_member", reason="first_breach",
        cluster_id="tech_drop_x", now=today,
        sent_status="cluster_member",
    )
    agg = gather_today_aggregates(utc_date=today.date().isoformat())
    drop_tickers = [d["ticker"] for d in agg["drops"]]
    assert "SOLO" in drop_tickers
    assert "MEMBER" not in drop_tickers


def test_gather_aggregates_clusters_decoded_from_cluster_id(tmp_db):
    """Cluster_id 'tech_drop_2026-05-11' → sector='Tech'."""
    from src.agents.dd import alert_dedup
    today = datetime.now(timezone.utc)
    cid = "tech_drop_2026-05-11"
    for tk, pct in [("CRM", -0.11), ("NOW", -0.12), ("NET", -0.13)]:
        alert_dedup.mark_alerted(
            ticker=tk, direction="DROP", pct=pct, price=100.0,
            tier="t1", reason="first_breach",
            cluster_id=cid, sent_status="cluster_member", now=today,
        )
    agg = gather_today_aggregates(utc_date=today.date().isoformat())
    assert agg["n_clusters"] == 1
    assert agg["clusters"][0]["sector"] == "Tech"
    assert agg["clusters"][0]["n"] == 3


# ── get_watchlist_size ──────────────────────────────────────────────────────


def test_watchlist_size_zero_when_table_missing(tmp_db):
    assert get_watchlist_size() == 0


def test_watchlist_size_counts_distinct_tickers(tmp_db):
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS watchlist "
            "(id INTEGER PRIMARY KEY, ticker TEXT, added_at TEXT)"
        )
        for t in ("CRM", "NOW", "PYPL", "CRM"):  # duplicate CRM filtered out
            conn.execute(
                "INSERT INTO watchlist (ticker, added_at) VALUES (?, '2026-05-10')",
                (t,),
            )
        conn.commit()
    assert get_watchlist_size() == 3


# ── upsert (synthetic + real) ──────────────────────────────────────────────


def test_upsert_synthetic_digest_writes_row(tmp_db):
    """Synthetic fallback writes to dd_reports keyed by digest_<date>."""
    aggregates = {"n_drops": 0, "n_pumps": 0, "n_clusters": 0,
                  "drops": [], "pumps": [], "clusters": []}
    run_id = upsert_synthetic_digest(utc_date="2026-05-11", aggregates=aggregates)
    assert run_id == "digest_2026-05-11"

    from src.agents.dd import alert_dedup
    with alert_dedup._conn() as conn:
        row = conn.execute(
            "SELECT model_name, full_result_json FROM dd_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None
    assert "FALLBACK" in row[0] or "synthetic" in row[1].lower()
    payload = json.loads(row[1])
    assert "narrative" in payload
    assert "[SYNTHETIC]" in payload["narrative"]


def test_upsert_synthetic_digest_active_day(tmp_db):
    """Synthetic with non-zero counts has different copy than quiet day."""
    aggregates = {
        "n_drops": 2, "n_pumps": 1, "n_clusters": 0,
        "drops": [], "pumps": [], "clusters": [],
    }
    run_id = upsert_synthetic_digest(utc_date="2026-05-11", aggregates=aggregates)
    from src.agents.dd import alert_dedup
    with alert_dedup._conn() as conn:
        row = conn.execute("SELECT full_result_json FROM dd_reports WHERE run_id = ?",
                           (run_id,)).fetchone()
    payload = json.loads(row[0])
    assert "2 drops" in payload["narrative"]


def test_upsert_digest_row_replaces_on_rerun(tmp_db):
    """INSERT OR REPLACE — same UTC date overwrites prior row."""
    n1 = DigestNarrative(narrative="first run")
    upsert_digest_row(utc_date="2026-05-11", narrative=n1, aggregates={})
    n2 = DigestNarrative(narrative="second run")
    upsert_digest_row(utc_date="2026-05-11", narrative=n2, aggregates={})

    from src.agents.dd import alert_dedup
    with alert_dedup._conn() as conn:
        rows = conn.execute(
            "SELECT full_result_json FROM dd_reports WHERE run_id = ?",
            ("digest_2026-05-11",),
        ).fetchall()
    assert len(rows) == 1   # one row, replaced
    payload = json.loads(rows[0][0])
    assert payload["narrative"] == "second run"


# ── run_digest_agent end-to-end (mocked) ───────────────────────────────────


def _mock_qwen(json_text: str, n_search: int = 1):
    text_block = MagicMock(type="text", text=json_text, citations=[])
    blocks = [text_block]
    for i in range(n_search):
        b = MagicMock(type="server_tool_use", name="web_search", input={"query": f"q{i}"})
        del b.text
        blocks.append(b)
    return MagicMock(content=blocks, stop_reason="end_turn")


def test_run_digest_agent_happy_path(tmp_db, qwen_env):
    valid_json = (
        '{"narrative":"Today saw broad-based rotation out of high-beta tech '
        'following hawkish Fed minutes, with rate-sensitive names absorbing '
        'most of the selling. ServiceNow earnings on deck Thursday adds '
        'idiosyncratic overhang.",'
        '"key_themes":["Fed minutes hawkish","Tech rotation",'
        '"NOW earnings catalyst"],'
        '"macro_or_micro":"macro",'
        '"tomorrow_watch":"NOW earnings post-close; CPI print 8:30am ET."}'
    )
    fake_resp = _mock_qwen(valid_json, n_search=2)
    with patch("src.agents.dd.digest_agent._call_llm_with_rate_retry",
               return_value=fake_resp):
        narrative = run_digest_agent(utc_date="2026-05-11")
    assert "Fed minutes" in narrative.narrative
    assert narrative.macro_or_micro == "macro"
    assert len(narrative.key_themes) == 3


def test_run_digest_agent_raises_on_qwen_failure(tmp_db, qwen_env):
    with patch("src.agents.dd.digest_agent._call_llm_with_rate_retry",
               side_effect=Exception("DashScope 500")):
        with pytest.raises(DDAgentError, match="Qwen API call failed"):
            run_digest_agent(utc_date="2026-05-11")


def test_run_digest_agent_raises_on_empty_response(tmp_db, qwen_env):
    empty = MagicMock(content=[], stop_reason="max_tokens")
    with patch("src.agents.dd.digest_agent._call_llm_with_rate_retry",
               return_value=empty):
        with pytest.raises(DDAgentError, match="empty"):
            run_digest_agent(utc_date="2026-05-11")


def test_run_digest_agent_raises_when_env_missing(tmp_db, monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DEEP_RESEARCH_BASE_URL", raising=False)
    with pytest.raises(DDAgentError, match="DEEP_RESEARCH_API_KEY"):
        run_digest_agent(utc_date="2026-05-11")


def test_run_digest_agent_coerces_string_to_empty_list(tmp_db, qwen_env):
    """LLM returns key_themes as 'none' string → coerced to []."""
    bad_shape = (
        '{"narrative":"x","key_themes":"none","macro_or_micro":"mixed",'
        '"tomorrow_watch":"y"}'
    )
    fake_resp = _mock_qwen(bad_shape)
    with patch("src.agents.dd.digest_agent._call_llm_with_rate_retry",
               return_value=fake_resp):
        narrative = run_digest_agent(utc_date="2026-05-11")
    assert narrative.key_themes == []


def test_run_digest_agent_uses_web_search_tool(tmp_db, qwen_env):
    """Phase 2E: digest agent fires web_search per user preference."""
    valid_json = '{"narrative":"x","key_themes":[],"macro_or_micro":"mixed","tomorrow_watch":"n/a"}'
    fake_resp = _mock_qwen(valid_json)
    with patch("src.agents.dd.digest_agent._call_llm_with_rate_retry",
               return_value=fake_resp) as mock_call:
        run_digest_agent(utc_date="2026-05-11")
    tools = mock_call.call_args.kwargs["tools"]
    assert any(t.get("name") == "web_search" for t in tools)
