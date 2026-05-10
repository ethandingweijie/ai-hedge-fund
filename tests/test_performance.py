"""Tests for src/agents/dd/performance.py — Phase 3 attribution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.dd.performance import (
    ActionCategory,
    ActionOutcome,
    ForwardReturns,
    compute_forward_returns,
    grade_action,
    parse_recommended_action,
)


# ── parse_recommended_action ────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    # ADD variants
    ("ADD on weakness.",                                ActionCategory.ADD),
    ("ADD 25% to position.",                            ActionCategory.ADD),
    ("BUY the dip aggressively.",                       ActionCategory.ADD),
    ("Accumulate at current levels.",                   ActionCategory.ADD),
    ("Recommend overweight.",                           ActionCategory.ADD),
    ("Scale-in on confirmation.",                       ActionCategory.ADD),
    # TRIM variants
    ("TRIM 25% on bounce.",                             ActionCategory.TRIM),
    ("Pare exposure into strength.",                    ActionCategory.TRIM),
    ("Take profit at $200.",                            ActionCategory.TRIM),
    ("De-risk this position.",                          ActionCategory.TRIM),
    # EXIT variants
    ("EXIT immediately.",                               ActionCategory.EXIT),
    ("Sell the position.",                              ActionCategory.EXIT),
    ("Close position by Friday.",                       ActionCategory.EXIT),
    ("Liquidate the holding.",                          ActionCategory.EXIT),
    ("Move to underweight.",                            ActionCategory.EXIT),
    # HOLD variants
    ("HOLD the position.",                              ActionCategory.HOLD),
    ("No-change recommended.",                          ActionCategory.HOLD),
    ("Maintain current sizing.",                        ActionCategory.HOLD),
    # WATCH variants — must beat EXIT/HOLD by ordering
    ("WATCH-CLOSELY for next 48 hours.",                ActionCategory.WATCH),
    ("Watch closely; do not act.",                      ActionCategory.WATCH),
    ("Stand aside today.",                              ActionCategory.WATCH),
    ("Wait-and-see for tomorrow's open.",               ActionCategory.WATCH),
    ("Monitor for confirmation.",                       ActionCategory.WATCH),
    # UNCLEAR
    ("",                                                ActionCategory.UNCLEAR),
    ("   ",                                             ActionCategory.UNCLEAR),
    (None,                                              ActionCategory.UNCLEAR),
    ("thesis_under_review (synthetic admin trigger)",   ActionCategory.UNCLEAR),
    ("(pending — agent running)",                       ActionCategory.UNCLEAR),
])
def test_parse_recommended_action(text, expected):
    assert parse_recommended_action(text) == expected


def test_parse_watch_beats_exit_keyword():
    """The 'CLOSELY' in 'WATCH-CLOSELY' should not trigger the EXIT 'CLOSE' regex."""
    assert parse_recommended_action("WATCH-CLOSELY. Confirm support.") == ActionCategory.WATCH


def test_parse_real_world_pega_response():
    """Actual production output from the MELI smoke test."""
    real = (
        "WATCH-CLOSELY. Stand aside today; verify volume normalization "
        "and a technical base over the next 48 hours before deploying "
        "an ADD or HOLD decision."
    )
    assert parse_recommended_action(real) == ActionCategory.WATCH


# ── grade_action ────────────────────────────────────────────────────────────


def test_grade_pending_when_return_is_none():
    assert grade_action(ActionCategory.HOLD, None) == ActionOutcome.PENDING


def test_grade_watch_always_neutral():
    assert grade_action(ActionCategory.WATCH, +0.50) == ActionOutcome.NEUTRAL
    assert grade_action(ActionCategory.WATCH, -0.50) == ActionOutcome.NEUTRAL


def test_grade_unclear_always_neutral():
    assert grade_action(ActionCategory.UNCLEAR, +0.20) == ActionOutcome.NEUTRAL
    assert grade_action(ActionCategory.UNCLEAR, -0.20) == ActionOutcome.NEUTRAL


def test_grade_hold():
    """HOLD correct if |fwd| < hold_flat_threshold (default 5%)."""
    assert grade_action(ActionCategory.HOLD,  0.03) == ActionOutcome.CORRECT
    assert grade_action(ActionCategory.HOLD, -0.03) == ActionOutcome.CORRECT
    assert grade_action(ActionCategory.HOLD,  0.07) == ActionOutcome.INCORRECT   # too far up
    assert grade_action(ActionCategory.HOLD, -0.07) == ActionOutcome.INCORRECT   # too far down


def test_grade_trim_exit():
    """TRIM/EXIT correct if fwd < -directional_threshold (default 2%)."""
    assert grade_action(ActionCategory.TRIM, -0.03) == ActionOutcome.CORRECT
    assert grade_action(ActionCategory.EXIT, -0.10) == ActionOutcome.CORRECT
    assert grade_action(ActionCategory.TRIM, +0.05) == ActionOutcome.INCORRECT
    assert grade_action(ActionCategory.EXIT, -0.01) == ActionOutcome.INCORRECT   # not down enough


def test_grade_add():
    """ADD correct if fwd > +directional_threshold."""
    assert grade_action(ActionCategory.ADD, +0.05) == ActionOutcome.CORRECT
    assert grade_action(ActionCategory.ADD, +0.01) == ActionOutcome.INCORRECT    # not up enough
    assert grade_action(ActionCategory.ADD, -0.05) == ActionOutcome.INCORRECT


def test_grade_thresholds_overridable():
    """Custom thresholds change the boundaries."""
    # Tighter HOLD: |fwd| < 1% required
    assert grade_action(ActionCategory.HOLD, 0.03, hold_flat_threshold=0.01) == ActionOutcome.INCORRECT
    # Looser TRIM: only need fwd < -10% to be correct
    assert grade_action(ActionCategory.TRIM, -0.05, directional_threshold=0.10) == ActionOutcome.INCORRECT
    assert grade_action(ActionCategory.TRIM, -0.15, directional_threshold=0.10) == ActionOutcome.CORRECT


# ── compute_forward_returns ─────────────────────────────────────────────────


class _FakePrice:
    def __init__(self, time, close):
        self.time, self.close = time, close


def test_compute_forward_returns_basic():
    """5 forward trading days, all positive → ADD-favorable returns."""
    trigger = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    fake = [
        _FakePrice("2026-05-11", 100.0),  # trigger date — excluded
        _FakePrice("2026-05-12", 102.0),  # +2% T+1
        _FakePrice("2026-05-13", 103.0),
        _FakePrice("2026-05-14", 104.0),
        _FakePrice("2026-05-15", 105.0),
        _FakePrice("2026-05-18", 110.0),  # +10% T+5
    ]
    fr = compute_forward_returns(
        "AAPL", trigger, trigger_price=100.0,
        fetch_prices=lambda *a, **kw: fake,
    )
    assert fr.fwd_1d  == pytest.approx(0.02)
    assert fr.fwd_5d  == pytest.approx(0.10)
    assert fr.fwd_22d is None    # not enough days


def test_compute_forward_returns_drop():
    """Negative forward return — T+1 price already below trigger price."""
    trigger = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    # Trigger price = 100; T+1=98, T+2=96, ..., T+5=90
    fake = [_FakePrice(f"2026-05-{12+i:02d}", 100.0 - (i + 1) * 2) for i in range(6)]
    fr = compute_forward_returns(
        "PEGA", trigger, trigger_price=100.0,
        fetch_prices=lambda *a, **kw: fake,
    )
    assert fr.fwd_1d < 0
    assert fr.fwd_5d < 0


def test_compute_forward_returns_empty_response():
    fr = compute_forward_returns(
        "X", datetime.now(timezone.utc), 100.0,
        fetch_prices=lambda *a, **kw: [],
    )
    assert fr == ForwardReturns()


def test_compute_forward_returns_swallows_fetch_errors():
    def boom(*a, **kw):
        raise RuntimeError("FMP down")
    fr = compute_forward_returns(
        "X", datetime.now(timezone.utc), 100.0, fetch_prices=boom,
    )
    assert fr == ForwardReturns()


def test_compute_forward_returns_filters_pre_trigger_rows():
    """Rows on or before the trigger date must not contribute to forward returns."""
    trigger = datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc)
    fake = [
        _FakePrice("2026-05-08", 50.0),    # before trigger — must be ignored
        _FakePrice("2026-05-09", 55.0),    # before — ignored
        _FakePrice("2026-05-11", 100.0),   # trigger date — ignored (we want strictly after)
        _FakePrice("2026-05-12", 110.0),   # T+1
    ]
    fr = compute_forward_returns(
        "X", trigger, 100.0, fetch_prices=lambda *a, **kw: fake,
    )
    assert fr.fwd_1d == pytest.approx(0.10)


def test_compute_forward_returns_zero_trigger_price_safe():
    """Defensive: division by zero on bad input → returns None, doesn't raise."""
    fr = compute_forward_returns(
        "X", datetime.now(timezone.utc), trigger_price=0.0,
        fetch_prices=lambda *a, **kw: [_FakePrice("2026-05-12", 100.0)],
    )
    assert fr.fwd_1d is None


