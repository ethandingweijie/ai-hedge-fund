"""Tests for src/agents/dd/universe.py — Tier-based universe builders.

Coverage:
  - PORTFOLIO_TICKERS env parsing (csv, whitespace-separated, mixed)
  - get_analyzed_universe queries web_runs with cutoff date
  - get_sp500_universe gracefully handles FMP failure
  - build_dispatcher_universe respects include_* flags + env overrides
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.dd.universe import (
    ENV_INCLUDE_ANALYZED,
    ENV_INCLUDE_SP500,
    ENV_PORTFOLIO_TICKERS,
    build_dispatcher_universe,
    get_analyzed_universe,
    get_held_positions,
    get_sp500_universe,
)


# ── get_held_positions ──────────────────────────────────────────────────────


def test_held_positions_empty_when_env_unset(monkeypatch):
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    assert get_held_positions() == set()


def test_held_positions_empty_when_env_blank(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "   ")
    assert get_held_positions() == set()


def test_held_positions_csv(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL,MSFT,NVDA")
    assert get_held_positions() == {"AAPL", "MSFT", "NVDA"}


def test_held_positions_whitespace_separated(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL MSFT NVDA")
    assert get_held_positions() == {"AAPL", "MSFT", "NVDA"}


def test_held_positions_mixed_separators_with_spaces(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "  AAPL,  MSFT NVDA,, GOOGL  ")
    assert get_held_positions() == {"AAPL", "MSFT", "NVDA", "GOOGL"}


def test_held_positions_normalizes_to_uppercase(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "aapl,msft")
    assert get_held_positions() == {"AAPL", "MSFT"}


# ── get_analyzed_universe ───────────────────────────────────────────────────


def test_analyzed_universe_returns_empty_on_db_error(monkeypatch):
    """If web_runs query throws (e.g. DB missing), return empty set, not raise."""
    with patch(
        "app.backend.services.analysis_service._connect",
        side_effect=Exception("db gone"),
    ):
        result = get_analyzed_universe(lookback_days=30)
    assert result == set()


def test_analyzed_universe_uses_explicit_lookback(tmp_path, monkeypatch):
    """When lookback_days passed explicitly, the SQL cutoff matches."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db))

    # Reload analysis_service so it picks up the new RUN_ARCHIVE_PATH env
    import importlib
    from app.backend.services import analysis_service
    importlib.reload(analysis_service)

    # Insert one fresh row + one stale row
    analysis_service._ensure_web_runs_table()
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    with analysis_service._connect() as conn:
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("fresh-1", fresh, "AAPL", "test", "{}"),
        )
        conn.execute(
            "INSERT INTO web_runs (run_id, run_at, ticker, model_name, full_result_json, is_checkpoint) "
            "VALUES (?,?,?,?,?,0)",
            ("stale-1", stale, "DEAD-TICKER", "test", "{}"),
        )
        conn.commit()

    # Reload universe so it uses the reloaded analysis_service
    from src.agents.dd import universe
    importlib.reload(universe)
    result = universe.get_analyzed_universe(lookback_days=30)
    assert "AAPL" in result
    assert "DEAD-TICKER" not in result


# ── get_sp500_universe ──────────────────────────────────────────────────────


def test_sp500_universe_empty_on_fmp_failure():
    with patch("src.tools.api._fmp_get", return_value=None):
        assert get_sp500_universe() == set()


def test_sp500_universe_empty_on_unexpected_shape():
    """If FMP returns a dict instead of a list, fail soft."""
    with patch("src.tools.api._fmp_get", return_value={"oops": "wrong shape"}):
        assert get_sp500_universe() == set()


def test_sp500_universe_extracts_symbols():
    fake_response = [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "",     "name": "Empty"},      # filtered
        {"name": "Missing symbol"},                # filtered
    ]
    with patch("src.tools.api._fmp_get", return_value=fake_response):
        assert get_sp500_universe() == {"AAPL", "MSFT"}


# ── build_dispatcher_universe ───────────────────────────────────────────────


def test_dispatcher_universe_held_only_by_default(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL,MSFT")
    monkeypatch.delenv(ENV_INCLUDE_ANALYZED, raising=False)
    monkeypatch.delenv(ENV_INCLUDE_SP500, raising=False)
    assert build_dispatcher_universe() == {"AAPL", "MSFT"}


def test_dispatcher_universe_kwarg_overrides_env(monkeypatch):
    """Explicit kwarg=False wins over env var = true."""
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL")
    monkeypatch.setenv(ENV_INCLUDE_ANALYZED, "true")
    with patch("src.agents.dd.universe.get_analyzed_universe", return_value={"GOOGL"}):
        result = build_dispatcher_universe(include_analyzed=False)
    assert result == {"AAPL"}   # GOOGL not added because kwarg overrode env


def test_dispatcher_universe_unions_all_three_sources(monkeypatch):
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL")
    with patch("src.agents.dd.universe.get_analyzed_universe", return_value={"GOOGL"}), \
         patch("src.agents.dd.universe.get_sp500_universe", return_value={"MSFT", "NVDA"}):
        result = build_dispatcher_universe(include_analyzed=True, include_sp500=True)
    assert result == {"AAPL", "GOOGL", "MSFT", "NVDA"}


def test_dispatcher_universe_dedupes_overlap(monkeypatch):
    """Tickers shared across sources should only appear once."""
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL,MSFT")
    with patch("src.agents.dd.universe.get_sp500_universe", return_value={"AAPL", "GOOGL"}):
        result = build_dispatcher_universe(include_sp500=True)
    assert result == {"AAPL", "MSFT", "GOOGL"}


def test_dispatcher_universe_truthy_env_flags(monkeypatch):
    """Various truthy values for the include_* env flags."""
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL")
    for truthy in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv(ENV_INCLUDE_SP500, truthy)
        with patch("src.agents.dd.universe.get_sp500_universe", return_value={"MSFT"}):
            assert build_dispatcher_universe() == {"AAPL", "MSFT"}, f"failed for {truthy}"
