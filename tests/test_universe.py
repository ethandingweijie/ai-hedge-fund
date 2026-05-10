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
    get_watchlist_tickers,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the watchlist query at an empty tmp DB so tests don't see any
    real watchlist rows. Tests that want watchlist content INSERT into this
    DB directly."""
    db_path = tmp_path / "test_watchlist.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    yield db_path


def _seed_watchlist(tickers: list[str]) -> None:
    """Create the watchlist table in the current RUN_ARCHIVE_PATH DB and
    insert the given tickers."""
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                added_at TEXT,
                user_id INTEGER
            )
        """)
        for t in tickers:
            conn.execute(
                "INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
                (t.upper(), "2026-05-10T00:00:00Z"),
            )
        conn.commit()


# ── get_watchlist_tickers (Tier 1 source) ──────────────────────────────────


def test_watchlist_empty_when_db_empty_and_env_unset(isolated_db, monkeypatch):
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    assert get_watchlist_tickers() == set()


def test_watchlist_reads_from_db_table(isolated_db, monkeypatch):
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["CRM", "NOW", "PYPL"])
    assert get_watchlist_tickers() == {"CRM", "NOW", "PYPL"}


def test_watchlist_unions_db_and_env(isolated_db, monkeypatch):
    """ENV var contents are UNIONed with the DB results, not used as
    a fallback. Lets ops force-add tickers without UI access."""
    _seed_watchlist(["CRM", "NOW"])
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "PYPL,EXTRA")
    assert get_watchlist_tickers() == {"CRM", "NOW", "PYPL", "EXTRA"}


def test_watchlist_dedupes_overlap(isolated_db, monkeypatch):
    _seed_watchlist(["CRM", "NOW"])
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "CRM, GOOGL")
    assert get_watchlist_tickers() == {"CRM", "NOW", "GOOGL"}


def test_watchlist_normalizes_case(isolated_db, monkeypatch):
    _seed_watchlist(["crm", "Now"])
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "pypl")
    assert get_watchlist_tickers() == {"CRM", "NOW", "PYPL"}


def test_watchlist_skips_blank_rows(isolated_db, monkeypatch):
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["CRM"])
    # Manually insert a blank row to simulate bad data
    from app.backend.services.analysis_service import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO watchlist (ticker, added_at) VALUES ('', '2026-05-10')")
        conn.commit()
    # Empty ticker filtered out
    assert get_watchlist_tickers() == {"CRM"}


def test_watchlist_returns_empty_on_table_missing(tmp_path, monkeypatch):
    """If the watchlist table doesn't exist (fresh DB), return empty rather
    than raising — graceful degradation for the dispatcher."""
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db_path))
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    assert get_watchlist_tickers() == set()


def test_get_held_positions_alias_still_works(isolated_db, monkeypatch):
    """get_held_positions is a backward-compat alias for get_watchlist_tickers."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["AAPL", "MSFT"])
    assert get_held_positions() == get_watchlist_tickers() == {"AAPL", "MSFT"}


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


def test_dispatcher_universe_watchlist_only_by_default(isolated_db, monkeypatch):
    """Tier 1 only by default. Watchlist DB drives Tier 1; analyzed and
    S&P 500 stay off until their flags are set."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    monkeypatch.delenv(ENV_INCLUDE_ANALYZED, raising=False)
    monkeypatch.delenv(ENV_INCLUDE_SP500, raising=False)
    _seed_watchlist(["AAPL", "MSFT"])
    assert build_dispatcher_universe() == {"AAPL", "MSFT"}


def test_dispatcher_universe_kwarg_overrides_env(isolated_db, monkeypatch):
    """Explicit kwarg=False wins over env var = true."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    monkeypatch.setenv(ENV_INCLUDE_ANALYZED, "true")
    _seed_watchlist(["AAPL"])
    with patch("src.agents.dd.universe.get_analyzed_universe", return_value={"GOOGL"}):
        result = build_dispatcher_universe(include_analyzed=False)
    assert result == {"AAPL"}   # GOOGL not added because kwarg overrode env


def test_dispatcher_universe_unions_all_three_sources(isolated_db, monkeypatch):
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["AAPL"])
    with patch("src.agents.dd.universe.get_analyzed_universe", return_value={"GOOGL"}), \
         patch("src.agents.dd.universe.get_sp500_universe", return_value={"MSFT", "NVDA"}):
        result = build_dispatcher_universe(include_analyzed=True, include_sp500=True)
    assert result == {"AAPL", "GOOGL", "MSFT", "NVDA"}


def test_dispatcher_universe_dedupes_overlap(isolated_db, monkeypatch):
    """Tickers shared across sources should only appear once."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["AAPL", "MSFT"])
    with patch("src.agents.dd.universe.get_sp500_universe", return_value={"AAPL", "GOOGL"}):
        result = build_dispatcher_universe(include_sp500=True)
    assert result == {"AAPL", "MSFT", "GOOGL"}


def test_dispatcher_universe_truthy_env_flags(isolated_db, monkeypatch):
    """Various truthy values for the include_* env flags."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    _seed_watchlist(["AAPL"])
    for truthy in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv(ENV_INCLUDE_SP500, truthy)
        with patch("src.agents.dd.universe.get_sp500_universe", return_value={"MSFT"}):
            assert build_dispatcher_universe() == {"AAPL", "MSFT"}, f"failed for {truthy}"