# ── alert_dedup integration ────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test_perf.db"
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(db))
    from src.agents.dd import alert_dedup
    alert_dedup._clear_all_alerts_for_test()
    yield alert_dedup


def test_set_alert_grade_writes_columns(tmp_db):
    """After mark_alerted + set_alert_grade, the row has populated columns."""
    now = datetime.now(timezone.utc)
    tmp_db.mark_alerted(
        ticker="PEGA", direction="DROP", pct=-0.13, price=87.0,
        tier="t1", reason="first_breach", now=now,
    )
    n = tmp_db.set_alert_grade(
        ticker="PEGA",
        last_direction="DROP",
        last_triggered_at=now.isoformat(),
        forward_1d_return=-0.02,
        forward_5d_return=-0.07,
        forward_22d_return=None,
        action_category="WATCH",
        action_outcome="neutral",
    )
    assert n == 1

    with tmp_db._conn() as conn:
        row = conn.execute(
            "SELECT forward_5d_return, action_category, action_outcome "
            "FROM dd_alerts WHERE ticker = ?", ("PEGA",)
        ).fetchone()
    assert row[0] == pytest.approx(-0.07)
    assert row[1] == "WATCH"
    assert row[2] == "neutral"


def test_set_alert_grade_returns_zero_when_no_match(tmp_db):
    """Wrong PK → no rows updated."""
    n = tmp_db.set_alert_grade(
        ticker="NOPE", last_direction="DROP",
        last_triggered_at="2026-01-01T00:00:00+00:00",
        forward_1d_return=0.0, forward_5d_return=0.0, forward_22d_return=0.0,
        action_category="HOLD", action_outcome="correct",
    )
    assert n == 0


