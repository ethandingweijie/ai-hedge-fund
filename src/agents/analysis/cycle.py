"""Where a company sits in its earnings cycle (owner, 2026-11-12: money-losing and cyclical firms, phase 1).

Professional treatment of a cyclical: value it on mid-cycle earnings, not on the year in hand. A peak margin
capitalised into a DCF or a forward multiple overstates (MU at 2.4x spot); a trough margin held flat understates.

measure() reads the company's own annual history (up to 10 years) and returns:
  cyclical         profile says so, or the data does: the EBIT margin swings widely AND oscillates around its median
                   (crosses it at least twice) -- a one-way slide is a structural decline, not a cycle
  structural_decline  the margin has slid for most of the window and sits at or near its low -- a turnaround
                   question for phase 3, never mean-reverted here
  mid_cycle_margin median EBIT margin over the window after IQR outlier removal (impairments, one-offs)
  position         peak / mid / trough: the latest margin's z-score against the window (>= +1 sigma or a new high
                   above +0.5 sigma = peak; the mirror for trough)
Pure function; no I/O.
"""
from __future__ import annotations

from statistics import median, pstdev
from typing import Optional

MIN_YEARS = 5
DATA_MIN_YEARS = 6
#: data-driven cyclicality: margin range and dispersion (EBIT margin, absolute points)
RANGE_MIN = 0.08
SIGMA_MIN = 0.03
#: a revenue drawdown this deep with a margin range of at least RANGE_MIN_DRAWDOWN also counts
REV_DRAWDOWN = -0.15
RANGE_MIN_DRAWDOWN = 0.05
MIN_CROSSINGS = 2
PEAK_Z, EDGE_Z = 1.0, 0.5
#: forecast years over which the margin returns to mid-cycle after the guided / street years
REVERSION_YEARS = 3


LONG_YEARS = 15
#: the mid-cycle margin is read over the last full cycle, not the whole window
MID_YEARS = 10


def long_history_enabled() -> bool:
    """CYCLE_LONG_HISTORY=off skips the long fetch (golden replay pins it off: its fixtures hold no such call)."""
    import os
    return os.environ.get("CYCLE_LONG_HISTORY", "on").strip().lower() not in ("0", "false", "no", "off")


def fetch_long_history(ticker: str, api_key: Optional[str] = None, years: int = LONG_YEARS) -> list[dict]:
    """Up to `years` annual income statements (revenue, EBIT, EBITDA, D&A), oldest first, for the cycle measure only.
    The engine's line-item series is capped at five years (src.tools.api._FREE_LIMIT), one cycle at best; a cycle
    needs 2008-09 and 2020 in the window. [] when disabled or on any failure (the caller falls back to the series)."""
    if not long_history_enabled():
        return []
    try:
        from src.tools.api import _STABLE, _fmp_get
        rows = _fmp_get(f"{_STABLE}/income-statement", {"symbol": ticker, "period": "annual", "limit": years},
                        api_key, uncap=True) or []
    except Exception:                                      # noqa: BLE001
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        out.append({"period": str(r.get("date") or r.get("fiscalYear") or ""),
                    "revenue": r.get("revenue"), "ebit": r.get("operatingIncome"), "ebitda": r.get("ebitda"),
                    "depreciation_and_amortization": r.get("depreciationAndAmortization")})
    return sorted(out, key=lambda x: x["period"])


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ebit_margin(row: dict) -> Optional[float]:
    rev = _f(row.get("revenue"))
    if not rev or rev <= 0:
        return None
    e = _f(row.get("ebit"))
    if e is None:
        eb, da = _f(row.get("ebitda")), _f(row.get("depreciation_and_amortization"))
        e = (eb - abs(da)) if (eb is not None and da is not None) else None
    return None if e is None else e / rev


