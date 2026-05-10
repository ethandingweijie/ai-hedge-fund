"""Tests for src/agents/dd/cron_dispatcher.py — Phase 2B auto-fire entry point.

All tests mock the underlying universe builder + FMP batch quote +
HTTP POST so no real network calls happen.
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from src.agents.dd.batch_quote import BatchQuote
from src.agents.dd.cron_dispatcher import (
    ENV_ADMIN_SECRET,
    ENV_BASE_URL,
    ENV_DRY_RUN,
    ENV_MAX_ALERTS_TICK,
    ENV_THRESHOLD_PCT,
    main,
)
from src.agents.dd.scheduler import (
    ENV_FORCE_RUN,
    ENV_SKIP_MARKET_GATE,
    DispatchDecision,
)
from src.agents.dd.universe import ENV_PORTFOLIO_TICKERS


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def basic_env(monkeypatch):
    """Set the minimum env required for main() to run.

    - Stubs out the daily cleanup (covered by its own tests below) so
      dispatch tests count POSTs to /admin/dd-trigger only.
    - Stubs out the Tier 1 HTTP fetch (covered by its own tests) so
      tests don't wait for fake HTTP calls to time out.
    - Falls Tier 1 back to PORTFOLIO_TICKERS=AAPL,MSFT for the universe.
    """
    monkeypatch.setenv(ENV_BASE_URL,     "http://test-host:1234")
    monkeypatch.setenv(ENV_ADMIN_SECRET, "test-secret")
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "AAPL,MSFT")
    # Force-dispatch so test isn't time-of-day dependent
    monkeypatch.setenv(ENV_FORCE_RUN, "true")
    monkeypatch.setattr(
        "src.agents.dd.cron_dispatcher._maybe_run_daily_cleanup",
        lambda **_kw: None,
    )
    # Stub the Tier 1 HTTP fetch to use the env-var path directly. Real HTTP
    # behaviour is covered by test_fetch_tier1_via_http_*.
    monkeypatch.setattr(
        "src.agents.dd.cron_dispatcher._fetch_tier1_via_http",
        lambda *, base_url: {"AAPL", "MSFT"},
    )
    yield


def _q(ticker: str, pct: float, price: float = 100.0) -> BatchQuote:
    return BatchQuote(ticker=ticker, price=price, changes_percentage=pct, raw={})


# ── Config validation ──────────────────────────────────────────────────────


def test_main_fails_when_base_url_missing(monkeypatch):
    monkeypatch.delenv(ENV_BASE_URL, raising=False)
    monkeypatch.setenv(ENV_ADMIN_SECRET, "x")
    assert main() == 1


def test_main_fails_when_secret_missing(monkeypatch):
    monkeypatch.setenv(ENV_BASE_URL, "http://x")
    monkeypatch.delenv(ENV_ADMIN_SECRET, raising=False)
    assert main() == 1


# ── Gate behavior ──────────────────────────────────────────────────────────


def test_main_returns_0_when_gate_blocks(monkeypatch, basic_env, capsys):
    """When scheduler says don't run, main() returns 0 + emits a summary
    with decision=skipped reason (and no FMP / POST calls happen)."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.setenv(ENV_SKIP_MARKET_GATE, "true")
    # Saturday 18:00 UTC → weekend gate blocks
    fixed = MagicMock(should_run=False, reason="weekend (Saturday)")
    with patch("src.agents.dd.cron_dispatcher.scheduler.should_run", return_value=fixed):
        rc = main()
    assert rc == 0
    captured = capsys.readouterr().out
    summary_line = next(l for l in captured.splitlines() if "[dd_dispatcher_summary]" in l)
    payload = json.loads(summary_line.split(" ", 1)[1])
    assert payload["decision"] == "weekend (Saturday)"
    assert payload["alerts_dispatched"] == 0


def test_main_returns_0_when_universe_empty(monkeypatch):
    """No PORTFOLIO_TICKERS set → empty universe → no FMP call → no POST."""
    monkeypatch.setenv(ENV_BASE_URL, "http://x")
    monkeypatch.setenv(ENV_ADMIN_SECRET, "s")
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    monkeypatch.setenv(ENV_FORCE_RUN, "true")

    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes") as fetch_mock, \
         patch("src.agents.dd.cron_dispatcher.requests.post") as post_mock:
        rc = main()
    assert rc == 0
    fetch_mock.assert_not_called()
    post_mock.assert_not_called()


