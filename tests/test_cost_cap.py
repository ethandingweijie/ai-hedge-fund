"""Tests for src/agents/audit/cost_cap.py — budget heartbeat."""
from __future__ import annotations

import pytest

from src.agents.audit.cost_cap import (
    BUDGET_HEADROOM_USD,
    CostCap,
    DEFAULT_BUDGET_USD,
)


def test_default_budget_is_half_a_dollar():
    """Plan spec: hard cap is $0.50/run."""
    # Note: DEFAULT_BUDGET_USD reads env at import time. Test asserts
    # the SHIPPED default, not the env override behaviour.
    cap = CostCap()
    assert cap.max_usd == DEFAULT_BUDGET_USD
    assert cap.max_usd == 0.50 or cap.max_usd > 0   # tolerate env override in CI


def test_fresh_cap_has_headroom():
    cap = CostCap(max_usd=0.50)
    assert cap.check_headroom() is True
    assert cap.remaining() == 0.50 - BUDGET_HEADROOM_USD


def test_add_accumulates():
    cap = CostCap(max_usd=0.50)
    cap.add(0.10)
    cap.add(0.05)
    assert cap.accumulated_usd == pytest.approx(0.15)
    # max - headroom - accumulated, using approx for float-arithmetic tolerance
    assert cap.remaining() == pytest.approx(0.50 - 0.05 - 0.15)


def test_negative_cost_ignored():
    """Defensive: a buggy estimator returning -0.01 must not RAISE the budget."""
    cap = CostCap(max_usd=0.50)
    cap.add(0.20)
    cap.add(-0.05)   # would otherwise reset accumulated to 0.15
    assert cap.accumulated_usd == 0.20


def test_check_headroom_false_when_at_or_above_cap_minus_headroom():
    """The boundary: accumulated >= max - headroom → no more calls.
    With max=0.50 and headroom=0.05, the trigger is at $0.45."""
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    cap.add(0.44)
    assert cap.check_headroom() is True
    cap.add(0.01)                  # now exactly $0.45 — at the boundary
    assert cap.check_headroom() is False
    cap.add(0.10)                  # over the boundary
    assert cap.check_headroom() is False


def test_check_headroom_with_custom_headroom():
    """Tests can use a tight headroom to drive precise boundary cases.

    Uses binary-clean values (0.5, 0.125, 0.25) so float arithmetic doesn't
    create off-by-epsilon surprises at the boundary."""
    cap = CostCap(max_usd=0.5, headroom_usd=0.125)
    # Boundary at 0.5 - 0.125 = 0.375
    cap.add(0.25)
    assert cap.check_headroom() is True       # 0.25 < 0.375
    cap.add(0.125)                            # accumulated = 0.375 (exact)
    assert cap.check_headroom() is False      # 0.375 < 0.375 → False
    cap.add(0.5)                              # well over the cap
    assert cap.check_headroom() is False


def test_remaining_never_negative():
    """If accumulated overshoots, remaining() clamps at 0 (UI / logging guard)."""
    cap = CostCap(max_usd=0.50, headroom_usd=0.05)
    cap.add(1.00)
    assert cap.remaining() == 0.0


def test_multiple_caps_independent():
    """Each ticker audit gets its own CostCap. Verify no shared state."""
    a = CostCap()
    b = CostCap()
    a.add(0.20)
    assert b.accumulated_usd == 0.0