def _iqr_filter(vals: list[float]) -> list[float]:
    if len(vals) < 4:
        return list(vals)
    s = sorted(vals)
    q1, q3 = s[len(s) // 4], s[(3 * len(s)) // 4]
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    kept = [v for v in vals if lo <= v <= hi]
    return kept if len(kept) >= 3 else list(vals)


def measure(series: Optional[list], *, profile_cyclical: bool = False, max_years: int = LONG_YEARS) -> Optional[dict]:
    rows = [r for r in (series or []) if isinstance(r, dict)][-max_years:]
    pts = [(str(r.get("period") or "")[:4], m, _f(r.get("revenue"))) for r in rows if (m := _ebit_margin(r)) is not None]
    if len(pts) < MIN_YEARS:
        return None
    years = [p[0] for p in pts]
    ms = [p[1] for p in pts]
    revs = [p[2] for p in pts]
    med_all = median(ms)
    sd = pstdev(ms)
    rng = max(ms) - min(ms)
    crossings = sum(1 for a, b in zip(ms, ms[1:]) if (a - med_all) * (b - med_all) < 0)
    peak_rev, dd = revs[0], 0.0
    for r in revs:
        peak_rev = max(peak_rev, r)
        dd = min(dd, r / peak_rev - 1.0)
    swings = (rng >= RANGE_MIN and sd >= SIGMA_MIN) or (dd <= REV_DRAWDOWN and rng >= RANGE_MIN_DRAWDOWN)
    data_cyclical = len(ms) >= DATA_MIN_YEARS and swings and crossings >= MIN_CROSSINGS
    # a one-way slide: most year-on-year moves down and the latest margin in the bottom fifth
    downs = sum(1 for a, b in zip(ms, ms[1:]) if b < a)
    low_rank = sum(1 for v in ms if v <= ms[-1]) / len(ms)
    structural = (swings and downs >= 0.6 * (len(ms) - 1) and low_rank <= 0.2 and crossings <= 1
                  and ms[-1] < med_all)
    # the last full cycle: older regimes (a franchise that has since changed) do not set today's mid-cycle
    mid_ms = ms[-MID_YEARS:]
    mid = median(_iqr_filter(mid_ms))
    sd_mid = pstdev(mid_ms) if len(mid_ms) >= 2 else sd
    latest = ms[-1]
    z = (latest - mid) / sd_mid if sd_mid > 1e-9 else 0.0
    prior = ms[:-1]
    if z >= PEAK_Z or (latest >= max(prior) and z >= EDGE_Z):
        position = "peak"
    elif z <= -PEAK_Z or (latest <= min(prior) and z <= -EDGE_Z):
        position = "trough"
    else:
        position = "mid"
    # Cyclicality is an industry trait: only a cyclical PROFILE mean-reverts. Margins that swing on a non-cyclical
    # profile are usually one-offs or a broken franchise (INTC, WBD, MMM) -- flagged for review, never reverted.
    cyclical = bool(profile_cyclical) and not structural
    return {
        "years": years, "ebit_margins": [round(m, 4) for m in ms], "n_years": len(ms),
        "mid_cycle_margin": round(mid, 4), "mid_cycle_years": len(mid_ms), "latest_margin": round(latest, 4),
        "sigma": round(sd_mid, 4),
        "range": round(rng, 4), "median_crossings": crossings, "revenue_drawdown": round(dd, 4),
        "z": round(z, 2), "position": position,
        "cyclical": cyclical, "cyclical_basis": ("profile" if profile_cyclical else None),
        "review_cyclical": bool(data_cyclical and not profile_cyclical and not structural),
        "data_cyclical": bool(data_cyclical), "structural_decline": bool(structural),
        "reversion_years": REVERSION_YEARS,
    }


def describe(c: Optional[dict]) -> Optional[str]:
    if not c:
        return None
    if c.get("structural_decline"):
        return (f"Margin in structural decline, not a cycle: EBIT margin {c['latest_margin']:.1%} against a "
                f"{c['n_years']}-year median {c['mid_cycle_margin']:.1%}, falling in most years -- not mean-reverted")
    if c.get("review_cyclical"):
        return (f"Margins swing like a cyclical's ({c['n_years']} years, range {c['range']:.0%}, {c['median_crossings']} "
                f"crossings of the median) but the profile is not cyclical: not mean-reverted -- review the profile, or "
                "whether the swings are one-offs or a changed franchise")
    if not c.get("cyclical"):
        return None
    return (f"Cycle position {c['position'].upper()}: EBIT margin {c['latest_margin']:.1%} against a "
            f"{c['mid_cycle_years']}-year mid-cycle {c['mid_cycle_margin']:.1%} (z {c['z']:+.1f}; {c['n_years']} years "
            f"read); the forecast margin returns to mid-cycle over {c['reversion_years']} years after the guided years")


def mid_cycle_ratio(rows: list, field: str, years: int = MID_YEARS) -> Optional[float]:
    """Median of field / revenue over the last `years` rows with both, after IQR filtering (None under 5 points)."""
    vals = []
    for r in (rows or [])[-years:]:
        rev, v = _f(r.get("revenue")), _f(r.get(field))
        if rev and rev > 0 and v is not None:
            vals.append(v / rev)
    return median(_iqr_filter(vals)) if len(vals) >= MIN_YEARS else None
