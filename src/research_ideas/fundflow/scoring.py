"""
src/research_ideas/fundflow/scoring.py
=======================================
Three-dimension SIGNED flow scorer — the Sectors (US) momentum engine's shape,
applied to money instead of price.

  PRESSURE — where flow stands now (de-biased CMF, MFI, net-flow z-score)
  TURN     — is it inflecting (de-biased CMF crossing its own baseline, fast/
             slow CMF cross, de-meaned accumulation line reclaiming its MA —
             fresh crosses only)
  ACCEL    — is it strengthening (rising flow pressure, turnover surge,
             accumulation slope, breadth of up-flow sessions)

Each pillar scores in [-2, +2]; composite = sum in [-6, +6]. Positive means
money is arriving, negative means it is leaving.

Two calibration decisions worth stating, because a naive version of this file
produced a screen on which eight of nine geographies printed +6:

  1. Every directional input is measured against the region's OWN trailing
     year, not against a fixed level. Regional equity ETFs are highly
     correlated and all carry an upward drift, so absolute readings cluster
     into a band too narrow to rank. Deviations from each region's baseline
     do not.
  2. Bands sit near +/-0.5 standard deviations. Tighter and the pillars
     saturate on ordinary variation; wider and genuine turns arrive too late
     to act on.

Tape-derived flow is NEVER divided by assets. It is a conviction-weighted
share of turnover, not a creation/redemption ledger, and dividing it by AUM
produces figures like "Korea +26% of assets this month" that are off by orders
of magnitude. Percent-of-assets is reported only for `implied_flow_*`, which
is the measured share-count series.

  score_flow_series(rs) -> dict
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.fundflow import indicators as fi
from src.research_ideas.fundflow.indicators import RegionSeries


_TURN_LOOKBACK = 10        # bars: an inflection must be this fresh to count

_Z_BAND = 0.5              # standard deviations off a region's own baseline
_MFI_HI, _MFI_LO = 55.0, 45.0
_SURGE_BAND = 1.15         # 21d turnover 15% above its 63d norm
_BREADTH_HI, _BREADTH_LO = 0.60, 0.40
_SLOPE_BAND = 0.10


def _clamp_net(up: int, down: int) -> int:
    net = up - down
    if net >= 2:
        return 2
    if net == 1:
        return 1
    if net == -1:
        return -1
    if net <= -2:
        return -2
    return 0


# ─── Pillar 1: PRESSURE ─────────────────────────────────────────────────────


def _score_pressure(cmfz: Optional[float], mfi: Optional[float],
                    flowz: Optional[float]) -> tuple[float, list[str]]:
    notes: list[str] = []
    up = down = 0

    if cmfz is not None:
        if cmfz > _Z_BAND:
            up += 1
        elif cmfz < -_Z_BAND:
            down += 1
    if mfi is not None:
        if mfi > _MFI_HI:
            up += 1
        elif mfi < _MFI_LO:
            down += 1
    if flowz is not None:
        if flowz > _Z_BAND:
            up += 1
        elif flowz < -_Z_BAND:
            down += 1

    score = _clamp_net(up, down)
    if score != 0:
        word = "Accumulation" if score > 0 else "Distribution"
        notes.append(
            f"{word} pressure — flow running {_num(cmfz, 1)}σ off this region's own "
            f"baseline (MFI {_num(mfi, 0)}, 1m net flow {_num(flowz, 1)}σ vs its normal month)"
        )
    return float(score), notes


# ─── Pillar 2: TURN ─────────────────────────────────────────────────────────


def _score_turn(base_x: dict, fast_x: dict, line_x: dict) -> tuple[float, Optional[int], list[str]]:
    notes: list[str] = []
    base_dir = base_x.get("direction", 0)
    fast_dir = fast_x.get("direction", 0)
    line_dir = line_x.get("direction", 0)

    # Crossing the region's own flow baseline is the real regime change. The
    # fast/slow cross and the accumulation-line reclaim fire earlier and more
    # often, so they confirm at half weight rather than triggering alone.
    raw = base_dir * 1.0 + fast_dir * 0.5 + line_dir * 0.5

    if raw >= 1.0:
        score = 2.0
    elif raw >= 0.5:
        score = 1.0
    elif raw <= -1.0:
        score = -2.0
    elif raw <= -0.5:
        score = -1.0
    else:
        score = 0.0

    days_since = None
    for src in (base_x.get("days_since_cross"), fast_x.get("days_since_cross"),
                line_x.get("days_since_cross")):
        if src is not None:
            days_since = src if days_since is None else min(days_since, src)

    if score != 0:
        bullish = score > 0
        bits = []
        if (base_dir > 0) if bullish else (base_dir < 0):
            bits.append(f"flow crossed {'above' if bullish else 'below'} its own baseline")
        if (fast_dir > 0) if bullish else (fast_dir < 0):
            bits.append(f"5d flow crossed {'above' if bullish else 'below'} 21d")
        if (line_dir > 0) if bullish else (line_dir < 0):
            bits.append(f"accumulation line {'reclaimed' if bullish else 'lost'} its 21d average")
        notes.append(
            ("Inflow turn: " if bullish else "Outflow turn: ")
            + ", ".join(bits) + _fresh(days_since)
        )
    return score, days_since, notes


# ─── Pillar 3: ACCEL ────────────────────────────────────────────────────────


def _score_accel(cmfz_delta: Optional[float], surge: Optional[float],
                 slope: Optional[float], breadth: Optional[float],
                 cmfz: Optional[float]) -> tuple[float, list[str]]:
    notes: list[str] = []
    up = down = 0

    if cmfz_delta is not None:
        if cmfz_delta > _Z_BAND:
            up += 1
        elif cmfz_delta < -_Z_BAND:
            down += 1
    if slope is not None:
        if slope > _SLOPE_BAND:
            up += 1
        elif slope < -_SLOPE_BAND:
            down += 1
    if breadth is not None:
        if breadth > _BREADTH_HI:
            up += 1
        elif breadth < _BREADTH_LO:
            down += 1
    # A turnover surge is directionless on its own — more money changing hands
    # only counts as acceleration once the prevailing flow direction says which
    # way that extra money is going.
    if surge is not None and surge > _SURGE_BAND and cmfz is not None:
        if cmfz > 0:
            up += 1
        elif cmfz < 0:
            down += 1

    score = _clamp_net(up, down)
    if score != 0:
        word = "Inflows accelerating" if score > 0 else "Outflows accelerating"
        bits = [f"flow pressure {_num(cmfz_delta, 1)}σ over 1m"]
        if breadth is not None:
            bits.append(f"{breadth * 100:.0f}% of sessions net-{'buy' if score > 0 else 'sell'}")
        if surge is not None:
            bits.append(f"turnover {surge:.2f}× normal")
        notes.append(f"{word} ({', '.join(bits)})")
    return float(score), notes


# ─── Verdict ────────────────────────────────────────────────────────────────


def derive_verdict(pressure: float, turn: float, accel: float, composite: float) -> str:
    """
    Mirrors the momentum engine's logic: a fresh inflection that contradicts
    or neutralises the standing state is the headline, unless the move is
    already deep AND accelerating, in which case "Accelerating" is the more
    honest label than "Turning".
    """
    fresh_in = turn >= 1
    fresh_out = turn <= -1

    if fresh_in and accel >= 0 and pressure <= 1:
        if composite >= 4 and accel >= 1:
            return "Accelerating-Inflow"
        return "Turning-Inflow"
    if fresh_out and accel <= 0 and pressure >= -1:
        if composite <= -4 and accel <= -1:
            return "Accelerating-Outflow"
        return "Turning-Outflow"
    if composite >= 4 and accel >= 1:
        return "Accelerating-Inflow"
    if composite <= -4 and accel <= -1:
        return "Accelerating-Outflow"
    return "Neutral"


def _verdict_direction(verdict: str) -> str:
    if verdict.endswith("Inflow"):
        return "INFLOW"
    if verdict.endswith("Outflow"):
        return "OUTFLOW"
    return "NEUTRAL"


# ─── Entry point ────────────────────────────────────────────────────────────


def score_flow_series(rs: RegionSeries) -> dict:
    """
    Compute the three signed pillars + composite + verdict for one region's
    aggregated dollar series. Pure function of `rs` — the runner layers the
    price overlay, rotation and prior-period snapshots on top.
    """
    flow, dvol = rs.flow, rs.dvol

    cmf21 = fi.cmf(flow, dvol, 21)
    cmf5 = fi.cmf(flow, dvol, 5)
    cmfz = fi.cmf_z(flow, dvol, 21)
    cmfz_d = fi.cmf_z_delta(flow, dvol, 21, 21)
    mfi = fi.money_flow_index(flow, 14)
    breadth = fi.flow_breadth(flow, 21)
    surge = fi.turnover_surge(dvol, 21, 63)
    flowz = fi.flow_z_score(flow, 21)
    slope = fi.flow_slope(flow, 21)

    base_x = fi.cmf_baseline_cross(flow, dvol, 21, _TURN_LOOKBACK)
    fast_x = fi.short_vs_long_cross(flow, dvol, 5, 21, _TURN_LOOKBACK)
    line_x = fi.line_vs_ma(fi.cumulative_flow_line(flow, demean=True), 21, _TURN_LOOKBACK)

    pressure, p_notes = _score_pressure(cmfz, mfi, flowz)
    turn, days_since, t_notes = _score_turn(base_x, fast_x, line_x)
    accel, a_notes = _score_accel(cmfz_d, surge, slope, breadth, cmfz)

    composite = pressure + turn + accel
    verdict = derive_verdict(pressure, turn, accel, composite)

    # Measured creation/redemption — the only series here that is an
    # observation rather than an estimate, and the only one it is legitimate
    # to express as a percentage of assets.
    # Multi-horizon reads. The composite is still scored on the 1-month window
    # — that is the horizon a flow signal is actionable over — but a month in
    # isolation cannot distinguish a genuine regime change from a wobble
    # inside a year-long trend, so the same measures are reported over 3, 6
    # and 12 months alongside it.
    windows = {"63": 63, "126": 126, "252": 252}
    multi: dict = {}
    for suffix, w in windows.items():
        multi[f"cmf_{suffix}"] = fi.cmf(flow, dvol, w)
        multi[f"cmf_z_{suffix}"] = fi.cmf_z(flow, dvol, w)
        multi[f"tape_flow_{suffix}d"] = fi.net_flow(flow, w)

    implied: dict = {}
    for w in (21, 63, 126, 252):
        v = fi.net_flow(rs.implied, w) if rs.implied is not None else None
        if rs.implied_quality == "stale":
            v = None
        implied[f"implied_flow_{w}d"] = v
        implied[f"implied_flow_{w}d_pct_aum"] = (v / rs.aum) if (v is not None and rs.aum) else None

    return {
        "cmf_21": cmf21,
        "cmf_5": cmf5,
        "cmf_z_21": cmfz,
        "cmf_z_delta_21": cmfz_d,
        "mfi_14": mfi,
        "tape_flow_5d": fi.net_flow(flow, 5),
        "tape_flow_21d": fi.net_flow(flow, 21),
        "avg_daily_turnover": float(dvol.iloc[-21:].mean()) if len(dvol) >= 21 else None,
        **multi,
        **implied,
        "flow_breadth_21": breadth,
        "turnover_surge": surge,
        "flow_z_21": flowz,
        "flow_slope_21": slope,
        "days_since_turn": days_since,
        "pressure_score": pressure,
        "turn_score": turn,
        "accel_score": accel,
        "composite": float(composite),
        "signal_strength": round(abs(composite) / 6.0 * 100.0, 1),
        "verdict": verdict,
        "direction": _verdict_direction(verdict),
        "passes_gate": verdict != "Neutral",
        "implied_quality": rs.implied_quality,
        "flag_notes": p_notes + t_notes + a_notes,
    }


def classify_divergence(flow_composite: float,
                        price_composite: Optional[float]) -> Optional[str]:
    """
    Compare the money signal with the price signal.

      confirming   — both point the same way; the move has funding behind it.
      flow-leads   — money is moving before price has. The early read, and the
                     reason a flow screen earns its keep.
      price-leads  — price has moved without the flow to back it. Rallies on
                     thinning accumulation and selloffs into steady bids both
                     land here; treat the price move as the less-supported one.
    """
    if price_composite is None:
        return None
    f = (flow_composite > 0) - (flow_composite < 0)
    p = (price_composite > 0) - (price_composite < 0)
    if f == 0 and p == 0:
        return None
    if f == p:
        return "confirming"
    if f != 0 and abs(flow_composite) >= abs(price_composite):
        return "flow-leads"
    return "price-leads"


def compose_justification(label: str, verdict: str, composite: float,
                          cmfz: Optional[float], notes: list[str]) -> str:
    headline = {
        "Accelerating-Inflow": "Inflows accelerating",
        "Turning-Inflow": "Turning to inflow — money starting to arrive",
        "Neutral": "No actionable flow signal",
        "Turning-Outflow": "Turning to outflow — money starting to leave",
        "Accelerating-Outflow": "Outflows accelerating",
    }[verdict]
    scale = f", flow pressure {cmfz:+.1f}σ vs its own baseline" if cmfz is not None else ""
    flag_str = "; ".join(notes[:4]) if notes else "no individual flags surfaced"
    return f"{label}: {headline} (flow composite {composite:+.0f}/6{scale}). {flag_str}."


# ─── formatting helpers ─────────────────────────────────────────────────────


def _num(x: Optional[float], digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:+.{digits}f}"


def _fresh(days_since: Optional[int]) -> str:
    if days_since is None:
        return ""
    if days_since == 0:
        return " (today)"
    return f" ({days_since}d ago)"
