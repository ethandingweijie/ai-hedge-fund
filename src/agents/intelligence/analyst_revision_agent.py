"""
src/agents/intelligence/analyst_revision_agent.py
==================================================
Phase 2.5 — Analyst Revision Agent (deterministic, no LLM)

Runs in parallel with the Insider Activity Agent immediately after the
Strategic Router (Phase 2) and before the Industry Specialist (Phase 3).

Data sources:
  FMP /stable/analyst-estimates   → current consensus EPS / revenue estimates
                                    (dispersion = high-low spread as % of avg)
  FMP /stable/earnings-surprises  → beat/miss history (free tier)

Output written to state["data"]["analyst_revisions"][ticker] as an
AnalystRevisionOutput dict, consumed by:
  - Damodaran  (growth-rate anchor and estimate quality check)
  - Graham     (earnings consistency screen)
  - Druckenmiller (momentum confirmation)
  - Portfolio Manager (dispersion flag → conviction haircut)

State compatibility:
  Reads:  state["data"]["tickers"], state["data"]["end_date"]
  Writes: state["data"]["analyst_revisions"][ticker]
  Format: AnalystRevisionOutput.model_dump()  — safe to deserialise downstream
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from src.graph.state import AgentState
from src.data.models import AnalystRevisionOutput, EarningsSurprise, AnalystEstimates
from src.tools.api import get_analyst_estimates, get_earnings_surprises

# Dispersion thresholds (EPS high-low spread as % of |avg|)
_DISPERSION_LOW    = 10.0   # < 10% — analysts agree
_DISPERSION_MEDIUM = 25.0   # 10–25% — moderate uncertainty
# > 25% → HIGH — wide disagreement, conviction haircut warranted

# Minimum consecutive surprises to declare a directional streak
_STREAK_THRESHOLD = 2


def _classify_dispersion(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct < _DISPERSION_LOW:
        return "LOW"
    if pct < _DISPERSION_MEDIUM:
        return "MEDIUM"
    return "HIGH"


def _compute_surprise_streak(surprises: list[dict]) -> tuple[int, str]:
    """
    Return (streak, direction) from ordered surprises (newest first).

    streak > 0 → consecutive beats  (e.g. +3 = 3 beats in a row)
    streak < 0 → consecutive misses (e.g. -2 = 2 misses in a row)
    direction  → "BEAT" | "MISS" | "MIXED" | "UNKNOWN"
    """
    if not surprises:
        return 0, "UNKNOWN"

    streak = 0
    last_beat = surprises[0]["beat"]

    for s in surprises:
        if s["beat"] == last_beat:
            streak += 1 if last_beat else -1
        else:
            break

    # Overall direction based on majority
    beats = sum(1 for s in surprises if s["beat"])
    misses = len(surprises) - beats
    if beats == misses:
        direction = "MIXED"
    elif beats > misses:
        direction = "BEAT"
    else:
        direction = "MISS"

    return streak, direction


def _infer_revision_direction(
    streak: int,
    dispersion: str,
    surprise_direction: str,
) -> str:
    """
    Infer estimate revision momentum from observable proxies.

    FMP does not expose historical consensus snapshots, so we can't directly
    measure the delta between this week's and last week's estimates. Instead
    we use two proxies:

    1. Earnings surprise streak: analysts who consistently underestimate
       earnings are being revised upward (anchoring effect reversal).
    2. Dispersion: narrowing → consensus converging (often post-revision);
       widening → uncertainty increasing (often pre-revision or during guidance
       withdrawal).

    Logic:
      streak >= +THRESHOLD → recent beats → estimates likely trailing reality
                              → ACCELERATING_UP
      streak <= -THRESHOLD → recent misses → guidance cuts incoming
                              → ACCELERATING_DOWN
      |streak| == 1 with direction matching → STABLE (one data point only)
      Mixed streaks with HIGH dispersion → DECELERATING (no clear consensus)
      Otherwise → STABLE
    """
    if streak >= _STREAK_THRESHOLD:
        return "ACCELERATING_UP"
    if streak <= -_STREAK_THRESHOLD:
        return "ACCELERATING_DOWN"
    if dispersion == "HIGH" and surprise_direction == "MIXED":
        return "DECELERATING"
    if surprise_direction == "UNKNOWN":
        return "UNKNOWN"
    return "STABLE"


def run_analyst_revision_agent(state: AgentState) -> AgentState:
    """
    Compute analyst revision metrics for each ticker in state.

    Reads:   state["data"]["tickers"], state["data"]["end_date"]
    Writes:  state["data"]["analyst_revisions"][ticker]
    """
    tickers  = state["data"]["tickers"]
    end_date = state["data"]["end_date"]
    api_key  = (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIAL_DATASETS_API_KEY")
    )

    results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  [AnalystRevisionAgent] {ticker} — fetching estimates & surprises")

        # ── Fetch forward estimates (FMP Basic plan or higher) ────────────
        estimates: list[AnalystEstimates] = get_analyst_estimates(
            ticker, end_date=end_date, period="annual", limit=2, api_key=api_key
        )

        # ── Fetch earnings surprise history (FMP free tier) ───────────────
        surprise_rows: list[dict] = get_earnings_surprises(
            ticker, end_date=end_date, limit=8, api_key=api_key
        )

        # ── Compute EPS dispersion from nearest forward year ──────────────
        eps_dispersion_pct:     float | None = None
        revenue_dispersion_pct: float | None = None
        analyst_count:          int          = 0

        if estimates:
            nearest = estimates[0]   # sorted by period_end, nearest first

            if (nearest.eps_high is not None and nearest.eps_low is not None
                    and nearest.eps_avg and nearest.eps_avg != 0):
                eps_dispersion_pct = abs(
                    (nearest.eps_high - nearest.eps_low) / abs(nearest.eps_avg) * 100
                )

            if (nearest.revenue_high is not None and nearest.revenue_low is not None
                    and nearest.revenue_avg and nearest.revenue_avg != 0):
                revenue_dispersion_pct = abs(
                    (nearest.revenue_high - nearest.revenue_low)
                    / abs(nearest.revenue_avg) * 100
                )

            analyst_count = nearest.analyst_count_eps or nearest.analyst_count_revenue or 0

        # ── Compute surprise streak & direction ───────────────────────────
        streak, surprise_direction = _compute_surprise_streak(surprise_rows)

        dispersion_label = _classify_dispersion(eps_dispersion_pct)
        revision_direction = _infer_revision_direction(
            streak, dispersion_label, surprise_direction
        )

        # ── Build EarningsSurprise objects (for downstream agents) ────────
        recent_surprises: list[EarningsSurprise] = []
        for s in surprise_rows[:4]:   # keep 4 most recent
            try:
                recent_surprises.append(EarningsSurprise(
                    date=s["date"],
                    eps_actual=s["eps_actual"],
                    eps_estimated=s["eps_estimated"],
                    surprise_pct=s["surprise_pct"],
                    beat=s["beat"],
                ))
            except Exception:
                continue

        # ── Build analysis note ───────────────────────────────────────────
        note_parts: list[str] = []
        if eps_dispersion_pct is not None:
            note_parts.append(
                f"EPS dispersion: {eps_dispersion_pct:.1f}% ({dispersion_label}). "
            )
        if analyst_count:
            note_parts.append(f"Analyst coverage: {analyst_count}. ")
        if surprise_rows:
            recent_beat_pcts = [f"{s['surprise_pct']:+.1f}%" for s in surprise_rows[:3]]
            note_parts.append(
                f"Last {len(recent_surprises)} surprises: {', '.join(recent_beat_pcts)}. "
            )
        if streak != 0:
            note_parts.append(
                f"Streak: {'+' if streak > 0 else ''}{streak} "
                f"({'beats' if streak > 0 else 'misses'}) in a row. "
            )
        if not note_parts:
            note_parts.append("Insufficient data for revision analysis.")

        output = AnalystRevisionOutput(
            ticker=ticker,
            revision_direction=revision_direction,
            eps_dispersion_pct=round(eps_dispersion_pct, 2) if eps_dispersion_pct is not None else None,
            revenue_dispersion_pct=round(revenue_dispersion_pct, 2) if revenue_dispersion_pct is not None else None,
            analyst_count=analyst_count,
            surprise_streak=streak,
            surprise_direction=surprise_direction,
            estimate_dispersion=dispersion_label,
            recent_surprises=recent_surprises,
            analysis_note="".join(note_parts),
        )

        results[ticker] = output.model_dump()

        print(
            f"  [AnalystRevisionAgent] {ticker} — {revision_direction} | "
            f"streak={streak:+d} | dispersion={dispersion_label} | "
            f"analysts={analyst_count}"
        )

    state["data"]["analyst_revisions"] = results
    return state
