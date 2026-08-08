"""
src/research_ideas/hundred_q/_calc.py
========================================
Small numeric helpers shared by questions_registry.py and scoring.py.
Pure functions, no I/O — kept in their own module to avoid a circular
import between the two (questions_registry defines quant_fn callables
that need these; scoring.py drives the registry).
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence


def clean(series: Sequence[Optional[float]]) -> list[float]:
    """Drop Nones, preserving order."""
    return [v for v in series if v is not None]


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def last(series: Sequence[Optional[float]]) -> Optional[float]:
    vals = clean(series)
    return vals[-1] if vals else None


def first(series: Sequence[Optional[float]]) -> Optional[float]:
    vals = clean(series)
    return vals[0] if vals else None


def cagr(series: Sequence[Optional[float]]) -> Optional[float]:
    """CAGR from the first to the last non-None value in the series."""
    vals = clean(series)
    if len(vals) < 2 or vals[0] <= 0:
        return None
    years = len(vals) - 1
    ratio = vals[-1] / vals[0]
    if ratio <= 0:
        return None
    return ratio ** (1 / years) - 1


def yoy_growth_series(series: Sequence[Optional[float]]) -> list[Optional[float]]:
    """Period-over-period growth rate, aligned to the LATER period of each pair."""
    out: list[Optional[float]] = []
    prev = None
    for v in series:
        if v is None or prev is None or prev == 0:
            out.append(None)
        else:
            out.append((v - prev) / abs(prev))
        prev = v if v is not None else prev
    return out


def latest_yoy_growth(series: Sequence[Optional[float]]) -> Optional[float]:
    vals = clean(series)
    if len(vals) < 2 or vals[-2] == 0:
        return None
    return (vals[-1] - vals[-2]) / abs(vals[-2])


def bps_stdev(series: Sequence[Optional[float]]) -> Optional[float]:
    """Population stdev of a fractional series (e.g. margins), in basis points."""
    vals = clean(series)
    if len(vals) < 3:
        return None
    return statistics.pstdev(vals) * 10_000


def ratio_series(numer: Sequence[Optional[float]], denom: Sequence[Optional[float]]) -> list[Optional[float]]:
    out = []
    for n, d in zip(numer, denom):
        out.append(safe_div(n, d))
    return out


def sum_clean(series: Sequence[Optional[float]], last_n: Optional[int] = None) -> Optional[float]:
    vals = clean(series)
    if not vals:
        return None
    if last_n:
        vals = vals[-last_n:]
    return sum(vals)


def median(vals: Sequence[Optional[float]]) -> Optional[float]:
    cleaned = clean(vals)
    if not cleaned:
        return None
    return statistics.median(cleaned)


# ── WACC profile mapping ────────────────────────────────────────────────────
# src/data/sector_profiles.py::SECTOR_WACC is keyed by DCF-engine profile
# names ("Tech", "Consumer", "Biopharma", ...), not the GICS-style sector/
# industry strings this screener's universe.json uses. Map the common cases;
# get_wacc() already falls back to a safe 9% default for anything unmapped,
# so an incomplete mapping degrades gracefully rather than raising.
_WACC_PROFILE_MAP: dict[str, str] = {
    "Technology": "Tech",
    "Communication Services": "Tech",
    "Consumer Cyclical": "Consumer",
    "Consumer Defensive": "Consumer",
    "Financial Services": "Financials",
    "Financials": "Financials",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Real Estate": "RealEstate",
}


def wacc_profile_for(sector: Optional[str], industry: Optional[str]) -> str:
    industry = industry or ""
    if "Semiconductor" in industry:
        return "Semiconductor"
    if sector == "Healthcare":
        if "Plan" in industry or "Service" in industry:
            return "HealthcareServices"
        return "Biopharma"
    return _WACC_PROFILE_MAP.get(sector or "", sector or "")
