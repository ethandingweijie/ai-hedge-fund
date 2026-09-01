"""Replay a gate at a past date and score it against what was actually reported.

The forward loop cannot start yet. Production holds 43 runs, the oldest three
weeks old, and every `ticker_signals.outcome` is `PENDING` — there is no ground
truth in the system and there cannot be for a quarter. Every Bayesian mechanism
in the Store A design would spend that quarter decaying rules toward deletion
with nothing to score them on.

Backward testing sidesteps that entirely: pick a date far enough in the past
that the following year has already been reported, rebuild the drivers as they
stood then, project forward under both the gated and ungated assumption, and
compare each to the figure the company actually printed. Ground truth is not
waited for — it is already on file.

**Why this is valid despite the point-in-time gaps.** The audit found no
filing-date awareness anywhere, and analyst estimates are a latest-snapshot
only, so a full pipeline replay would be contaminated. This module does not
replay the pipeline. It scores ONE intervention, and both paths are projected
with the *same* growth rate drawn from the trailing series. Growth, WACC,
share count and every other driver are identical on both sides and cancel out
of the difference. What survives is the margin intervention alone, which is
exactly what a gate's alpha and beta should be earned on.

The residual exposure is restatement — the trailing series is fetched as
filed today, not as filed then. For cash-flow lines that is small, and it
biases both paths identically.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The band inside which a difference is noise rather than evidence. Their
# design proposes a flat 2% of the reported metric; that is far too tight for
# free cash flow, which swings on working-capital timing, and too loose for
# revenue. Left as a parameter with a per-metric default rather than one
# global constant.
EPSILON_BY_METRIC: dict[str, float] = {
    # The metric the cash-conversion gate actually makes a claim about. See
    # `realised_owner_earnings_margin` for why reported FCF was the wrong
    # target.
    "owner_earnings_margin": 0.10,
    "free_cash_flow": 0.10,
    "revenue":        0.02,
    "ebitda":         0.05,
    "net_income":     0.08,
}
_DEFAULT_EPSILON = 0.05

# Symmetric, per the audit: every documented incident in this codebase is
# overvaluation (MELI 9.4x, PDD 11.8x, JD 8.7x, MNDY $475 on $65, INTU PT
# $1,076 on $396) and there is no recorded over-conservatism failure, so
# penalising the conservative direction harder would tune against the observed
# error distribution.
ALPHA_STEP = 1.0
BETA_STEP = 1.0


def epsilon_for(metric: str) -> float:
    return EPSILON_BY_METRIC.get(metric, _DEFAULT_EPSILON)


def delta_error_verdict(
    path_a: Optional[float],
    path_b: Optional[float],
    actual: Optional[float],
    metric: str = "free_cash_flow",
    epsilon: Optional[float] = None,
) -> dict[str, Any]:
    """Score one gate firing. Path A is ungated, Path B is gated.

        delta = (|B - actual| - |A - actual|) / |actual|

    Negative means the intervention moved the projection toward reality.
    Returns a verdict dict; never raises on degenerate input, because a gate
    that cannot be scored must be left unscored rather than guessed at.
    """
    eps = epsilon_for(metric) if epsilon is None else epsilon
    out: dict[str, Any] = {
        "metric": metric, "path_a": path_a, "path_b": path_b,
        "actual": actual, "epsilon": eps,
        "delta_error_pct": None, "verdict": "UNSCORABLE",
        "alpha_delta": 0.0, "beta_delta": 0.0,
    }
    if path_a is None or path_b is None or actual is None:
        return out
    try:
        a, b, act = float(path_a), float(path_b), float(actual)
    except (TypeError, ValueError):
        return out
    if act == 0:
        return out

    delta = (abs(b - act) - abs(a - act)) / abs(act)
    out["delta_error_pct"] = delta
    out["error_a_pct"] = abs(a - act) / abs(act)
    out["error_b_pct"] = abs(b - act) / abs(act)
    if delta <= -eps:
        out["verdict"] = "HELPED"
        out["alpha_delta"] = ALPHA_STEP
    elif delta >= eps:
        out["verdict"] = "FALSE_ALARM"
        out["beta_delta"] = BETA_STEP
    else:
        out["verdict"] = "NEUTRAL"
    return out


# ── Replay ──────────────────────────────────────────────────────────────────

_LINE_ITEMS = [
    "revenue", "free_cash_flow", "net_income", "capital_expenditure",
    "depreciation_and_amortization", "operating_cash_flow",
    "change_in_working_capital", "ebitda", "stock_based_compensation",
]


def _series(ticker: str, end_date: str, limit: int = 8) -> list[dict]:
    """Annual rows with period_end <= end_date, oldest first."""
    from src.agents.analysis.dcf_agent import _extract_annual_series
    from src.tools.api import search_line_items

    li = search_line_items(ticker, _LINE_ITEMS, end_date,
                           period="annual", limit=limit)
    rows, _ccy = _extract_annual_series(li or [])
    return rows


def _next_reported_year(ticker: str, after_period: str,
                        today: str) -> Optional[dict]:
    """The first annual row the company reported AFTER `after_period`.

    This is the ground truth the replay is scored against — a figure that was
    unknowable at the as-of date and is on file now.
    """
    for row in _series(ticker, today, limit=10):
        if str(row.get("period") or "") > str(after_period):
            return row
    return None


def backtest_cash_conversion(ticker: str, as_of: str,
                             today: str = "2026-08-30") -> dict[str, Any]:
    """Replay the cash-conversion gate at `as_of` and score it.

    Both paths carry the SAME growth rate, drawn from the trailing series, so
    the growth model cancels out of the comparison and the verdict is earned
    by the margin intervention alone.
    """
    from src.agents.analysis.dcf_agent import (
        _CASH_CONVERSION_TOLERANCE,
        _historical_cagr,
        _mean_fcf_margin,
        _projectable_fcf_margin_cap,
    )

    out: dict[str, Any] = {
        "ticker": ticker, "as_of": as_of, "gate_id": "GATE_CASH_CONVERSION",
        "fired": False, "verdict": "UNSCORABLE",
    }
    trailing = _series(ticker, as_of, limit=5)
    if len(trailing) < 2:
        out["skip_reason"] = "insufficient trailing history"
        return out

    last_period = str(trailing[-1].get("period") or "")
    out["trailing_through"] = last_period

    margin_raw = _mean_fcf_margin(trailing)
    cap, _trailing_margin, _basis = _projectable_fcf_margin_cap(trailing)
    if margin_raw is None:
        out["skip_reason"] = "no trailing FCF margin"
        return out

    out["margin_path_a"] = margin_raw
    out["margin_cap"] = cap
    if not (cap is not None and cap > 0
            and margin_raw > cap * _CASH_CONVERSION_TOLERANCE):
        out["skip_reason"] = "gate does not fire at this date"
        out["margin_path_b"] = margin_raw
        return out

    out["fired"] = True
    out["margin_path_b"] = cap

    revenue_base = trailing[-1].get("revenue")
    g = _historical_cagr(trailing, revenue_base) or 0.0
    out["growth_used"] = g

    actual_row = _next_reported_year(ticker, last_period, today)
    if not actual_row:
        out["skip_reason"] = "no reported year after the as-of date yet"
        return out
    out["actual_period"] = actual_row.get("period")

    # Same revenue path on both sides; only the margin differs.
    rev_1 = float(revenue_base) * (1.0 + g)
    out["projected_revenue"] = rev_1
    out["actual_revenue"] = actual_row.get("revenue")

    verdict = delta_error_verdict(
        path_a=rev_1 * margin_raw,
        path_b=rev_1 * cap,
        actual=actual_row.get("free_cash_flow"),
        metric="free_cash_flow",
    )
    out.update(verdict)
    return out


# ── Scoring the claim the gate actually makes ───────────────────────────────

def _owner_earnings_margin(row: dict) -> Optional[float]:
    """(FCF - change in working capital) / revenue for one reported year.

    The cash-conversion gate does not claim to predict next year's reported
    free cash flow. It claims that the part of reported FCF which comes from
    working capital is not repeatable and must not be capitalised for ten
    years. Scoring it against reported FCF therefore tested a claim it never
    made — and marked it a false alarm on MELI for correctly declining to
    project a float that had not yet stopped growing.
    """
    rev = row.get("revenue")
    fcf = row.get("free_cash_flow")
    if not rev or rev <= 0 or fcf is None:
        return None
    dwc = row.get("change_in_working_capital") or 0.0
    return (fcf - dwc) / rev


def realised_owner_earnings_margin(
    ticker: str, after_period: str, today: str, max_years: int = 3,
) -> tuple[Optional[float], int, list[str]]:
    """Mean owner-earnings MARGIN over the years reported after `after_period`.

    Two deliberate choices, both to match what the gate asserts:

    * **A margin, not a cash level.** The gate outputs a margin, so comparing
      margin to margin removes the growth rate, the revenue base and the
      compounding from the comparison entirely. Previously both paths were
      projected forward and their absolute errors reached 300-450% on names
      like BABA and JPM — noise from the growth assumption, which the gate
      does not touch, swamping the intervention it does.
    * **A mean over several years, not one.** "Terminal" means the level that
      persists. A single year is dominated by timing; averaging is the closest
      observable proxy for steady state.
    """
    margins: list[float] = []
    periods: list[str] = []
    for row in _series(ticker, today, limit=10):
        if str(row.get("period") or "") <= str(after_period):
            continue
        m = _owner_earnings_margin(row)
        if m is None:
            continue
        margins.append(m)
        periods.append(str(row.get("period")))
        if len(margins) >= max_years:
            break
    if not margins:
        return None, 0, []
    return sum(margins) / len(margins), len(margins), periods


def backtest_cash_conversion_owner_earnings(
    ticker: str, as_of: str, today: str = "2026-08-30", max_years: int = 3,
) -> dict[str, Any]:
    """Replay the cash-conversion gate and score it on terminal owner earnings.

    Path A and Path B are the two margins the gate chose between. No
    projection is involved, so nothing but the intervention is being scored.
    """
    from src.agents.analysis.dcf_agent import (
        _CASH_CONVERSION_TOLERANCE,
        _mean_fcf_margin,
        _projectable_fcf_margin_cap,
    )

    out: dict[str, Any] = {
        "ticker": ticker, "as_of": as_of, "gate_id": "GATE_CASH_CONVERSION",
        "metric": "owner_earnings_margin", "fired": False,
        "verdict": "UNSCORABLE",
    }
    trailing = _series(ticker, as_of, limit=5)
    if len(trailing) < 2:
        out["skip_reason"] = "insufficient trailing history"
        return out

    last_period = str(trailing[-1].get("period") or "")
    out["trailing_through"] = last_period

    margin_raw = _mean_fcf_margin(trailing)
    cap, _tr, _basis = _projectable_fcf_margin_cap(trailing)
    if margin_raw is None:
        out["skip_reason"] = "no trailing FCF margin"
        return out
    out["margin_path_a"] = margin_raw
    out["margin_cap"] = cap
    if not (cap is not None and cap > 0
            and margin_raw > cap * _CASH_CONVERSION_TOLERANCE):
        out["skip_reason"] = "gate does not fire at this date"
        return out

    out["fired"] = True
    realised, n, periods = realised_owner_earnings_margin(
        ticker, last_period, today, max_years)
    out["realised_periods"] = periods
    out["realised_years"] = n
    if realised is None:
        out["skip_reason"] = "no reported year after the as-of date yet"
        return out
    out["realised_owner_earnings_margin"] = realised

    out.update(delta_error_verdict(
        path_a=margin_raw, path_b=cap, actual=realised,
        metric="owner_earnings_margin",
    ))
    return out
