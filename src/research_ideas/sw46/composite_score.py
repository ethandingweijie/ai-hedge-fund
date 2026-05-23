"""
src/research_ideas/sw46/composite_score.py
===========================================
100-point composite score per the Cassandra Unchained image:

  Shareholder Bucket  (cap 30)
    TA tier            up to  8 pts
    ΔE                 up to 17 pts   (guarded: 0 pts if ΣN ≤ 0)
    SBC trend          up to 10 pts

  Quality Bucket      (cap 35)
    AICT tier          up to 10 pts
    ROIC (cap @ 25%)   up to 15 pts
    Adjusted fwd rev   up to 15 pts   (fallback to raw fwd × (1 - haircut))

  Valuation Bucket    (cap 35, can be -2)
    P / IV15 brackets  35 → -2 across 11 brackets

Calibration (2026-05 vs Cassandra published table):
  - top SW46 names land 55-60 (not 90+)
  - bottom names land 10-15
  Achieved by tightening the linear-interpolation floors and removing the
  "neutral 5 pts when None" defaults on growth and SBC trend.
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.sw46.data_fetch import TickerBundle
from src.research_ideas.sw46.schemas import (
    AICTResult,
    CompositeScore,
    IV15Result,
    ROICResult,
    TragicAlgebraResult,
)


# ─── Sub-component scoring tables ─────────────────────────────────────────

_TA_TIER_POINTS = {
    "Not-TT": 8.0,
    "Near-TT": 4.0,
    "TT*": 0.0,
    "N/A": 0.0,
}

_AICT_TIER_POINTS = {
    "Fortress": 10.0,
    "Castle":    8.0,
    "Chapel":    6.0,    # softened from 5
    "Stone":     5.0,    # softened from 3 — Stone covers two distinct cases
    "Wood":      1.0,
}

# (max_p_iv15_threshold, points). Iterate in ascending order; first match wins.
_P_IV15_BRACKETS: list[tuple[float, float]] = [
    (0.50,  35.0),
    (0.75,  32.0),
    (0.90,  28.0),
    (1.00,  24.0),
    (1.25,  20.0),
    (1.50,  17.0),
    (2.00,  14.0),
    (3.00,   8.0),
    (5.00,   5.0),
    (10.00,  3.0),
]
# Anything > 10x OR negative IV15 -> -2 pts.


# ─── Sub-component scorers ────────────────────────────────────────────────


def _score_delta_e(
    delta_e: Optional[float],
    sum_net_income: Optional[float] = None,
) -> float:
    """
    0 .. 17.
    Guards:
      - ΣN ≤ 0  → 0 pts (ΔE math unstable for unprofitable windows).
      - ΔE > 1.05 → 0 pts (out-of-band; usually negative-over-negative
                            arithmetic, not real retention).

    Linear: 0 pts at dE ≤ 0.55, 17 pts at dE ≥ 1.00.
    Calibrated so PAYC-style 0.64 earns partial credit (~3.4 pts), Not-TT
    0.85 earns ~11 pts, and exceptional 1.00 hits the max.
    """
    if delta_e is None:
        return 0.0
    if sum_net_income is not None and sum_net_income <= 0:
        return 0.0
    if delta_e > 1.05:
        return 0.0
    if delta_e <= 0.55:
        return 0.0
    if delta_e >= 1.00:
        return 17.0
    return 17.0 * (delta_e - 0.55) / 0.45


def _score_sbc_trend(slope: Optional[float]) -> float:
    """
    0 .. 10. Slope is per-year change in (SBC / Revenue).
      slope >=  0.000   → 0 pts   (SBC/Rev growing at all → no credit)
      slope <= -0.005   → 10 pts  (SBC/Rev shrinking ≥0.5%/yr → max credit)
      linear in between.
    None → 0 (no penalty but no credit either — tightened from previous
            "neutral 5" default).
    """
    if slope is None:
        return 0.0
    if slope >= 0.0:
        return 0.0
    if slope <= -0.005:
        return 10.0
    return 10.0 * (-slope) / 0.005


def _score_roic(roic: Optional[float]) -> float:
    """0 .. 15. Tightened floor to 8% (was 5%). Cap at 25% → 15 pts."""
    if roic is None:
        return 0.0
    if roic <= 0.08:
        return 0.0
    if roic >= 0.25:
        return 15.0
    return 15.0 * (roic - 0.08) / 0.17


def _score_growth(growth: Optional[float]) -> float:
    """
    0 .. 15. Forward revenue growth (AICT-haircut already applied upstream).
      growth >= 30%  → 15 pts
      growth <=  5%  → 0 pts   (tightened from 0%)
      linear in between.
    None → 0 (was 5 neutral — too generous; HUBS/WDAY-style low-growth names
              must actually get docked).
    """
    if growth is None:
        return 0.0
    if growth >= 0.30:
        return 15.0
    if growth <= 0.05:
        return 0.0
    return 15.0 * (growth - 0.05) / 0.25


def _score_p_iv15(p_iv15: Optional[float]) -> float:
    if p_iv15 is None or p_iv15 < 0:
        return -2.0
    for threshold, pts in _P_IV15_BRACKETS:
        if p_iv15 <= threshold:
            return pts
    return -2.0


# ─── Public aggregator ────────────────────────────────────────────────────


def compute_composite(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
    roic: ROICResult,
    aict: AICTResult,
    iv15: IV15Result,
) -> CompositeScore:
    pts_ta_tier = _TA_TIER_POINTS.get(ta.ta_tier, 0.0)
    pts_delta_e = _score_delta_e(ta.pooled_delta_e, ta.sum_net_income)
    pts_sbc_trend = _score_sbc_trend(ta.sbc_trend)
    shareholder_raw = pts_ta_tier + pts_delta_e + pts_sbc_trend
    shareholder = min(shareholder_raw, 30.0)

    pts_aict_tier = _AICT_TIER_POINTS.get(aict.tier, 0.0)
    pts_roic = _score_roic(roic.roic)

    # Tweak 6 — growth fallback. When IV15 bails (negative-OE inflection
    # companies), fall back to raw analyst growth with the AICT haircut so
    # poor-growth companies still get docked.
    growth = iv15.growth_year1_5
    if growth is None and bundle.forward_revenue_growth is not None:
        growth = bundle.forward_revenue_growth * (1.0 - aict.growth_haircut)
    pts_growth = _score_growth(growth)

    quality_raw = pts_aict_tier + pts_roic + pts_growth
    quality = min(quality_raw, 35.0)

    p_iv15: Optional[float] = None
    if bundle.current_price and iv15.iv15_per_share and iv15.iv15_per_share > 0:
        p_iv15 = bundle.current_price / iv15.iv15_per_share
    pts_p_iv15 = _score_p_iv15(p_iv15)
    valuation = min(pts_p_iv15, 35.0)

    total = shareholder + quality + valuation

    return CompositeScore(
        shareholder_bucket=shareholder,
        quality_bucket=quality,
        valuation_bucket=valuation,
        total=total,
        pts_ta_tier=pts_ta_tier,
        pts_delta_e=pts_delta_e,
        pts_sbc_trend=pts_sbc_trend,
        pts_aict_tier=pts_aict_tier,
        pts_roic=pts_roic,
        pts_growth=pts_growth,
        pts_p_iv15=pts_p_iv15,
        p_iv15=p_iv15,
    )
