"""Dynamic, regime-adjusted valuation multiples -- Phase 2 (owner spec 2026-09-20/21).

A static multiple is a number chosen once and never rechecked. Every multiple
defect found on 2026-09-20 was one: a distributable-cash-flow leg on a free-
cash-flow yield, a refinery valued as an advertising business, pure-play
margins on segment revenue. This module replaces the constant with a function
of the regime, and keeps the one property that matters: a multiple is never
chosen silently. It PROPOSES, with its full derivation; the owner accepts.

The engine is industry-agnostic (owner, 2026-09-21). Energy is only the first
set with owner bands.

    multiple = baseline x exp( b1 x (real_rate - its 5y mean)
                             + b2 x (basket ROIC - its window mean) )

then the refining crack-spread rule, then two bounds:
  * +/-20% of baseline -- the owner's outlier guard;
  * the owner's band, where one exists ("calibrated" mode).
Breaching either is clamped AND flagged, never clamped silently.

Four choices the data forced, each recorded on every proposal:

  NORMALISED TARGET. Factors are fitted to EV / mean-EBITDA (the through-cycle
    multiple), not TTM EV/EBITDA. A cyclical's TTM multiple moves inversely
    with its own earnings -- refining large cohort 3.17x in 2022 at 16.5% ROIC,
    8.35x in 2021 at 5.7% -- so a ROIC factor fitted to it would EXPAND the
    multiple at the peak: the peak anchoring the crack rule exists to stop.
  REAL RATE. 10y Treasury minus FMP's `inflationRate`, which is the 10y
    breakeven: 2021-01-04 gives 0.93% - 2.01% = -1.08% against FRED DFII10's
    -1.07%. FRED is preferred when FRED_API_KEY is set; neither environment
    has one today.
  SHRINKAGE. Five annual basket points (the FMP key-metrics depth on this plan)
    cannot estimate two slopes on their own. Each beta is shrunk toward an
    owner-editable prior with weight n / (n + k), and n, the raw OLS beta and
    the shrunk one are all reported. The prior carries the answer until the
    weekly history is long enough to overrule it -- and says so.
  CYCLICAL SIGN. On a cyclical the fitted ROIC slope is allowed to be
    negative; the prior is not forced onto it. The data decides the sign.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_S = "https://financialmodelingprep.com/stable"
# Outside src/ on purpose: a fetched market series is a cache, not source, and
# must never be committed. `.cache/` is untracked.
_MACRO_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "dynamic_multiples_macro.json"
_MACRO_TTL_DAYS = 7

#: The owner's outlier guard: never more than 20% from baseline.
MAX_DEVIATION = 0.20
#: Pseudo-observations the prior is worth when shrinking a fitted beta.
SHRINK_K = 10.0
#: Above this absolute correlation between the real rate and basket ROIC, the
#: two slopes cannot be told apart and the prior is used for both.
COLLINEAR_CORR = 0.80
#: The crack-spread rule fires when the 3-2-1 crack runs this far above its
#: long-run mean (owner: "force trough multiples when crack spreads spike >25%
#: above historical means to prevent peak-cycle anchoring").
CRACK_SPIKE = 0.25

#: Owner baselines and bands (spec 2026-09-20). `basket` is the comps key whose
#: history drives the factors; `proxy` marks a type with no basket of its own,
#: which borrows the regime factor of the named one and says so.
SEGMENT_BASELINES: dict[str, dict] = {
    "refining":        {"baseline": 5.5,  "band": (4.5, 6.5),
                        "basket": "Oil & Gas Refining & Marketing", "crack_rule": True},
    "midstream":       {"baseline": 10.5, "band": (9.0, 12.0),
                        "basket": "Oil & Gas Midstream"},
    "fuel_marketing":  {"baseline": 7.0,  "band": (6.0, 8.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True},
    "renewable_fuels": {"baseline": 7.0,  "band": (6.0, 8.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True},
    "ethanol":         {"baseline": 5.0,  "band": (4.0, 6.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True},
    "chemicals":       {"baseline": 8.0,  "band": (7.0, 9.0), "basket": "Chemicals"},
    # E&P is valued on the ASC 932 standardized measure as an unblended bear
    # floor (owner spec), not on a dynamic multiple: no baseline here.
}

#: Priors for the two slopes, per unit: b1 per 1.00 (100pp) of real rate, b2
#: per 1.00 of ROIC. Owner-editable; every proposal shows which one it used.
#: b1 is negative (higher real yields compress multiples) and steeper for
#: infrastructure, which the spec calls "highly sensitive to real yield".
PRIORS: dict[str, dict[str, float]] = {
    "default":   {"b1": -4.0, "b2": 2.0},
    "midstream": {"b1": -8.0, "b2": 2.0},
}


# ── macro inputs ────────────────────────────────────────────────────────────

def _fmp(path: str, params: dict):
    from src.data.regional_comps import _fmp_get
    return _fmp_get(f"{_S}/{path}", params, api_key=None)


def _quarter_windows(start: date, end: date):
    d = date(start.year, 1, 1)
    while d <= end:
        q_end = (date(d.year + (d.month + 2) // 12, ((d.month + 2) % 12) + 1, 1)
                 - timedelta(days=1))
        yield d, min(q_end, end)
        d = q_end + timedelta(days=1)


def _load_cache() -> dict:
    try:
        doc = json.loads(_MACRO_CACHE.read_text(encoding="utf-8"))
        age = datetime.now(timezone.utc) - datetime.fromisoformat(doc["fetched_at"])
        if age.days < _MACRO_TTL_DAYS:
            return doc
    except Exception:                                          # noqa: BLE001
        pass
    return {}


def real_rate_series(start: date = date(2016, 1, 1)) -> dict:
    """Daily 10y real yield, {'source', 'series': {iso_date: rate}}.

    FRED DFII10 when a key is set; otherwise nominal 10y minus the 10y
    breakeven, both from FMP in quarterly windows (the endpoint caps a query
    at about a quarter).
    """
    cached = _load_cache()
    if cached.get("real_rate"):
        return cached["real_rate"]
    series: dict[str, float] = {}
    source = None
    key = os.environ.get("FRED_API_KEY")
    if key:
        try:
            import urllib.parse
            import urllib.request
            u = ("https://api.stlouisfed.org/fred/series/observations?"
                 + urllib.parse.urlencode({"series_id": "DFII10", "api_key": key,
                                           "file_type": "json",
                                           "observation_start": start.isoformat()}))
            obs = json.loads(urllib.request.urlopen(u, timeout=20).read())["observations"]
            series = {o["date"]: float(o["value"]) / 100.0 for o in obs
                      if o.get("value") not in (None, ".")}
            source = "FRED DFII10 (10y TIPS real yield)"
        except Exception:                                      # noqa: BLE001
            series = {}
    if not series:
        nominal: dict[str, float] = {}
        breakeven: dict[str, float] = {}
        for a, b in _quarter_windows(start, date.today()):
            for r in _fmp("treasury-rates", {"from": a.isoformat(), "to": b.isoformat()}) or []:
                if isinstance(r.get("year10"), (int, float)):
                    nominal[str(r["date"])[:10]] = float(r["year10"]) / 100.0
            for r in _fmp("economic-indicators", {"name": "inflationRate",
                                                  "from": a.isoformat(),
                                                  "to": b.isoformat()}) or []:
                if isinstance(r.get("value"), (int, float)):
                    breakeven[str(r["date"])[:10]] = float(r["value"]) / 100.0
        series = {d: nominal[d] - breakeven[d] for d in nominal if d in breakeven}
        source = "FMP 10y Treasury minus FMP 10y breakeven (inflationRate)"
    out = {"source": source, "series": dict(sorted(series.items()))}
    _save_cache("real_rate", out)
    return out


def crack_spread_series(start: date = date(2016, 1, 1)) -> dict:
    """Daily US Gulf-style 3-2-1 crack, $/bbl: (2 x RBOB + 1 x ULSD - 3 x WTI)/3.

    Gasoline and heating oil quote in $/gallon, crude in $/bbl, hence x42.
    """
    cached = _load_cache()
    if cached.get("crack"):
        return cached["crack"]
    px: dict[str, dict[str, float]] = {}
    for sym in ("CLUSD", "RBUSD", "HOUSD"):
        rows = _fmp("historical-price-eod/light", {"symbol": sym, "from": start.isoformat()})
        rows = rows if isinstance(rows, list) else (rows or {}).get("historical") or []
        px[sym] = {str(r["date"])[:10]: float(r.get("price") or r.get("close"))
                   for r in rows if (r.get("price") or r.get("close"))}
    common = set(px["CLUSD"]) & set(px["RBUSD"]) & set(px["HOUSD"])
    series = {d: (2 * px["RBUSD"][d] * 42 + px["HOUSD"][d] * 42 - 3 * px["CLUSD"][d]) / 3
              for d in sorted(common)}
    out = {"source": "FMP CLUSD / RBUSD / HOUSD, 3-2-1", "series": series}
    _save_cache("crack", out)
    return out


def _save_cache(key: str, value: dict) -> None:
    try:
        doc = {}
        if _MACRO_CACHE.exists():
            doc = json.loads(_MACRO_CACHE.read_text(encoding="utf-8"))
        doc[key] = value
        doc["fetched_at"] = datetime.now(timezone.utc).isoformat()
        _MACRO_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _MACRO_CACHE.write_text(json.dumps(doc), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass


def annual_mean(series: dict[str, float]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for d, v in series.items():
        by.setdefault(d[:4], []).append(v)
    return {y: statistics.fmean(vs) for y, vs in by.items() if vs}


def trailing_mean(series: dict[str, float], years: float = 5.0) -> Optional[float]:
    if not series:
        return None
    last = datetime.fromisoformat(max(series)).date()
    cut = (last - timedelta(days=int(365.25 * years))).isoformat()
    vs = [v for d, v in series.items() if d >= cut]
    return statistics.fmean(vs) if vs else None


def latest(series: dict[str, float], days: int = 30) -> Optional[float]:
    """Mean of the last `days` of observations -- one print is noise."""
    if not series:
        return None
    last = datetime.fromisoformat(max(series)).date()
    cut = (last - timedelta(days=days)).isoformat()
    vs = [v for d, v in series.items() if d >= cut]
    return statistics.fmean(vs) if vs else None


# ── fitting ─────────────────────────────────────────────────────────────────

def _ols2(y: list[float], x1: list[float], x2: list[float]) -> Optional[dict]:
    """y = a + b1 x1 + b2 x2 by normal equations. None when singular or n < 4."""
    n = len(y)
    if n < 4:
        return None
    m1, m2, my = statistics.fmean(x1), statistics.fmean(x2), statistics.fmean(y)
    c1 = [v - m1 for v in x1]
    c2 = [v - m2 for v in x2]
    cy = [v - my for v in y]
    s11 = sum(a * a for a in c1)
    s22 = sum(b * b for b in c2)
    s12 = sum(a * b for a, b in zip(c1, c2))
    s1y = sum(a * b for a, b in zip(c1, cy))
    s2y = sum(a * b for a, b in zip(c2, cy))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return None
    b1 = (s1y * s22 - s2y * s12) / det
    b2 = (s2y * s11 - s1y * s12) / det
    fit = [my + b1 * a + b2 * b for a, b in zip(c1, c2)]
    ss_res = sum((yi - fi) ** 2 for yi, fi in zip(y, fit))
    ss_tot = sum(v * v for v in cy)
    return {"b1": b1, "b2": b2, "a": my, "n": n,
            "r2": (1 - ss_res / ss_tot) if ss_tot > 0 else None}


def fit_betas(basket: str, exchange: str = "US", prior_key: str = "default",
              field: str = "ev_ebitda_norm") -> dict:
    """Fit the two slopes on a basket's history and shrink them to the prior."""
    from src.data import regional_comps as rc
    prior = PRIORS.get(prior_key) or PRIORS["default"]
    hist_m = {}
    hist_r = {}
    cohort = "large"
    for c in ("large", "all"):
        hm = {r["as_of"][:4]: r["value"] for r in rc.load_history(exchange, basket, field, c)}
        if len(hm) >= 3:
            hist_m, cohort = hm, c
            hist_r = {r["as_of"][:4]: r["value"]
                      for r in rc.load_history(exchange, basket, "roic", c)}
            break
    rr = annual_mean(real_rate_series()["series"])
    years = sorted(y for y in hist_m if y in hist_r and y in rr and hist_m[y] > 0)
    y = [math.log(hist_m[k]) for k in years]
    x1 = [rr[k] for k in years]
    x2 = [hist_r[k] for k in years]
    ols = _ols2(y, x1, x2)
    n = len(years)
    w = n / (n + SHRINK_K)
    corr = _corr(x1, x2)
    out = {"basket": basket, "cohort": cohort, "field": field, "years": years, "n": n,
           "prior": dict(prior), "shrink_weight": w, "regressor_corr": corr,
           "ols": ols, "b1": prior["b1"], "b2": prior["b2"],
           "roic_mean": statistics.fmean(x2) if x2 else None,
           "series": {k: hist_m[k] for k in sorted(hist_m)}}
    notes: list[str] = []
    # COLLINEARITY. Over 2021-2025 real yields rose steadily while a
    # cyclical's ROIC peaked and fell, so the two regressors can move almost in
    # lockstep. The fit then splits one trend into two huge offsetting slopes:
    # chemicals came back OLS b1 +41.9, b2 +19.4 at R-squared 1.00 on five
    # points. Neither slope is identified, so neither is used -- the prior
    # stands for both, and the proposal says why.
    # Checked whether or not the fit succeeded: perfectly collinear inputs make
    # the normal equations singular, OLS returns None, and the prior is used
    # anyway -- which must still be said, not left silent.
    if corr is not None and abs(corr) > COLLINEAR_CORR:
        notes.append(f"regressors collinear (corr {corr:+.2f}): slopes not identified, prior used")
    elif ols:
        out["b1"] = w * ols["b1"] + (1 - w) * prior["b1"]
        out["b2"] = w * ols["b2"] + (1 - w) * prior["b2"]
    # SIGN. The owner's spec is that real rates adjust multiples INVERSELY. A
    # fitted positive slope contradicts the spec rather than refining it, so it
    # is clipped to zero -- and said to have been.
    if out["b1"] > 0:
        notes.append(f"fitted rate slope {out['b1']:+.2f} contradicts the inverse "
                     f"relationship in the spec: clipped to 0")
        out["b1"] = 0.0
    notes.append("prior-dominated: the history is too short to overrule it"
                 if w < 0.5 else "data-weighted")
    out["note"] = "; ".join(notes)
    return out