def test_get_pending_grade_rows_filters_by_age(tmp_db):
    """Rows newer than min_age_days must not appear in pending list."""
    now = datetime.now(timezone.utc)
    tmp_db.mark_alerted(
        ticker="OLD", direction="DROP", pct=-0.13, price=87,
        tier="t1", reason="first_breach", now=now - timedelta(days=10),
    )
    tmp_db.mark_alerted(
        ticker="NEW", direction="DROP", pct=-0.11, price=89,
        tier="t1", reason="first_breach", now=now - timedelta(days=2),
    )
    pending = tmp_db.get_pending_grade_rows(min_age_days=7)
    tickers = [r["ticker"] for r in pending]
    assert "OLD" in tickers
    assert "NEW" not in tickers


def test_get_pending_grade_rows_excludes_already_graded(tmp_db):
    """Rows with forward_5d_return populated must not be re-graded."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    tmp_db.mark_alerted(
        ticker="GRADED", direction="DROP", pct=-0.13, price=87,
        tier="t1", reason="first_breach", now=old,
    )
    tmp_db.set_alert_grade(
        ticker="GRADED", last_direction="DROP",
        last_triggered_at=old.isoformat(),
        forward_1d_return=-0.01, forward_5d_return=-0.05,
        forward_22d_return=None,
        action_category="TRIM", action_outcome="correct",
    )
    pending = tmp_db.get_pending_grade_rows(min_age_days=7)
    assert all(r["ticker"] != "GRADED" for r in pending)