def test_main_returns_0_when_no_quotes(basic_env, capsys):
    """FMP returns nothing → main returns 0 without dispatching."""
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value={}), \
         patch("src.agents.dd.cron_dispatcher.requests.post") as post_mock:
        rc = main()
    assert rc == 0
    post_mock.assert_not_called()


def test_main_returns_0_when_no_breaches(basic_env, capsys):
    """Quotes returned but none ≥ ±10% → no dispatches."""
    quotes = {"AAPL": _q("AAPL", 0.05), "MSFT": _q("MSFT", -0.03)}
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post") as post_mock:
        rc = main()
    assert rc == 0
    post_mock.assert_not_called()


# ── Dispatch behavior ──────────────────────────────────────────────────────


def test_main_dispatches_one_breach_via_http_post(basic_env, capsys):
    quotes = {
        "AAPL": _q("AAPL", -0.12, price=150.0),
        "MSFT": _q("MSFT",  0.04),
    }
    fake_resp = MagicMock(status_code=200, json=lambda: {
        "fired": True, "eligibility_reason": "first_breach",
        "dd_run_id": "ab" * 16,
    })
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp) as post_mock:
        rc = main()
    assert rc == 0
    assert post_mock.call_count == 1
    # Verify the POST payload
    call_kwargs = post_mock.call_args.kwargs
    params = call_kwargs["params"]
    assert params["secret"] == "test-secret"
    assert params["ticker"] == "AAPL"
    assert params["pct"] == -0.12
    assert params["price"] == 150.0
    assert params["agent_mode"] == "real"


def test_main_dispatches_multiple_breaches_in_magnitude_order(basic_env):
    quotes = {
        "AAPL": _q("AAPL", -0.12),
        "PEGA": _q("PEGA", -0.25),    # biggest move first
        "NVDA": _q("NVDA",  0.15),
    }
    fake_resp = MagicMock(status_code=200, json=lambda: {"fired": True})
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp) as post_mock:
        main()
    # 3 POSTs in order PEGA → NVDA → AAPL (by abs pct)
    tickers_in_order = [c.kwargs["params"]["ticker"] for c in post_mock.call_args_list]
    assert tickers_in_order == ["PEGA", "NVDA", "AAPL"]


def test_main_caps_dispatches_at_max_alerts_per_tick(monkeypatch, basic_env):
    """Safety valve: never dispatch more than MAX_ALERTS_PER_TICK in one tick."""
    monkeypatch.setenv(ENV_MAX_ALERTS_TICK, "2")
    quotes = {f"T{i}": _q(f"T{i}", -0.20 - i * 0.01) for i in range(5)}
    fake_resp = MagicMock(status_code=200, json=lambda: {"fired": True})
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp) as post_mock:
        rc = main()
    assert rc == 0
    assert post_mock.call_count == 2


def test_main_dry_run_does_not_post(monkeypatch, basic_env):
    """DD_DRY_RUN=true → log breaches but skip the POST."""
    monkeypatch.setenv(ENV_DRY_RUN, "true")
    quotes = {"AAPL": _q("AAPL", -0.12)}
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post") as post_mock:
        rc = main()
    assert rc == 0
    post_mock.assert_not_called()


def test_main_continues_when_one_post_fails(basic_env, capsys):
    """Network exception on one POST shouldn't stop the rest of the tick."""
    quotes = {
        "AAPL": _q("AAPL", -0.12),
        "MSFT": _q("MSFT", -0.15),
    }
    side_effects = [
        MagicMock(status_code=200, json=lambda: {"fired": True}),
        Exception("connection refused"),
    ]
    # The second call's side_effect is an Exception, requests.post raises it
    import requests as real_requests
    side_effects = [
        MagicMock(status_code=200, json=lambda: {"fired": True}),
        real_requests.RequestException("connection refused"),
    ]
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               side_effect=side_effects):
        rc = main()
    assert rc == 0
    # Summary should record one success + one failure
    captured = capsys.readouterr().out
    summary_line = next(l for l in captured.splitlines() if "[dd_dispatcher_summary]" in l)
    payload = json.loads(summary_line.split(" ", 1)[1])
    assert payload["alerts_dispatched"] == 1
    assert payload["failures"] == 1


def test_main_uses_custom_threshold(monkeypatch, basic_env):
    """DD_DISPATCH_THRESHOLD_PCT lets users tune the trigger threshold."""
    monkeypatch.setenv(ENV_THRESHOLD_PCT, "0.05")
    quotes = {"AAPL": _q("AAPL", -0.07)}    # 7% — under default 10%, over 5%
    fake_resp = MagicMock(status_code=200, json=lambda: {"fired": True})
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp) as post_mock:
        main()
    assert post_mock.call_count == 1


