"""Tests for src/agents/dd/scheduler.py — market-hours dispatch gate."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.dd.scheduler import (
    ENV_FORCE_RUN,
    ENV_SKIP_MARKET_GATE,
    DispatchDecision,
    should_run,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC-aware datetime for a specific moment."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ── Weekend gate ────────────────────────────────────────────────────────────


def test_skips_on_saturday(monkeypatch):
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    # 2026-05-09 is a Saturday
    decision = should_run(_utc(2026, 5, 9, 18, 0))
    assert decision.should_run is False
    assert "weekend" in decision.reason.lower()


def test_skips_on_sunday(monkeypatch):
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    # 2026-05-10 is a Sunday
    decision = should_run(_utc(2026, 5, 10, 18, 0))
    assert decision.should_run is False
    assert "weekend" in decision.reason.lower()


# ── Holiday gate ────────────────────────────────────────────────────────────


def test_skips_on_known_holiday(monkeypatch):
    """2026-05-25 is Memorial Day (in _HOLIDAYS_2026)."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    # 18:00 UTC = 14:00 ET on Mon May 25 — middle of "regular" hours
    decision = should_run(_utc(2026, 5, 25, 18, 0))
    assert decision.should_run is False
    assert "holiday" in decision.reason.lower()


# ── Market-hours gate ──────────────────────────────────────────────────────


def test_skips_pre_market(monkeypatch):
    """2026-05-12 (Tuesday) at 13:00 UTC = 09:00 ET — before 09:30 open."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run(_utc(2026, 5, 12, 13, 0))
    assert decision.should_run is False
    assert "pre-market" in decision.reason.lower()


def test_skips_after_market(monkeypatch):
    """2026-05-12 (Tuesday) at 21:00 UTC = 17:00 ET — after 16:00 close."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run(_utc(2026, 5, 12, 21, 0))
    assert decision.should_run is False
    assert "after-market" in decision.reason.lower()


def test_runs_during_regular_hours(monkeypatch):
    """2026-05-12 (Tuesday) at 17:00 UTC = 13:00 ET — regular session."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run(_utc(2026, 5, 12, 17, 0))
    assert decision.should_run is True
    assert "market open" in decision.reason.lower()


def test_runs_at_exactly_market_open(monkeypatch):
    """09:30 ET inclusive."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run(_utc(2026, 5, 12, 13, 30))
    assert decision.should_run is True


def test_skips_at_exactly_market_close(monkeypatch):
    """16:00 ET exclusive — the close itself is treated as after-market."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run(_utc(2026, 5, 12, 20, 0))
    assert decision.should_run is False
    assert "after-market" in decision.reason.lower()


# ── Env-var overrides ──────────────────────────────────────────────────────


def test_force_dispatch_env_bypasses_all_gates(monkeypatch):
    """DD_FORCE_DISPATCH=true forces a run even on weekend / holiday."""
    monkeypatch.setenv(ENV_FORCE_RUN, "true")
    decision = should_run(_utc(2026, 5, 10, 3, 0))   # Sunday 3am UTC
    assert decision.should_run is True
    assert "force_dispatch" in decision.reason.lower()


def test_skip_market_gate_env_bypasses_only_market_hours(monkeypatch):
    """DD_SKIP_MARKET_HOURS=true bypasses pre/after-market gate, but
    weekend gate still applies."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.setenv(ENV_SKIP_MARKET_GATE, "true")

    # Tuesday 04:00 UTC = midnight ET — outside normal hours but weekday
    decision = should_run(_utc(2026, 5, 12, 4, 0))
    assert decision.should_run is True

    # Saturday 18:00 UTC — still skipped (weekend gate not bypassed)
    decision = should_run(_utc(2026, 5, 9, 18, 0))
    assert decision.should_run is False
    assert "weekend" in decision.reason.lower()


# ── Default now=None path ──────────────────────────────────────────────────


def test_now_none_defaults_to_utc_now(monkeypatch):
    """Sanity: when called with no argument, it uses datetime.now(utc)."""
    monkeypatch.delenv(ENV_FORCE_RUN, raising=False)
    monkeypatch.delenv(ENV_SKIP_MARKET_GATE, raising=False)
    decision = should_run()
    assert isinstance(decision, DispatchDecision)
    # We can't predict the answer (depends on real time), but it must
    # produce a non-empty reason and a bool.
    assert isinstance(decision.should_run, bool)
    assert isinstance(decision.reason, str) and decision.reason
