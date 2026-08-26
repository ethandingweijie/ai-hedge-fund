"""
src/portfolio/replay.py
========================
P2 — quantitative crisis-replay engine. Pure Python, zero LLM: what would
THIS portfolio have done through each curated historical event?

Design
------
  • Input: holdings [{ticker, quantity, avg_cost}], an event list (default
    event_library.EVENTS), an injectable price_fetcher (default
    src.tools.api.get_prices — HK/SG routing + superset cache included),
    and today's regime dict (regime_state.json's `regime` block).
  • Weights for the replay are cost-basis weights (qty × avg_cost) — the
    deterministic "what did I own" basis; live-price weighting belongs to
    the P1 dashboard, not a historical replay.
  • Coverage guard: a holding that wasn't listed yet (first available
    price after window start + grace) or has too few in-window points is
    flagged covered=False and EXCLUDED from the portfolio aggregates —
    weights renormalize over the covered set. Never silently zero-filled.
  • Portfolio path: per-covered-holding price series normalized to 100 at
    the first in-window close, forward-filled onto the union of dates,
    weighted-summed → portfolio equity curve → window return + max DD
    (same cummax pattern as src/backtesting/metrics.py).
  • Determinism: fixed rounding, sorted keys, no wall clock inside the
    engine → identical inputs give byte-identical JSON (backward gate).
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from src.portfolio.event_library import (
    BENCH_TOLERANCE_PP, EVENTS, LIBRARY_VERSION, EventSpec, events_as_dicts,
)

# Coverage parameters
_GRACE_CALENDAR_DAYS = 14   # first price may lag window start by this much
_MIN_WINDOW_POINTS = 15     # fewer in-window closes → insufficient data

# Benchmark symbols
_SPY, _QQQ = "SPY", "QQQ"


# ── Primitive math ───────────────────────────────────────────────────────────

def _window_return_pct(closes: list[float]) -> Optional[float]:
    """First→last return of a non-empty in-window close series."""
    if len(closes) < 2 or not closes[0]:
        return None
    return (closes[-1] / closes[0] - 1.0) * 100.0


def _max_dd_pct(closes: list[float]) -> Optional[float]:
    """Max drawdown % (negative) via running-max (cummax pattern)."""
    if not closes:
        return None
    peak = closes[0]
    worst = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = c / peak - 1.0
            if dd < worst:
                worst = dd
    return worst * 100.0


def _beta_pct_series(asset_ret: list[float], bench_ret: list[float]) -> Optional[float]:
    """Beta of aligned daily simple returns. None when variance is nil or
    fewer than 10 aligned observations."""
    n = min(len(asset_ret), len(bench_ret))
    if n < 10:
        return None
    a, b = asset_ret[:n], bench_ret[:n]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    var = sum((y - mb) ** 2 for y in b) / n
    if var < 1e-12:
        return None
    return cov / var


def _daily_returns(dates: list[str], closes: list[float]) -> tuple[list[str], list[float]]:
    rets = []
    rd: list[str] = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            rets.append(closes[i] / closes[i - 1] - 1.0)
            rd.append(dates[i])
    return rd, rets


def _align_returns(dates_a: list[str], ret_a: list[float],
                   dates_b: list[str], ret_b: list[float]):
    """Align two dated return series on common dates (both must have the
    prior-day close)."""
    b_idx = dict(zip(dates_b, ret_b))
    da, ra, rb = [], [], []
    for d, r in zip(dates_a, ret_a):
        if d in b_idx:
            da.append(d)
            ra.append(r)
            rb.append(b_idx[d])
    return ra, rb


# ── Per-event replay ─────────────────────────────────────────────────────────

def _series_for(fetcher: Callable, ticker: str, start: str, end: str,
                buffer_start: str) -> tuple[list[str], list[float]]:
    """Fetch (dates, closes) ascending. Buffer fetch starts before the
    window so the superset cache amortizes across events/holdings."""
    try:
        px = fetcher(ticker, buffer_start, end)
    except Exception:
        return [], []
    pts = sorted(
        ((str(getattr(p, "time", "")), float(getattr(p, "close", 0) or 0)) for p in (px or [])),
        key=lambda t: t[0],
    )
    return [d for d, _ in pts if d], [c for _, c in pts]


def _replay_event(ev: EventSpec, holdings: list[dict], weights: dict[str, float],
                  fetcher: Callable, today_regime: dict) -> dict:
    buffer_start = _shift_days(ev.start, -45)

    # Benchmarks (live-computed; cross-checked against curated numbers)
    spy = _series_for(fetcher, _SPY, ev.start, ev.end, buffer_start)
    qqq = _series_for(fetcher, _QQQ, ev.start, ev.end, buffer_start)
    spy_in = _in_window(spy, ev.start, ev.end)
    qqq_in = _in_window(qqq, ev.start, ev.end)
    live = {
        "spy_return_pct": _round2(_window_return_pct([c for _, c in spy_in])),
        "spy_max_dd_pct": _round2(_max_dd_pct([c for _, c in spy_in])),
        "qqq_return_pct": _round2(_window_return_pct([c for _, c in qqq_in])),
        "qqq_max_dd_pct": _round2(_max_dd_pct([c for _, c in qqq_in])),
    }
    curated = {
        "spy_return_pct": ev.spy_return_pct, "spy_max_dd_pct": ev.spy_max_dd_pct,
        "qqq_return_pct": ev.qqq_return_pct, "qqq_max_dd_pct": ev.qqq_max_dd_pct,
    }
    divergences = [
        k for k in curated
        if live.get(k) is not None and abs(live[k] - curated[k]) > BENCH_TOLERANCE_PP
    ]
    spy_ret_series = _daily_returns([d for d, _ in spy_in], [c for _, c in spy_in])

    rows = []
    covered_names: list[str] = []
    normalized: dict[str, list[tuple[str, float]]] = {}
    for h in sorted(holdings, key=lambda x: x["ticker"]):
        tkr = h["ticker"]
        full = _series_for(fetcher, tkr, ev.start, ev.end, buffer_start)
        in_win = _in_window(full, ev.start, ev.end)
        first_in_window = _first_on_or_after(full, ev.start)
        covered = bool(
            in_win
            and first_in_window is not None
            and first_in_window <= _shift_days(ev.start, _GRACE_CALENDAR_DAYS)
            and len(in_win) >= _MIN_WINDOW_POINTS
        )
        row = {
            "ticker": tkr,
            "covered": covered,
            "window_return_pct": None,
            "max_dd_pct": None,
            "beta": None,
        }
        if covered:
            covered_names.append(tkr)
            closes = [c for _, c in in_win]
            dates = [d for d, _ in in_win]
            row["window_return_pct"] = _round2(_window_return_pct(closes))
            row["max_dd_pct"] = _round2(_max_dd_pct(closes))
            a_dates, a_rets = _daily_returns(dates, closes)
            a_al, b_al = _align_returns(a_dates, a_rets, *spy_ret_series)
            row["beta"] = _round2(_beta_pct_series(a_al, b_al))
            base = closes[0] or 1.0
            normalized[tkr] = [(d, 100.0 * c / base) for d, c in zip(dates, closes)]
        rows.append(row)

    # Portfolio aggregate over covered holdings (weights renormalized)
    wsum = sum(weights.get(t, 0.0) for t in covered_names)
    portfolio = {"window_return_pct": None, "max_dd_pct": None,
                 "covered_weight_pct": _round2(wsum * 100.0) if weights else None}
    if covered_names and wsum > 0:
        curve = _portfolio_curve(normalized, {t: weights[t] / wsum for t in covered_names})
        portfolio["window_return_pct"] = _round2(_window_return_pct([v for _, v in curve]))
        portfolio["max_dd_pct"] = _round2(_max_dd_pct([v for _, v in curve]))

    return {
        **ev.as_dict(),
        "benchmarks": {
            "curated": curated,
            "live": live,
            "cross_check": "divergent" if divergences else "ok",
            "divergent_keys": divergences,
        },
        "holdings": rows,
        "portfolio": portfolio,
        "excluded": [r["ticker"] for r in rows if not r["covered"]],
        "regime_similarity": _regime_similarity(today_regime, ev.macro.as_dict()),
    }


def _portfolio_curve(normalized: dict[str, list[tuple[str, float]]],
                     weights: dict[str, float]) -> list[tuple[str, float]]:
    """Weighted portfolio equity curve (each series normalized to 100 at its
    first in-window close, forward-filled onto the union of dates)."""
    all_dates = sorted({d for series in normalized.values() for d, _ in series})
    last = {t: None for t in normalized}
    idx = {t: 0 for t in normalized}
    curve: list[tuple[str, float]] = []
    for d in all_dates:
        for t in normalized:
            series = normalized[t]
            while idx[t] < len(series) and series[idx[t]][0] <= d:
                last[t] = series[idx[t]][1]
                idx[t] += 1
        vals = [(t, last[t]) for t in normalized if last[t] is not None]
        if not vals:
            continue
        curve.append((d, sum(weights[t] * v for t, v in vals)))
    return curve


def _regime_similarity(today_regime: dict, then: dict) -> dict:
    """Exact-match count across the 5 regime dimensions."""
    dims = ("risk_appetite", "rate_direction", "dollar_trend",
            "volatility_regime", "recession_risk")
    matches = [d for d in dims if today_regime.get(d) == then.get(d)]
    return {"matches": len(matches), "of": len(dims), "matched_dims": matches,
            "today": {d: today_regime.get(d) for d in dims}, "then": then}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _in_window(series: tuple[list[str], list[float]], start: str, end: str):
    dates, closes = series
    return [(d, c) for d, c in zip(dates, closes) if start <= d <= end and c > 0]


def _first_on_or_after(series: tuple[list[str], list[float]], start: str) -> Optional[str]:
    for d, c in zip(series[0], series[1]):
        if d >= start and c > 0:
            return d
    return None


def _shift_days(date_str: str, days: int) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in date_str.split("-"))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


def _round2(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 2)


def snapshot_hash(holdings: list[dict]) -> str:
    """Stable hash of the holdings snapshot (the replay cache key).

    LIBRARY_VERSION is baked in so a library content change (new events,
    re-calibrated sector numbers) invalidates every cached replay — the
    old rows stay in the table but miss the lookup and recompute.
    """
    import hashlib
    payload = {
        "library_version": LIBRARY_VERSION,
        "holdings": sorted(
            (str(h["ticker"]).upper(), round(float(h.get("quantity") or 0), 6),
             round(float(h.get("avg_cost") or 0), 6))
            for h in holdings
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ── Entry point ──────────────────────────────────────────────────────────────

def replay_portfolio(holdings: list[dict],
                     events: Optional[list[EventSpec]] = None,
                     price_fetcher: Optional[Callable] = None,
                     today_regime: Optional[dict] = None,
                     end_date_hint: Optional[str] = None) -> dict:
    """Run the full replay. Returns a deterministic dict (JSON-safe).

    holdings:      [{ticker, quantity, avg_cost}]
    price_fetcher: (ticker, start, end) -> list of objects with .time/.close
                   (defaults to src.tools.api.get_prices)
    today_regime:  regime_state.json's `regime` block (similarity scoring
                   degrades to zero matches when absent)
    """
    if price_fetcher is None:
        from src.tools.api import get_prices
        price_fetcher = get_prices

    evs = list(events) if events else list(EVENTS)
    basis = {h["ticker"].upper(): float(h.get("quantity") or 0) * float(h.get("avg_cost") or 0)
             for h in holdings}
    total = sum(basis.values())
    weights = {t: (v / total if total else 0.0) for t, v in basis.items()}

    regime = dict(today_regime or {})
    per_event = [_replay_event(ev, holdings, weights, price_fetcher, regime)
                 for ev in evs]
    # Most-similar events first (stable tie-break on curated severity)
    per_event.sort(key=lambda e: (-(e["regime_similarity"]["matches"] or 0), e["key"]))

    return {
        "event_count": len(per_event),
        "events": per_event,
        "holdings_snapshot": {
            "tickers": sorted(basis.keys()),
            "position_count": len(basis),
            "snapshot_hash": snapshot_hash(holdings),
            "weight_basis": "cost_basis_qty_x_avg_cost",
        },
    }