def test_fetch_tier1_via_http_happy_path():
    """Web service returns the expected JSON shape → tickers parsed correctly."""
    from src.agents.dd.cron_dispatcher import _fetch_tier1_via_http
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"tier": "tier1_watchlist", "tickers": ["CRM", "NOW", "PYPL"], "count": 3}
    fake_resp.raise_for_status = MagicMock()
    with patch("src.agents.dd.cron_dispatcher.requests.get", return_value=fake_resp):
        out = _fetch_tier1_via_http(base_url="http://x")
    assert out == {"CRM", "NOW", "PYPL"}


def test_fetch_tier1_via_http_unions_env_override(monkeypatch):
    """ENV var contents UNION with the HTTP response (force-add path)."""
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "EXTRA1,EXTRA2")
    from src.agents.dd.cron_dispatcher import _fetch_tier1_via_http
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"tickers": ["CRM"]}
    fake_resp.raise_for_status = MagicMock()
    with patch("src.agents.dd.cron_dispatcher.requests.get", return_value=fake_resp):
        out = _fetch_tier1_via_http(base_url="http://x")
    assert out == {"CRM", "EXTRA1", "EXTRA2"}


def test_fetch_tier1_via_http_falls_back_to_env_on_http_failure(monkeypatch):
    """Network blip → HTTP fails → fall back to PORTFOLIO_TICKERS so
    monitoring isn't silently disabled."""
    monkeypatch.setenv(ENV_PORTFOLIO_TICKERS, "FALLBACK1,FALLBACK2")
    from src.agents.dd.cron_dispatcher import _fetch_tier1_via_http
    import requests as r
    with patch("src.agents.dd.cron_dispatcher.requests.get",
               side_effect=r.RequestException("network down")):
        out = _fetch_tier1_via_http(base_url="http://x")
    assert out == {"FALLBACK1", "FALLBACK2"}


def test_fetch_tier1_via_http_empty_when_both_sources_empty(monkeypatch):
    """HTTP fails AND no env override → empty set (caller logs + skips)."""
    monkeypatch.delenv(ENV_PORTFOLIO_TICKERS, raising=False)
    from src.agents.dd.cron_dispatcher import _fetch_tier1_via_http
    import requests as r
    with patch("src.agents.dd.cron_dispatcher.requests.get",
               side_effect=r.RequestException("down")):
        out = _fetch_tier1_via_http(base_url="http://x")
    assert out == set()


def test_maybe_run_daily_cleanup_fires_once_per_day(monkeypatch, tmp_path):
    """First call hits /admin/dd-cleanup; subsequent calls same UTC day no-op."""
    import tempfile
    # Redirect tempfile.gettempdir() so the marker file lives in tmp_path
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    from src.agents.dd.cron_dispatcher import _maybe_run_daily_cleanup
    fake_resp = MagicMock(status_code=200, json=lambda: {"alerts_deleted": 3})
    with patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp) as post_mock:
        # First call → POST fires
        _maybe_run_daily_cleanup(base_url="http://x", secret="s")
        assert post_mock.call_count == 1
        # Second call same day → marker file blocks the POST
        _maybe_run_daily_cleanup(base_url="http://x", secret="s")
        assert post_mock.call_count == 1   # unchanged


def test_maybe_run_daily_cleanup_swallows_errors(monkeypatch, tmp_path):
    """Cleanup is best-effort — network/HTTP errors must not crash dispatch."""
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    from src.agents.dd.cron_dispatcher import _maybe_run_daily_cleanup
    import requests as r
    with patch("src.agents.dd.cron_dispatcher.requests.post",
               side_effect=r.RequestException("boom")):
        # Should not raise
        _maybe_run_daily_cleanup(base_url="http://x", secret="s")


def test_main_records_cooldown_skip_as_success(basic_env):
    """A 200 response with fired=false is a healthy cooldown skip — not
    counted as a failure."""
    quotes = {"AAPL": _q("AAPL", -0.12)}
    fake_resp = MagicMock(status_code=200, json=lambda: {
        "fired": False, "eligibility_reason": "in_cooldown (4.2h elapsed of 24h)",
    })
    with patch("src.agents.dd.cron_dispatcher.batch_quote.fetch_batch_quotes",
               return_value=quotes), \
         patch("src.agents.dd.cron_dispatcher.requests.post",
               return_value=fake_resp), \
         patch("src.agents.dd.cron_dispatcher.logger.info"):
        rc = main()
    assert rc == 0  # Cooldown is healthy
