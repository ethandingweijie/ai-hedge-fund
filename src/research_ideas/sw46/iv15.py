"""
src/research_ideas/sw46/iv15.py
================================
Intrinsic-value calc with three required-return sensitivities (IV15 / IV12 /
IV18) plus the implied-LT-return solver (IVB).

Method (one common engine):
  A. Multi-stage DDM (years 1-5, 6-10, 11-15) + Gordon terminal at year 15.
  B. Same OE projection, but terminal = Year-15 OE × AICT-tier-multiple
     (no Gordon).
  Blend per AICT tier (`blend_buffett_weight`).

Base OE resolution (priority order):
  1. latest_owner_earnings  (if positive — the article's default)
  2. median_owner_earnings  (median of positive-OE years; smooths inflection
                             companies that just turned profitable)
  3. forward_margin         (latest revenue × (1 + forward growth) × tier-target
                             OE margin — for companies still in losses but with
                             a credible margin runway, per article's "Growth
                             Companies Inflecting to Profitability" section)
  4. None                   (true negative — IV undefined)

IVB: binary-search for the required return r such that IV_total(r) equals
the current market cap (price × shares). Returns the implied LT annual return.

Constants:
  - required return defaults: r in {0.12, 0.15, 0.18}
  - terminal growth (Gordon)  g_T_base = 0.03
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.sw46.data_fetch import TickerBundle
from src.research_ideas.sw46.schemas import (
    AICTResult,
    IV15Result,
    ROICResult,
    TragicAlgebraResult,
)


_DEFAULT_REQUIRED_RETURN = 0.15
_TERMINAL_G_BASE = 0.03
_MAX_GROWTH_Y1_5 = 0.30
_MIN_GROWTH_Y1_5 = -0.05

# Owner-retention determines what fraction of forward GAAP NI accrues to
# shareholders after SBC drag. Three sources, in order of fidelity:
#
#  1. Use the company's actual pooled ΔE when it's in a "trustworthy" band
#     (0.20 .. 1.05). This is the most accurate per-company signal — the
#     Tragic Algebra calculation is bespoke to the company's history.
#  2. For TT* (negative-ΔE) names, use a TIGHT tier floor. These companies
#     have demonstrated heavy SBC abuse; forward NI alone is not credible.
#  3. For unknown / weird-positive ΔE (inflection companies), default to a
#     middle tier-keyed retention.
_DEFAULT_RETENTION = {
    "Fortress": 0.85, "Castle": 0.70, "Chapel": 0.55, "Stone": 0.40, "Wood": 0.20,
}
_TT_RETENTION = {  # used when pooled ΔE ≤ 0 (Tragic Tier — known SBC abuser)
    "Fortress": 0.50, "Castle": 0.35, "Chapel": 0.25, "Stone": 0.18, "Wood": 0.10,
}

# Extra growth haircut layered ON TOP of AICT haircut, keyed by TA tier.
# TT* names get a deep growth cut — Cassandra docks them for SBC abuse
# trajectories that imply margins won't actually arrive.
_TA_GROWTH_HAIRCUT = {
    "Not-TT": 0.00,
    "Near-TT": 0.20,
    "TT*":    0.50,
    "N/A":    0.10,
}


def _owner_retention(ta: TragicAlgebraResult, aict: AICTResult) -> float:
    """
    Resolve owner-retention by TA tier (NOT by raw ΔE value alone):

      - Pooled ΔE out-of-band (None / ≤0 / >1.05): math is unreliable
        (typically TT* with negative-over-negative, or inflection companies
        with weird ΔE arithmetic). Fall to the tight TT_RETENTION tier
        floor — Cassandra explicitly treats these as Tragic Tier.
      - TT*  → TT_RETENTION tier floor.
      - Near-TT → min(ΔE + 0.20, 0.85), floored at TT_RETENTION. Additive
        uplift (not multiplicative) so PAYC-style 0.64 gets 0.84.
      - Not-TT → full ΔE retention (clean SBC discipline earns trust).
    """
    tier = ta.ta_tier
    pooled = ta.pooled_delta_e

    # Out-of-band ΔE — treat as TT* regardless of nominal tier
    if pooled is None or pooled <= 0 or pooled > 1.05:
        return _TT_RETENTION.get(aict.tier, 0.25)

    if tier == "TT*":
        return _TT_RETENTION.get(aict.tier, 0.25)
    if tier == "Near-TT":
        floor = _TT_RETENTION.get(aict.tier, 0.25)
        return max(floor, min(pooled + 0.20, 0.85))
    if tier == "Not-TT":
        return pooled
    # N/A / Inflection — fall to default tier retention
    return _DEFAULT_RETENTION.get(aict.tier, 0.55)


def _resolve_base_oe(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
    aict: AICTResult,
) -> tuple[Optional[float], str]:
    """
    Returns (base_oe, source_label). source_label in {"latest","median",
    "forward_ni","none"}.

    Priority:
      1. Forward NI × owner-retention IF forward NI is positive AND larger
         than the latest year's OE (forward-looking valuation basis).
      2. Latest OE if positive (TTM-anchored fallback).
      3. Median of positive-OE years (smooths inflection).
      4. Forward NI × retention even if smaller (last-resort positive).
      5. None.
    """
    fwd_ni = bundle.forward_net_income
    retention = _owner_retention(ta, aict)
    fwd_base = (fwd_ni * retention) if (fwd_ni is not None and fwd_ni > 0) else None

    latest_oe = ta.latest_owner_earnings
    median_oe = ta.median_owner_earnings

    if fwd_base is not None:
        if latest_oe is None or fwd_base > (latest_oe or 0):
            return fwd_base, "forward_ni"
    if latest_oe is not None and latest_oe > 0:
        return latest_oe, "latest"
    if median_oe is not None and median_oe > 0:
        return median_oe, "median"
    if fwd_base is not None:
        return fwd_base, "forward_ni"
    return None, "none"


def _stage_growth(
    bundle: TickerBundle,
    roic: ROICResult,
    aict: AICTResult,
    ta: TragicAlgebraResult,
) -> tuple[float, float, float]:
    """Return (g1_5, g6_10, g11_15) after AICT + TA-tier haircuts."""
    fwd = bundle.forward_revenue_growth
    roic_growth = roic.roic if roic.roic and roic.roic > 0 else None

    if fwd is None and roic_growth is None:
        g1_5_raw = 0.05
    elif fwd is None:
        g1_5_raw = min(roic_growth or 0.05, _MAX_GROWTH_Y1_5)
    elif roic_growth is None:
        g1_5_raw = min(max(fwd, _MIN_GROWTH_Y1_5), _MAX_GROWTH_Y1_5)
    else:
        g1_5_raw = min(min(fwd, roic_growth), _MAX_GROWTH_Y1_5)

    # Stack AICT haircut + TA-tier haircut (multiplicative).
    # TT* names get a deep cut — Cassandra docks them for SBC trajectories
    # that imply forecast margins won't actually arrive.
    ta_haircut = _TA_GROWTH_HAIRCUT.get(ta.ta_tier, 0.10)
    combined_haircut_factor = (1.0 - aict.growth_haircut) * (1.0 - ta_haircut)
    g1_5 = max(g1_5_raw * combined_haircut_factor, _MIN_GROWTH_Y1_5)

    g6_10 = _TERMINAL_G_BASE + 0.40 * (g1_5 - _TERMINAL_G_BASE)
    g11_15 = _TERMINAL_G_BASE
    return g1_5, g6_10, g11_15


def compute_iv(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
    roic: ROICResult,
    aict: AICTResult,
    required_return: float = _DEFAULT_REQUIRED_RETURN,
) -> IV15Result:
    """Intrinsic value at the requested annualised required return."""
    base_oe, src = _resolve_base_oe(bundle, ta, aict)
    if base_oe is None or base_oe <= 0:
        return IV15Result(
            base_oe=base_oe,
            base_oe_source=src,
            required_return_used=required_return,
            shares_outstanding=bundle.shares_outstanding,
        )

    g1_5, g6_10, g11_15 = _stage_growth(bundle, roic, aict, ta)

    # Project OE year-by-year.
    oe_series: list[float] = []
    cur = base_oe
    for yr in range(1, 16):
        if yr <= 5:
            cur *= 1.0 + g1_5
        elif yr <= 10:
            cur *= 1.0 + g6_10
        else:
            cur *= 1.0 + g11_15
        oe_series.append(cur)

    r = required_return
    discount = [(1.0 + r) ** yr for yr in range(1, 16)]
    pv_oe = sum(oe / d for oe, d in zip(oe_series, discount))

    # Valuation A — DDM with Gordon terminal at year 15.
    g_T = _TERMINAL_G_BASE
    oe_15 = oe_series[-1]
    if r - g_T <= 0:
        tv_gordon = oe_15 * 25.0
    else:
        tv_gordon = oe_15 * (1.0 + g_T) / (r - g_T)
    pv_tv_gordon = tv_gordon / discount[-1]
    iv_ddm = pv_oe + pv_tv_gordon

    # Valuation B — Buffett: terminal = OE_15 × AICT-tier-multiple.
    tv_buffett = oe_15 * aict.terminal_multiple
    pv_tv_buffett = tv_buffett / discount[-1]
    iv_buffett = pv_oe + pv_tv_buffett

    # Blend per AICT tier.
    w_b = aict.blend_buffett_weight
    iv_total = (1.0 - w_b) * iv_ddm + w_b * iv_buffett

    shares = bundle.shares_outstanding
    iv_per_share = (iv_total / shares) if (shares and shares > 0) else None

    return IV15Result(
        iv15_total=iv_total,
        iv15_per_share=iv_per_share,
        iv15_ddm_total=iv_ddm,
        iv15_buffett_total=iv_buffett,
        base_oe=base_oe,
        base_oe_source=src,
        required_return_used=required_return,
        growth_year1_5=g1_5,
        growth_year6_10=g6_10,
        growth_year11_15=g11_15,
        terminal_multiple_used=aict.terminal_multiple,
        shares_outstanding=shares,
    )


def compute_iv15(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
    roic: ROICResult,
    aict: AICTResult,
) -> IV15Result:
    """Back-compat: defaults to 15% required return."""
    return compute_iv(bundle, ta, roic, aict, required_return=_DEFAULT_REQUIRED_RETURN)


def solve_ivb(
    bundle: TickerBundle,
    ta: TragicAlgebraResult,
    roic: ROICResult,
    aict: AICTResult,
    lo: float = 0.001,
    hi: float = 0.30,
    tol: float = 1e-3,
    max_iter: int = 50,
) -> Optional[float]:
    """
    Solve for the required return r such that IV_total(r) == current market cap.

    Returns the implied LT annualised return at current price.
      - lo (0.1%)  if IV at lo is still below price (market implies near-zero return)
      - hi (30%)   if IV at hi is still above price (market implies > 30% return)
      - None       if shares / price missing, or base_oe is None at any try

    IV is monotonically DECREASING in r, so:
      IV(lo) >  target  > IV(hi)   normal interior case
    """
    if not bundle.current_price or not bundle.shares_outstanding:
        return None
    target = bundle.current_price * bundle.shares_outstanding

    def iv_total(rr: float) -> Optional[float]:
        res = compute_iv(bundle, ta, roic, aict, required_return=rr)
        return res.iv15_total

    iv_at_lo = iv_total(lo)
    iv_at_hi = iv_total(hi)
    if iv_at_lo is None or iv_at_hi is None:
        return None
    if iv_at_lo < target:
        return lo  # even 0.1% RR can't justify the price
    if iv_at_hi > target:
        return hi  # 30%+ implied return — clip ceiling

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        iv_mid = iv_total(mid)
        if iv_mid is None:
            return None
        if abs(iv_mid - target) / target < tol:
            return mid
        if iv_mid > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
