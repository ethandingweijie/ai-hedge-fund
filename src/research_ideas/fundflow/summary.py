"""
src/research_ideas/fundflow/summary.py
=======================================
The top-of-page brief: what money did, what changed, and what it implies.

Deliberately RULE-BASED rather than LLM-written. The summary sits above the
table restating numbers the reader can check one scroll below it, so being
reproducible and instant matters more than being eloquent — and a stray
hallucinated figure in the headline would poison trust in the whole screen.
Every sentence below is a direct rendering of a scored field.

Two vocabularies are kept strictly apart, because conflating them is the
easiest way to make this page lie:

  "flow pressure"  — tape-derived, in sigma off a region's own baseline.
                     Available for every region, every day. Never in dollars.
  "issuer flow"    — measured creation/redemption in dollars and percent of
                     assets. Real money, but null wherever the share-count
                     feed is stale.

  build_summary(regions, benchmarks) -> FundFlowSummary
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.fundflow.schemas import FundFlowRegionResult, FundFlowSummary


_STRONG_REL = 0.5          # sigma of excess flow vs the world that counts
_BIG_ROTATION = 0.75       # sigma change in relative flow that counts as rotation
_FRESH_TURN_DAYS = 5


def _usd(x: Optional[float]) -> str:
    """Compact dollar formatting — flows span $10m to $50bn in one table."""
    if x is None:
        return "n/a"
    a = abs(x)
    sign = "-" if x < 0 else "+"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}bn"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f}m"
    return f"{sign}${a / 1e3:.0f}k"


def _pct(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def _sig(x: Optional[float], digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:+.{digits}f}σ"


def _name(r: FundFlowRegionResult) -> str:
    return f"{r.emoji} {r.label}".strip() if r.emoji else r.label


def build_summary(regions: list[FundFlowRegionResult],
                  benchmarks: list[FundFlowRegionResult]) -> FundFlowSummary:
    tracked = [r for r in regions if not r.is_benchmark]
    if not tracked:
        return FundFlowSummary(headline="No fund-flow data available.", regime="unknown")

    world = next((b for b in benchmarks if b.region == "WORLD"), None)

    # Ranked by flow PRESSURE, not dollars: the biggest dollar figure is
    # always the US simply because it is the biggest market, which tells the
    # reader nothing they did not already know.
    by_pressure = sorted(
        [r for r in tracked if r.cmf_z_21 is not None],
        key=lambda r: -(r.cmf_z_21 or 0),
    )
    gaining = [r for r in by_pressure if (r.cmf_z_21 or 0) > 0]
    losing = [r for r in reversed(by_pressure) if (r.cmf_z_21 or 0) < 0]

    measured = [r for r in tracked if r.implied_flow_21d is not None]
    net_implied = sum(r.implied_flow_21d or 0.0 for r in measured) if measured else None

    inflow_n = sum(1 for r in tracked if r.direction == "INFLOW")
    outflow_n = sum(1 for r in tracked if r.direction == "OUTFLOW")

    regime = _regime(tracked, world)
    headline = _headline(gaining, losing, net_implied, len(measured), len(tracked))

    return FundFlowSummary(
        headline=headline,
        regime=regime,
        net_implied_flow_21d=net_implied,
        implied_coverage=len(measured),
        inflow_count=inflow_n,
        outflow_count=outflow_n,
        key_flows=_key_flows(gaining, losing, tracked, measured),
        key_changes=_key_changes(tracked),
        implications=_implications(tracked, world, regime),
        watch_items=_watch_items(tracked),
    )


# ─── Regime + headline ──────────────────────────────────────────────────────


def _regime(tracked: list[FundFlowRegionResult],
            world: Optional[FundFlowRegionResult]) -> str:
    """
    Characterise the tape in one phrase. The distinction that matters is
    whether money is entering or leaving equities as a whole, or merely moving
    between geographies — those call for completely different responses. The
    global benchmark answers the first question; the spread of the individual
    regions around it answers the second.
    """
    n = len(tracked)
    pos = sum(1 for r in tracked if (r.cmf_z_21 or 0) > 0)
    neg = n - pos
    world_z = world.cmf_z_21 if world else None

    if world_z is not None and world_z > _STRONG_REL and pos >= n * 0.6:
        return "Broad risk-on — money entering equities globally, most geographies participating"
    if world_z is not None and world_z < -_STRONG_REL and neg >= n * 0.6:
        return "Broad de-risking — money leaving equities globally, few places to hide"
    if pos >= 3 and neg >= 3:
        return "Rotational — money is moving between geographies, not into or out of equities as a whole"
    if pos > neg:
        return "Selective risk-on — accumulation concentrated in a few geographies"
    if neg > pos:
        return "Selective de-risking — distribution concentrated in a few geographies"
    return "Balanced — no dominant direction across the tracked map"


def _headline(gaining: list[FundFlowRegionResult], losing: list[FundFlowRegionResult],
              net_implied: Optional[float], measured_n: int, total_n: int) -> str:
    parts: list[str] = []
    if gaining:
        g = gaining[0]
        parts.append(f"{_name(g)} is drawing the strongest bid at {_sig(g.cmf_z_21)} above its own normal")
    if losing:
        l = losing[0]
        parts.append(f"{_name(l)} is seeing the heaviest selling at {_sig(l.cmf_z_21)}")
    if not parts:
        return "Flows are flat across the tracked geographies over the past month."

    tail = ""
    if net_implied is not None and measured_n:
        tail = (
            f" Measured issuer flows across the {measured_n} of {total_n} geographies "
            f"with a live share-count feed net to {_usd(net_implied)}."
        )
    return f"Over the past month, {' while '.join(parts)}.{tail}"


# ─── Sections ───────────────────────────────────────────────────────────────


def _key_flows(gaining: list[FundFlowRegionResult], losing: list[FundFlowRegionResult],
               tracked: list[FundFlowRegionResult],
               measured: list[FundFlowRegionResult]) -> list[str]:
    out: list[str] = []

    for r in gaining[:3] + losing[:3]:
        out.append(
            f"{_name(r)}: flow pressure {_sig(r.cmf_z_21)} over 1M "
            f"(3M {_sig(r.cmf_z_63)}, 6M {_sig(r.cmf_z_126)}, 1Y {_sig(r.cmf_z_252)}), "
            f"{_sig(r.rel_flow_z)} vs the world — {r.verdict.replace('-', ' ').lower()}"
        )

    # The measured ledger, quoted separately so it is never mistaken for the
    # tape signal above.
    if measured:
        ranked = sorted(measured, key=lambda r: -(r.implied_flow_21d or 0))
        top, bottom = ranked[0], ranked[-1]
        if (top.implied_flow_21d or 0) > 0:
            out.append(
                f"Largest measured issuer inflow: {_name(top)} at {_usd(top.implied_flow_21d)} "
                f"({_pct(top.implied_flow_21d_pct_aum, 2)} of basket assets)"
            )
        if (bottom.implied_flow_21d or 0) < 0:
            out.append(
                f"Largest measured issuer outflow: {_name(bottom)} at {_usd(bottom.implied_flow_21d)} "
                f"({_pct(bottom.implied_flow_21d_pct_aum, 2)} of basket assets)"
            )
    return out


def _key_changes(tracked: list[FundFlowRegionResult]) -> list[str]:
    out: list[str] = []

    # Composite crossing zero versus a month ago is the single most
    # decision-relevant change on the page.
    for r in tracked:
        if r.composite_1m is None:
            continue
        now_sign = (r.composite > 0) - (r.composite < 0)
        then_sign = (r.composite_1m > 0) - (r.composite_1m < 0)
        if now_sign != 0 and then_sign != 0 and now_sign != then_sign:
            out.append(
                f"{_name(r)} flipped from {'inflow' if then_sign > 0 else 'outflow'} to "
                f"{'inflow' if now_sign > 0 else 'outflow'} "
                f"(flow composite {r.composite_1m:+.0f} → {r.composite:+.0f})"
            )

    # Rotation: biggest movers in flow strength RELATIVE to the world.
    movers = sorted(
        [r for r in tracked if r.rel_flow_z_delta is not None],
        key=lambda r: -abs(r.rel_flow_z_delta),
    )
    for r in movers[:3]:
        if abs(r.rel_flow_z_delta) < 0.4:
            break
        out.append(
            f"{_name(r)} {'gained' if r.rel_flow_z_delta > 0 else 'lost'} "
            f"{abs(r.rel_flow_z_delta):.1f}σ of relative flow versus the world over the month "
            f"({_sig(r.rel_flow_z_1m)} → {_sig(r.rel_flow_z)})"
        )

    # Where the short and long horizons DISAGREE. A region positive on every
    # window is a standing regime and not news; one whose month contradicts
    # its half-year is where a turn is actually happening.
    for r in tracked:
        short, long_ = r.cmf_z_21, r.cmf_z_126
        if short is None or long_ is None:
            continue
        if (short > 0.3 and long_ < -0.3) or (short < -0.3 and long_ > 0.3):
            out.append(
                f"{_name(r)}: the month contradicts the half-year — flow pressure "
                f"{_sig(short)} over 1M against {_sig(long_)} over 6M"
                + (f" (1Y {_sig(r.cmf_z_252)})" if r.cmf_z_252 is not None else "")
                + ". Either a genuine turn or a wobble inside the longer trend"
            )

    # Fresh inflections that the monthly comparison has not caught up with.
    for r in tracked:
        if r.turn_score != 0 and r.days_since_turn is not None and r.days_since_turn <= _FRESH_TURN_DAYS:
            out.append(
                f"{_name(r)}: fresh {'inflow' if r.turn_score > 0 else 'outflow'} inflection "
                f"{'today' if r.days_since_turn == 0 else f'{r.days_since_turn} sessions ago'} — "
                f"{r.verdict.replace('-', ' ').lower()}"
            )

    return out or ["No material change in the flow map versus a month ago."]


def _implications(tracked: list[FundFlowRegionResult],
                  world: Optional[FundFlowRegionResult],
                  regime: str) -> list[str]:
    out: list[str] = []

    if regime.startswith("Rotational"):
        gainers = sorted([r for r in tracked if (r.rel_flow_z_delta or 0) > 0],
                         key=lambda r: -(r.rel_flow_z_delta or 0))
        losers = sorted([r for r in tracked if (r.rel_flow_z_delta or 0) < 0],
                        key=lambda r: (r.rel_flow_z_delta or 0))
        if gainers and losers and abs(losers[0].rel_flow_z_delta or 0) >= _BIG_ROTATION:
            out.append(
                f"The dominant trade is a rotation out of {_name(losers[0])} and into "
                f"{_name(gainers[0])}. A relative-value pair expresses this more cleanly "
                f"than an outright position, since the aggregate bid for equities is "
                f"roughly unchanged."
            )
    elif regime.startswith("Broad risk-on"):
        out.append(
            "Money is entering equities broadly rather than rotating between them. "
            "Beta is doing the work — geography selection adds less here than it does "
            "in a rotational tape."
        )
    elif regime.startswith("Broad de-risking"):
        out.append(
            "Money is leaving equities broadly. Defensive geographies will fall less "
            "but are unlikely to rise; the decision that matters is gross exposure, "
            "not which geography to hold."
        )

    # Flow leading price is the reason this screen exists — surface it loudly.
    for r in [x for x in tracked if x.divergence == "flow-leads" and abs(x.composite) >= 3][:2]:
        out.append(
            f"{_name(r)}: money is moving ahead of price (flow {r.composite:+.0f} vs "
            f"price {r.price_composite:+.0f}). "
            + ("Accumulation is building before the price has re-rated — the early entry window."
               if r.composite > 0 else
               "Distribution is under way while price still holds up — the exit is being taken quietly.")
        )

    for r in [x for x in tracked if x.divergence == "price-leads" and abs(x.price_composite or 0) >= 3][:2]:
        out.append(
            f"{_name(r)}: price has moved without the flow to fund it "
            f"(price {r.price_composite:+.0f} vs flow {r.composite:+.0f}). "
            + ("A rally on thinning accumulation is the more fragile kind — size accordingly."
               if (r.price_composite or 0) > 0 else
               "A selloff into steady buying often marks capitulation rather than a trend.")
        )

    # Relative flow decides whether a positive regional reading is a genuine
    # preference or just the global tide.
    ranked_rel = sorted([r for r in tracked if r.rel_flow_z is not None],
                        key=lambda r: -(r.rel_flow_z or 0))
    if ranked_rel:
        best, worst = ranked_rel[0], ranked_rel[-1]
        if (best.rel_flow_z or 0) > _STRONG_REL:
            out.append(
                f"{_name(best)} is drawing flow {_sig(best.rel_flow_z)} ABOVE the global "
                f"benchmark — a genuine allocation preference, not just the global tide "
                f"lifting everything."
            )
        if (worst.rel_flow_z or 0) < -_STRONG_REL:
            out.append(
                f"{_name(worst)} is lagging the global benchmark by {_sig(worst.rel_flow_z)} "
                f"— being actively underweighted, not merely overlooked."
            )

    # Currency is frequently the whole story in Japan and Europe.
    for r in tracked:
        if r.fx_drag_21d is not None and abs(r.fx_drag_21d) > 0.02:
            out.append(
                f"{_name(r)}: currency moved the 1m USD return by "
                f"{r.fx_drag_21d * 100:+.1f}pp. A hedged vehicle would have delivered a "
                f"materially different outcome from the unhedged one."
            )

    return out or ["No strong directional implication — the flow map is balanced."]


def _watch_items(tracked: list[FundFlowRegionResult]) -> list[str]:
    out: list[str] = []

    # Regions on the fence — most likely to be a different story next week.
    for r in tracked:
        if abs(r.composite) <= 1 and r.cmf_z_21 is not None and abs(r.cmf_z_21) < 0.3:
            out.append(
                f"{_name(r)} is balanced (flow pressure {_sig(r.cmf_z_21)}, composite "
                f"{r.composite:+.0f}) — no edge either way yet; a baseline cross here "
                f"would be the first signal"
            )

    # Attention without direction usually precedes a move rather than being one.
    for r in tracked:
        if (r.turnover_surge or 0) > 1.25 and abs(r.composite) <= 2:
            out.append(
                f"{_name(r)}: turnover running {r.turnover_surge:.2f}× normal without a "
                f"clear flow direction — contested tape, resolution likely soon"
            )

    stale = [r for r in tracked if r.implied_quality in ("stale", "none")]
    if stale:
        out.append(
            "Issuer share-count feed is stale for "
            + ", ".join(r.label for r in stale)
            + " — the measured creation/redemption column reads n/a for those rows. "
            "The tape-derived flow pressure that drives the scores is unaffected."
        )

    return out