def _corr(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 3:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


# ── the proposal ────────────────────────────────────────────────────────────

def propose(segment_type: str, exchange: str = "US") -> dict:
    """What the multiple for this segment type should be now, and why.

    Never writes. A proposal becomes a valuation input only when the owner
    accepts it (see `accept` and scripts/calibrate_dynamic_multiples.py).
    """
    cfg = SEGMENT_BASELINES.get(segment_type)
    if not cfg:
        raise KeyError(segment_type)
    baseline = float(cfg["baseline"])
    lo, hi = cfg.get("band") or (None, None)
    basket = cfg["basket"]
    prior_key = segment_type if segment_type in PRIORS else "default"
    betas = fit_betas(basket, exchange, prior_key)

    rr = real_rate_series()
    r_now, r_mean = latest(rr["series"]), trailing_mean(rr["series"], 5)
    d_rate = (r_now - r_mean) if (r_now is not None and r_mean is not None) else 0.0

    from src.data import regional_comps as rc
    roic_now = None
    for c in (betas["cohort"], "all"):
        rows = rc.load_history(exchange, basket, "roic", c)
        if rows:
            roic_now = rows[-1]["value"]
            break
    d_roic = ((roic_now - betas["roic_mean"])
              if (roic_now is not None and betas.get("roic_mean") is not None) else 0.0)

    term1, term2 = betas["b1"] * d_rate, betas["b2"] * d_roic
    raw = baseline * math.exp(term1 + term2)
    flags: list[str] = []
    rules: list[str] = []

    crack = None
    if cfg.get("crack_rule"):
        cs = crack_spread_series()["series"]
        c_now, c_mean = latest(cs), (statistics.fmean(cs.values()) if cs else None)
        crack = {"now": c_now, "long_run_mean": c_mean,
                 "spike": (c_now / c_mean - 1.0) if (c_now and c_mean) else None}
        if crack["spike"] is not None and crack["spike"] > CRACK_SPIKE and lo is not None:
            raw = lo
            rules.append(f"crack spread {crack['spike']:+.0%} above its long-run mean "
                         f"(>{CRACK_SPIKE:.0%}): forced to the trough multiple {lo:.2f}x")

    dev_lo, dev_hi = baseline * (1 - MAX_DEVIATION), baseline * (1 + MAX_DEVIATION)
    bound_lo = max(dev_lo, lo) if lo is not None else dev_lo
    bound_hi = min(dev_hi, hi) if hi is not None else dev_hi
    proposed = min(max(raw, bound_lo), bound_hi)
    if proposed != raw:
        flags.append(f"clamped from {raw:.2f}x to {proposed:.2f}x "
                     f"(bounds {bound_lo:.2f}-{bound_hi:.2f}x)")

    market_now = betas["series"].get(max(betas["series"])) if betas["series"] else None
    if cfg.get("proxy"):
        # A proxy borrows another basket's REGIME factor, not its level: the
        # refining basket's 5.54x says nothing about where fuel marketing
        # should trade, so comparing it to this band would be apples to
        # oranges. Say what the proxy is and leave the band alone.
        flags.append(f"no basket of its own: regime factor borrowed from {basket}; "
                     f"the band is owner-set and has no market check")
    elif market_now is not None and lo is not None and not (lo <= market_now <= hi):
        flags.append(f"the market's through-cycle multiple is {market_now:.2f}x, "
                     f"{'above' if market_now > hi else 'below'} the owner band "
                     f"{lo:.1f}-{hi:.1f}x -- a band decision, not a clamp")

    return {
        "segment_type": segment_type,
        "mode": "calibrated" if lo is not None else "recommend",
        "baseline": baseline, "band": [lo, hi] if lo is not None else None,
        "basket": basket, "basket_is_proxy": bool(cfg.get("proxy")),
        "market_multiple_now": None if cfg.get("proxy") else market_now,
        "real_rate": {"now": r_now, "mean_5y": r_mean, "delta": d_rate, "source": rr["source"]},
        "roic": {"now": roic_now, "window_mean": betas.get("roic_mean"), "delta": d_roic},
        "betas": {k: betas[k] for k in ("b1", "b2", "n", "shrink_weight", "prior", "ols",
                                        "note", "cohort", "years", "regressor_corr")},
        "terms": {"rate": term1, "roic": term2},
        "crack": crack, "rules": rules,
        "raw": raw, "proposed": round(proposed, 4), "flags": flags,
        "as_of": date.today().isoformat(),
    }


def backtest(segment_type: str, exchange: str = "US") -> dict:
    """Leave-one-out: does the regime model beat the static baseline?

    For each year, fit on the others and predict that year's through-cycle
    multiple; compare with the history's own mean as the static forecast. With
    five points this is indicative, and it is labelled so.
    """
    cfg = SEGMENT_BASELINES[segment_type]
    b = fit_betas(cfg["basket"], exchange,
                  segment_type if segment_type in PRIORS else "default")
    years, series = b["years"], b["series"]
    from src.data import regional_comps as rc
    roic = {r["as_of"][:4]: r["value"] for r in rc.load_history(exchange, cfg["basket"], "roic", b["cohort"])}
    rr = annual_mean(real_rate_series()["series"])
    err_static, err_dyn = [], []
    for held in years:
        rest = [y for y in years if y != held]
        if len(rest) < 3:
            continue
        m_mean = statistics.fmean(series[y] for y in rest)
        r_mean = statistics.fmean(rr[y] for y in rest)
        q_mean = statistics.fmean(roic[y] for y in rest)
        ols = _ols2([math.log(series[y]) for y in rest], [rr[y] for y in rest],
                    [roic[y] for y in rest])
        w = len(rest) / (len(rest) + SHRINK_K)
        c = _corr([rr[y] for y in rest], [roic[y] for y in rest])
        usable = ols and not (c is not None and abs(c) > COLLINEAR_CORR)
        b1 = (w * ols["b1"] + (1 - w) * b["prior"]["b1"]) if usable else b["prior"]["b1"]
        b2 = (w * ols["b2"] + (1 - w) * b["prior"]["b2"]) if usable else b["prior"]["b2"]
        b1 = min(b1, 0.0)
        pred = m_mean * math.exp(b1 * (rr[held] - r_mean) + b2 * (roic[held] - q_mean))
        err_static.append(abs(m_mean - series[held]) / series[held])
        err_dyn.append(abs(pred - series[held]) / series[held])
    return {"segment_type": segment_type, "n": len(err_dyn),
            "mae_static": statistics.fmean(err_static) if err_static else None,
            "mae_dynamic": statistics.fmean(err_dyn) if err_dyn else None,
            "note": "leave-one-out on five annual points: indicative only"}


# ── owner acceptance ────────────────────────────────────────────────────────

def accepted(segment_type: str) -> Optional[dict]:
    """The owner-accepted dynamic multiple for a segment type, or None."""
    from src.data import valuation_constants as vc
    return ((vc.load().get("segment_multiples") or {}).get(segment_type))


def accept(proposal: dict, reviewer: str) -> dict:
    """Record a proposal as the multiple the SOTP uses. Owner action only."""
    from src.data import valuation_constants as vc
    doc = vc.load()
    entry = {"multiple": proposal["proposed"], "accepted_at": date.today().isoformat(),
             "reviewer": reviewer, "derivation": proposal}
    doc.setdefault("segment_multiples", {})[proposal["segment_type"]] = entry
    vc.save(doc)
    return entry
