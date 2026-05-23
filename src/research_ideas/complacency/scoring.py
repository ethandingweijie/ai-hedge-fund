"""
src/research_ideas/complacency/scoring.py
==========================================
Four-pillar Complacency scorer (spec §3.1).

  Pillar          Strong (2pt)                 Weak (1pt)
  ─────────────  ───────────────────────────  ───────────────────────────
  Valuation       EV/Sales > 2.5× sector med   > 1.5×
                  OR EV/Sales > 25× absolute   > 15×
                  AND FCF yield < -0.5%        < 1.5%
  Behavioral      A/D 4Q avg < 0.20            < 0.35
                  OR range_pos > 0.90 + EPS rev < 0   (extra flag)
                  OR range_pos > 0.85 + EPS rev < 0.05
  Technical       % above 200DMA > 40%         > 20%
                  Weekly RSI > 75              > 65
  Quality         Altman Z < 1.81 OR > 50      < 3 OR > 25
                  Piotroski ≤ 3                ≤ 5

Each pillar caps at 2 (don't double-count within a pillar — strong + weak
on the same pillar still = 2).

Gate: composite ≥ 6 AND every pillar score ≥ 1.
"""
from __future__ import annotations

from typing import Optional

from src.research_ideas.complacency.data_fetch import ComplacencyBundle


# ─── Sector medians (rough Nov-2025 defaults; overridable per run) ─────────
# When the sector layer is built (v2), these will be computed live from the
# scored universe. For v1 we use static medians keyed by sector.
SECTOR_EV_SALES_MEDIAN: dict[str, float] = {
    "Technology":              7.0,
    "Communication Services":  4.5,
    "Consumer Cyclical":       2.5,
    "Consumer Defensive":      1.5,
    "Healthcare":              5.0,
    "Financial Services":      3.0,
    "Industrials":             2.5,
    "Energy":                  1.5,
    "Utilities":               3.0,
    "Materials":               1.8,
    "Real Estate":             6.0,
}


def _score_valuation(b: ComplacencyBundle, sector_median_ev_sales: Optional[float]) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 0
    strong = False
    weak = False

    # Relative-to-sector EV/Sales
    if b.ev_sales is not None and sector_median_ev_sales and sector_median_ev_sales > 0:
        ratio = b.ev_sales / sector_median_ev_sales
        if ratio > 2.5:
            strong = True
            notes.append(f"EV/Sales {b.ev_sales:.1f}× = {ratio:.1f}× sector median")
        elif ratio > 1.5:
            weak = True
            notes.append(f"EV/Sales {b.ev_sales:.1f}× = {ratio:.1f}× sector median")

    # Absolute backstop (handles "whole sector in complacency" case)
    if b.ev_sales is not None:
        if b.ev_sales > 25.0:
            strong = True
            notes.append(f"EV/Sales {b.ev_sales:.1f}× — extreme absolute")
        elif b.ev_sales > 15.0 and not strong:
            weak = True
            if not any("EV/Sales" in n for n in notes):
                notes.append(f"EV/Sales {b.ev_sales:.1f}× — elevated absolute")

    # FCF yield (signed, so negative is worst)
    if b.fcf_yield_ttm is not None:
        if b.fcf_yield_ttm < -0.005:
            strong = True
            notes.append(f"FCF yield {b.fcf_yield_ttm*100:.2f}% (negative)")
        elif b.fcf_yield_ttm < 0.015:
            weak = True
            notes.append(f"FCF yield {b.fcf_yield_ttm*100:.2f}% — thin")

    if strong:
        score = 2
    elif weak:
        score = 1
    return score, notes


def _score_behavioral(b: ComplacencyBundle) -> tuple[float, list[str]]:
    notes: list[str] = []
    strong = False
    weak = False

    # Insider Acquired/Disposed ratio (often N/A on free FMP tier)
    if b.ad_ratio_4q_avg is not None:
        if b.ad_ratio_4q_avg < 0.20:
            strong = True
            notes.append(f"Insiders selling — A/D ratio {b.ad_ratio_4q_avg:.2f}")
        elif b.ad_ratio_4q_avg < 0.35:
            weak = True
            notes.append(f"Insider selling skew — A/D ratio {b.ad_ratio_4q_avg:.2f}")

    # Range position × EPS revision: high-priced into weak/declining estimates
    if b.range_position is not None and b.eps_revision_yoy is not None:
        if b.range_position > 0.90 and b.eps_revision_yoy < 0:
            strong = True
            notes.append(
                f"Near 52w high (range pos {b.range_position:.0%}) with EPS revisions cut {b.eps_revision_yoy*100:.1f}%"
            )
        elif b.range_position > 0.85 and b.eps_revision_yoy < 0.05:
            weak = True
            notes.append(
                f"Near 52w high (range pos {b.range_position:.0%}) with EPS revisions flat {b.eps_revision_yoy*100:.1f}%"
            )

    if strong:
        return 2, notes
    if weak:
        return 1, notes
    return 0, notes


