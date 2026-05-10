"""
performance.py — Phase 3 attribution: measure whether the agent's
recommended_action correlates with subsequent forward returns.

Pipeline:

    For each fired alert at trigger T:
      ① parse recommended_action prose → ActionCategory enum
         (ADD / TRIM / EXIT / HOLD / WATCH / UNCLEAR)
      ② fetch forward prices from FMP for T+1d / T+5d / T+22d
      ③ compute forward_returns = (P_t+N / trigger_price) - 1
      ④ grade action against 5d return:
           HOLD   correct if |fwd| < 5%
           TRIM/EXIT correct if fwd < -2%
           ADD/BUY  correct if fwd > +2%
           WATCH/UNCLEAR → always neutral
      ⑤ persist to dd_alerts: forward_{1,5,22}d_return + action_outcome

The persisted data feeds two surfaces:
  • GET /api/dd-alerts/performance — aggregate hit rates by action / dir / reason
  • Dashboard footer — "Last 30d: N alerts, X% action-correct, +Y.Y% mean 5d alpha"

Pure functions. No I/O except the FMP fetch (which is mocked in tests).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final


logger = logging.getLogger(__name__)


# ── Action category extraction ──────────────────────────────────────────────


class ActionCategory(str, Enum):
    """Structured grade of the LLM's prose `recommended_action` field.

    The LLM emits free text ("TRIM 25% on bounce", "WATCH-CLOSELY. Stand
    aside today. Verify volume normalization."). We pattern-match the
    prose to one of these categories so we can grade outcomes uniformly.
    """
    ADD     = "ADD"        # add to position / buy
    TRIM    = "TRIM"       # reduce position
    EXIT    = "EXIT"       # close position fully
    HOLD    = "HOLD"       # no change to current position
    WATCH   = "WATCH"      # stand aside, monitor — non-committal
    UNCLEAR = "UNCLEAR"    # parser couldn't categorize


# Order matters: more specific patterns first so "WATCH-CLOSELY" beats
# the EXIT regex's "CLOSE" substring match.
_ACTION_PATTERNS: list[tuple[ActionCategory, re.Pattern]] = [
    # WATCH first — the "WATCH-CLOSELY" is by far the most common LLM output
    # and contains "CLOSELY" which would otherwise confuse the EXIT regex.
    (ActionCategory.WATCH,
     re.compile(r"\b(watch[\s\-]closely|watch[\s\-]carefully|stand[\s\-]aside|"
                r"wait[\s\-]and[\s\-]see|monitor|do[\s\-]not[\s\-]act)\b", re.I)),

    # ADD / BUY
    (ActionCategory.ADD,
     re.compile(r"\b(add(?!\b\s*risk)|buy|accumulate|overweight|"
                r"increase[\s\-]exposure|scale[\s\-]in)\b", re.I)),

    # TRIM / REDUCE
    (ActionCategory.TRIM,
     re.compile(r"\b(trim|reduce[\s\-]position|pare|take[\s\-]profit|"
                r"de[\s\-]risk)\b", re.I)),

    # EXIT / SELL
    (ActionCategory.EXIT,
     re.compile(r"\b(exit|sell|close[\s\-]position|liquidate|"
                r"underweight)\b", re.I)),

    # HOLD / NO-CHANGE
    (ActionCategory.HOLD,
     re.compile(r"\b(hold|no[\s\-]change|maintain|stay[\s\-]put)\b", re.I)),
]


def parse_recommended_action(text: str | None) -> ActionCategory:
    """Map LLM-generated `recommended_action` prose → ActionCategory.

    First match wins per the _ACTION_PATTERNS order (WATCH > ADD > TRIM >
    EXIT > HOLD). Returns UNCLEAR if no pattern matches.

    Examples:
      "TRIM 25% on bounce."           → TRIM
      "WATCH-CLOSELY. Stand aside."   → WATCH
      "ADD on weakness."              → ADD
      "Continue to hold."             → HOLD
      "thesis_under_review (...)"     → UNCLEAR
      ""                              → UNCLEAR
    """
    if not text or not text.strip():
        return ActionCategory.UNCLEAR
    for category, pattern in _ACTION_PATTERNS:
        if pattern.search(text):
            return category
    return ActionCategory.UNCLEAR


# ── Outcome grading ─────────────────────────────────────────────────────────


# Default thresholds (overridable via env). Decimal returns:
#   HOLD: action is correct if price barely moved (|fwd| < threshold)
#   TRIM/EXIT: correct if fwd return < -threshold (sell was right call)
#   ADD/BUY:   correct if fwd return > +threshold (add was right call)
#   WATCH/UNCLEAR: never graded as correct/incorrect — neutral by design
ENV_HOLD_FLAT_THRESHOLD: Final[str] = "DD_GRADE_HOLD_THRESHOLD_PCT"
ENV_DIRECTIONAL_THRESHOLD: Final[str] = "DD_GRADE_DIRECTIONAL_THRESHOLD_PCT"

_DEFAULT_HOLD_FLAT = 0.05         # ±5%
_DEFAULT_DIRECTIONAL = 0.02       # ±2%


class ActionOutcome(str, Enum):
    CORRECT   = "correct"
    INCORRECT = "incorrect"
    NEUTRAL   = "neutral"           # WATCH, UNCLEAR, or HOLD-with-large-move-in-either-direction
    PENDING   = "pending"           # forward_5d_return not yet computed
    NO_DATA   = "no_data"           # FMP returned no prices for the window


def grade_action(
    action: ActionCategory,
    fwd_5d_return: float | None,
    *,
    hold_flat_threshold: float | None = None,
    directional_threshold: float | None = None,
) -> ActionOutcome:
    """Grade a (parsed action, observed forward return) pair.

    Returns PENDING if fwd_5d_return is None (not graded yet).
    Returns NEUTRAL for WATCH / UNCLEAR (no commitment, can't be wrong).
    Returns CORRECT/INCORRECT for ADD / TRIM / EXIT / HOLD per the rules
    described in the module docstring.
    """
    if fwd_5d_return is None:
        return ActionOutcome.PENDING

    if hold_flat_threshold is None:
        hold_flat_threshold = _read_float_env(ENV_HOLD_FLAT_THRESHOLD, _DEFAULT_HOLD_FLAT)
    if directional_threshold is None:
        directional_threshold = _read_float_env(ENV_DIRECTIONAL_THRESHOLD, _DEFAULT_DIRECTIONAL)

    if action in (ActionCategory.WATCH, ActionCategory.UNCLEAR):
        return ActionOutcome.NEUTRAL

    if action == ActionCategory.HOLD:
        return (ActionOutcome.CORRECT if abs(fwd_5d_return) < hold_flat_threshold
                else ActionOutcome.INCORRECT)

    if action in (ActionCategory.TRIM, ActionCategory.EXIT):
        return (ActionOutcome.CORRECT if fwd_5d_return < -directional_threshold
                else ActionOutcome.INCORRECT)

    if action == ActionCategory.ADD:
        return (ActionOutcome.CORRECT if fwd_5d_return > directional_threshold
                else ActionOutcome.INCORRECT)

    return ActionOutcome.NEUTRAL


# ── Forward-return computation ─────────────────────────────────────────────


@dataclass(frozen=True)
class ForwardReturns:
    """Forward returns at three trading-day horizons. None when the window
    extends past the latest available price."""
    fwd_1d:  float | None = None
    fwd_5d:  float | None = None
    fwd_22d: float | None = None


def compute_forward_returns(
    ticker:       str,
    trigger_date: datetime,
    trigger_price: float,
    *,
    fetch_prices = None,    # injectable for tests
) -> ForwardReturns:
    """Fetch FMP daily prices from the trigger date forward and compute
    1d / 5d / 22d forward returns.

    Args:
      ticker:        Ticker symbol.
      trigger_date:  UTC datetime of the trigger.
      trigger_price: Price at the moment of the alert.
      fetch_prices:  Optional override for src.tools.api.get_prices —
                     useful in tests.

    Returns:
      ForwardReturns with each horizon's return as a decimal, or None if
      that horizon hasn't elapsed yet (or FMP returned no data).

    Notes:
      • "Trading days" = rows in FMP's daily-price response. FMP skips
        weekends + holidays naturally so we don't need a market-calendar.
      • The trigger_price is used as denominator (not P_t0 from FMP) —
        captures the actual entry price the agent saw.
    """
    if fetch_prices is None:
        from src.tools.api import get_prices
        fetch_prices = get_prices

    # Fetch a wide window (35 calendar days) to cover up to ~22 trading days
    # plus weekends/holidays buffer.
    start = trigger_date.date().isoformat()
    end   = (trigger_date + timedelta(days=35)).date().isoformat()

    try:
        rows = fetch_prices(ticker, start, end) or []
    except Exception as exc:
        logger.warning("performance: get_prices failed for %s (%s—%s): %s",
                       ticker, start, end, exc)
        return ForwardReturns()

    # Sort ascending by date and skip the trigger-date row itself
    # (we want the FIRST trading day strictly after the trigger).
    rows_sorted = sorted(rows, key=lambda r: r.time)
    trigger_iso = trigger_date.date().isoformat()
    forward = [r for r in rows_sorted if r.time > trigger_iso]

    if not forward or trigger_price <= 0:
        return ForwardReturns()

    def _ret(idx: int) -> float | None:
        # idx is 0-based: T+1 trading day = forward[0]
        if idx >= len(forward):
            return None
        try:
            return (forward[idx].close - trigger_price) / trigger_price
        except (AttributeError, ZeroDivisionError, TypeError):
            return None

    return ForwardReturns(
        fwd_1d  = _ret(0),
        fwd_5d  = _ret(4),
        fwd_22d = _ret(21),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