def _score_technical(b: ComplacencyBundle) -> tuple[float, list[str]]:
    notes: list[str] = []
    strong = False
    weak = False

    if b.sma200_extension is not None:
        if b.sma200_extension > 0.40:
            strong = True
            notes.append(f"+{b.sma200_extension*100:.0f}% above 200DMA")
        elif b.sma200_extension > 0.20:
            weak = True
            notes.append(f"+{b.sma200_extension*100:.0f}% above 200DMA")

    if b.rsi_weekly is not None:
        if b.rsi_weekly > 75:
            strong = True
            notes.append(f"Weekly RSI {b.rsi_weekly:.0f} — overbought")
        elif b.rsi_weekly > 65 and not strong:
            weak = True
            notes.append(f"Weekly RSI {b.rsi_weekly:.0f} — extended")

    if strong:
        return 2, notes
    if weak:
        return 1, notes
    return 0, notes


def _score_quality(b: ComplacencyBundle) -> tuple[float, list[str]]:
    notes: list[str] = []
    strong = False
    weak = False

    # Altman Z dual tail
    if b.altman_z is not None:
        if b.altman_z < 1.81:
            strong = True
            notes.append(f"Altman Z {b.altman_z:.1f} — distress zone")
        elif b.altman_z > 50:
            strong = True
            notes.append(f"Altman Z {b.altman_z:.0f} — denominator collapse (mkt cap detached)")
        elif b.altman_z < 3:
            weak = True
            notes.append(f"Altman Z {b.altman_z:.1f} — grey zone")
        elif b.altman_z > 25 and not strong:
            weak = True
            notes.append(f"Altman Z {b.altman_z:.0f} — elevated")

    # Piotroski
    if b.piotroski is not None:
        if b.piotroski <= 3:
            strong = True
            notes.append(f"Piotroski {b.piotroski}/9 — weak fundamentals")
        elif b.piotroski <= 5 and not strong:
            weak = True
            notes.append(f"Piotroski {b.piotroski}/9 — middling fundamentals")

    if strong:
        return 2, notes
    if weak:
        return 1, notes
    return 0, notes


# ─── Verdict + justification ──────────────────────────────────────────────


def derive_verdict(composite: float, pillar_min: float, passes_gate: bool) -> str:
    if not passes_gate:
        if composite >= 4:
            return "Borderline"
        return "Pass"
    # Gate passed
    if composite >= 7.5 and pillar_min >= 1.5:
        return "Strong-Short"
    return "Watch"


def compose_justification(
    name: str,
    composite: float,
    verdict: str,
    flag_notes: list[str],
) -> str:
    headline = {
        "Strong-Short": "Strong complacency signal",
        "Watch":        "Multi-pillar complacency — watch",
        "Borderline":   "Partial complacency signal",
        "Pass":         "No meaningful complacency signal",
        "N/A":          "Insufficient data",
    }[verdict]
    flag_str = "; ".join(flag_notes[:4]) if flag_notes else "no individual flags surfaced"
    return f"{name}: {headline} ({composite:.1f}/8). {flag_str}."


# ─── Public aggregator ────────────────────────────────────────────────────


def score_bundle(b: ComplacencyBundle) -> dict:
    """Returns a dict of pillar scores + composite + verdict + notes."""
    sector_median = SECTOR_EV_SALES_MEDIAN.get(b.sector or "", None) if b.sector else None
    val, val_notes = _score_valuation(b, sector_median)
    beh, beh_notes = _score_behavioral(b)
    tech, tech_notes = _score_technical(b)
    qual, qual_notes = _score_quality(b)

    composite = val + beh + tech + qual
    pillars = [val, beh, tech, qual]
    pillar_min = min(pillars)

    passes_gate = composite >= 6 and pillar_min >= 1
    verdict = derive_verdict(composite, pillar_min, passes_gate)

    flag_notes = val_notes + beh_notes + tech_notes + qual_notes

    return {
        "ev_sales": b.ev_sales,
        "ev_sales_sector_median": sector_median,
        "ev_sales_relative": (b.ev_sales / sector_median) if (b.ev_sales and sector_median) else None,
        "fcf_yield_ttm": b.fcf_yield_ttm,
        "altman_z": b.altman_z,
        "piotroski": b.piotroski,
        "ad_ratio_4q_avg": b.ad_ratio_4q_avg,
        "eps_revision_yoy": b.eps_revision_yoy,
        "sma200_extension": b.sma200_extension,
        "rsi_weekly": b.rsi_weekly,
        "range_position": b.range_position,
        "val_score": float(val),
        "beh_score": float(beh),
        "tech_score": float(tech),
        "qual_score": float(qual),
        "composite": float(composite),
        "passes_gate": passes_gate,
        "verdict": verdict,
        "flag_notes": flag_notes,
        "justification": compose_justification(b.name or b.ticker, composite, verdict, flag_notes),
    }
