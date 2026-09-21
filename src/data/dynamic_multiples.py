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
                        "basket": "Oil & Gas Refining & Marketing", "crack_rule": True,
                        "band_rationale": "Owner's through-cycle EV/EBITDA band for refining: high cyclicality and terminal transition risk keep it the lowest band here. The market validates it -- the large-cohort through-cycle multiple ran 4.25x-5.54x over 2021-2025 and stood at 5.54x in 2025 against the 5.5x baseline. The low end (4.5x) is the trough the crack-spread rule forces when the 3-2-1 crack runs more than 25% above its long-run mean, so a crack spike is never capitalised."},
    # Owner, 2026-09-21: re-based on the market's through-cycle multiple
    # (14.14x, 2025, large cohort), which had re-rated above the original
    # 9-12x band. Band = the owner's +/-20% outlier guard around it.
    "midstream":       {"baseline": 14.1, "band": (11.28, 16.92),
                        "basket": "Oil & Gas Midstream",
                        "band_rationale": "Re-based by the owner 2026-09-21 on the market's through-cycle multiple, 14.14x (2025, US large cohort, n=10), which had re-rated from 8.08x in 2021 as fee-based infrastructure was repriced. The original 9-12x band sat entirely below the market and would have clamped every proposal to it, so it was superseded. The band is the owner's +/-20% outlier guard around the 14.1x baseline: 11.28x-16.92x."},
    "fuel_marketing":  {"baseline": 7.0,  "band": (6.0, 8.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True,
                        "band_rationale": 'Owner band 6-8x: fuel marketing and specialties earn steadier cash flows than refining (volume and margin over a wholesale cost, not the crack spread) but are not fee-based infrastructure, so they sit between the two. No peer basket exists, so the band has no market check; the 2% EBITDA margin is an owner estimate, deliberately thin because most of the revenue is resold fuel.'},
    "renewable_fuels": {"baseline": 7.0,  "band": (6.0, 8.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True,
                        "band_rationale": 'Owner band 6-8x: renewable diesel and SAF carry policy-supported margins (credits, blending mandates) that are steadier than the crack but policy-dependent, so they sit above refining and below midstream. No peer basket exists, so the band has no market check; the 10% EBITDA margin is an owner estimate.'},
    "ethanol":         {"baseline": 5.0,  "band": (4.0, 6.0),
                        "basket": "Oil & Gas Refining & Marketing", "proxy": True,
                        "band_rationale": "Owner band 4-6x: ethanol is a commodity-spread business (corn against ethanol and co-products) with less scale and pricing power than refining, so its band sits at and below refining's. No peer basket exists, so the band has no market check; the 6% EBITDA margin is an owner estimate."},
    # Owner, 2026-09-21: re-based on the market's through-cycle multiple
    # (5.17x, 2025, large cohort), which had de-rated below the original 7-9x
    # band. Band = +/-20% around it.
    "chemicals":       {"baseline": 5.2,  "band": (4.16, 6.24), "basket": "Chemicals",
                        "band_rationale": "Re-based by the owner 2026-09-21 on the market's through-cycle multiple, 5.17x (2025, US large cohort, n=7), which had de-rated from 10.07x in 2021 as commodity chemical spreads compressed. The original 7-9x band sat entirely above the market and was superseded. The band is +/-20% around the 5.2x baseline: 4.16x-6.24x. An equity-accounted chemicals JV is held at book value instead and never reaches this multiple."},
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
              field: str = "ev_ebitda_norm", level: Optional[str] = None) -> dict:
    """Fit the two slopes on a basket's history and shrink them to the prior."""
    from src.data import regional_comps as rc
    prior = PRIORS.get(prior_key) or PRIORS["default"]
    hist_m = {}
    hist_r = {}
    cohort = "large"
    # Only COMPLETE years. The backfill buckets a company by the calendar year
    # its fiscal year ends, so the current year holds only the early filers
    # (June year ends) until December filers report -- a thin, skewed median
    # that must not be fitted on or quoted as "the market now".
    this_year = date.today().year
    for c in ("large", "all"):
        hm = {r["as_of"][:4]: r["value"] for r in rc.load_history(exchange, basket, field, c, level)
              if int(r["as_of"][:4]) < this_year}
        if len(hm) >= 3:
            hist_m, cohort = hm, c
            hist_r = {r["as_of"][:4]: r["value"]
                      for r in rc.load_history(exchange, basket, "roic", c, level)
                      if int(r["as_of"][:4]) < this_year}
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
        rows = [r for r in rc.load_history(exchange, basket, "roic", c)
                if int(r["as_of"][:4]) < date.today().year]
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
        "band_rationale": cfg.get("band_rationale"),
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


def accept_value(segment_type: str, value: float, reviewer: str, basis: str) -> dict:
    """Record an owner-SET multiple rather than an engine proposal.

    The owner may take a starting point the engine did not propose (the market
    level itself, say). The proposal as it stood is still attached, so the
    record shows both what the engine said and what the owner chose.
    """
    from src.data import valuation_constants as vc
    cfg = SEGMENT_BASELINES.get(segment_type)
    if not cfg:
        raise KeyError(segment_type)
    lo, hi = cfg.get("band") or (None, None)
    if lo is not None and not (lo - 1e-9 <= value <= hi + 1e-9):
        raise ValueError(f"{segment_type}: {value} is outside its band {lo}-{hi}; "
                         f"move the band first")
    try:
        context = propose(segment_type)
    except Exception as exc:                                   # noqa: BLE001
        context = {"error": str(exc)[:200]}
    doc = vc.load()
    entry = {"multiple": float(value), "accepted_at": date.today().isoformat(),
             "reviewer": reviewer, "basis": basis,
             "band_rationale": cfg.get("band_rationale"),
             "derivation": {**context, "band": [lo, hi] if lo is not None else None,
                            "owner_set": True}}
    doc.setdefault("segment_multiples", {})[segment_type] = entry
    vc.save(doc)
    return entry


def accept(proposal: dict, reviewer: str) -> dict:
    """Record a proposal as the multiple the SOTP uses. Owner action only."""
    from src.data import valuation_constants as vc
    doc = vc.load()
    entry = {"multiple": proposal["proposed"], "accepted_at": date.today().isoformat(),
             "reviewer": reviewer, "band_rationale": proposal.get("band_rationale"),
             "derivation": proposal}
    doc.setdefault("segment_multiples", {})[proposal["segment_type"]] = entry
    vc.save(doc)
    return entry



# ═══════════════════════════════════════════════════════════════════════════
# ALL INDUSTRIES (owner, 2026-09-21)
#
# Every comps basket with through-cycle history -- US, HKSE, SES, industry and
# sector level -- gets a dynamic multiple, updated automatically each quarter
# after the backfill. The owner chose AUTO-APPLY WITHIN GUARDRAILS:
#
#   * baseline = the basket's own multi-year through-cycle average;
#   * the same rate / ROIC adjustment as the energy segments, bounded to
#     +/-20% of baseline, every clamp flagged;
#   * a move under 5% of the current multiple is held as noise;
#   * a basket the owner PINS is never touched by the automation;
#   * DYNAMIC_MULTIPLES_AUTO_DISABLED=true stops the quarterly update and
#     DYNAMIC_MULTIPLES_ENABLED=false stops valuations reading the table.
#
# It reaches a valuation only through the NORMALISED legs, EV/EBITDA (norm) and
# P/E (norm) (owner choice): those legs multiply through-cycle earnings, and the
# trailing peer median they used is the wrong basis for that -- the mismatch
# the refining audit found. Forward legs keep the live peer multiple, which is
# the right partner for next year's earnings. The bank P/E (norm) leg uses an
# owner-calibrated P/E rather than a peer multiple and is left as it is.
#
# Everything is logged so the owner can confirm, each quarter, that the update
# RAN, that it reached the engine for EVERY industry, and that the multiples
# REACHED VALUATIONS (dynamic_multiples_usage, written by the valuation itself).
# ═══════════════════════════════════════════════════════════════════════════

#: A quarterly move smaller than this is noise and the current multiple holds.
INERTIA = 0.05

_DM_DDL = """
CREATE TABLE IF NOT EXISTS dynamic_multiples (
    exchange        TEXT NOT NULL,
    level           TEXT NOT NULL,
    key             TEXT NOT NULL,
    field           TEXT NOT NULL,
    multiple        REAL NOT NULL,
    baseline        REAL,
    band_lo         REAL,
    band_hi         REAL,
    market_now      REAL,
    source          TEXT NOT NULL,
    pinned          INTEGER NOT NULL DEFAULT 0,
    effective_at    TEXT NOT NULL,
    derivation_json TEXT,
    PRIMARY KEY (exchange, level, key, field)
)
"""
_DM_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS dynamic_multiples_audit (
    run_id          TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    level           TEXT NOT NULL,
    key             TEXT NOT NULL,
    field           TEXT NOT NULL,
    previous        REAL,
    proposed        REAL,
    applied         REAL,
    action          TEXT NOT NULL,
    reason          TEXT,
    run_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, exchange, level, key, field)
)
"""
_DM_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS dynamic_multiples_runs (
    run_id          TEXT PRIMARY KEY,
    run_at          TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    summary_json    TEXT NOT NULL
)
"""
_DM_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS dynamic_multiples_usage (
    ticker          TEXT NOT NULL,
    leg             TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    level           TEXT NOT NULL,
    key             TEXT NOT NULL,
    field           TEXT NOT NULL,
    multiple        REAL NOT NULL,
    effective_at    TEXT,
    used_at         TEXT NOT NULL,
    PRIMARY KEY (ticker, leg)
)
"""

_dm_ready: Optional[tuple] = None


def _dm_db():
    from src.data import db as _db
    return _db


def _ensure_dm_tables() -> None:
    global _dm_ready
    _db = _dm_db()
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _dm_ready:
        return
    _db.execute_script(";".join([_DM_DDL, _DM_AUDIT_DDL, _DM_RUNS_DDL, _DM_USAGE_DDL]))
    _dm_ready = key


_FINANCIAL_KEYS = ("bank", "insurance", "asset management", "capital markets",
                   "credit services", "financial", "mortgage", "shell companies")
_SKIP_KEYS = ("reit",)


def industry_fields(key: str) -> list[str]:
    """Every through-cycle multiple a basket carries: both normalised legs
    read one, so both are kept -- except that a financial has no meaningful
    EBITDA, and a REIT is valued on its own path."""
    k = (key or "").lower()
    if any(x in k for x in _SKIP_KEYS):
        return []
    if any(x in k for x in _FINANCIAL_KEYS):
        return ["pe_norm"]
    return ["ev_ebitda_norm", "pe_norm"]


def industry_anchor_field(key: str) -> Optional[str]:
    """Which through-cycle multiple a basket is valued on.

    Balance-sheet financials anchor on earnings, not EBITDA (a bank's EBITDA is
    not a meaningful figure), so they take the through-cycle P/E. REITs are
    valued on their own P/FFO path and get no dynamic multiple here.
    """
    k = (key or "").lower()
    if any(x in k for x in _SKIP_KEYS):
        return None
    if any(x in k for x in _FINANCIAL_KEYS):
        return "pe_norm"
    return "ev_ebitda_norm"


#: live comps field -> the through-cycle field that replaces it in a normalised leg
LIVE_TO_NORM = {"ev_ebitda": "ev_ebitda_norm", "pe": "pe_norm"}


def propose_industry(exchange: str, level: str, key: str,
                     field: Optional[str] = None) -> Optional[dict]:
    """The proposed through-cycle multiple for one basket, with its working.

    None when the basket has too little complete-year history to say anything.
    """
    field = field or industry_anchor_field(key)
    if not field:
        return None
    b = fit_betas(key, exchange, "default", field, level=level)
    series = b["series"]
    if len(series) < 3:
        return None
    baseline = statistics.fmean(series.values())
    market_now = series[max(series)]
    rr = real_rate_series()
    r_now, r_mean = latest(rr["series"]), trailing_mean(rr["series"], 5)
    d_rate = (r_now - r_mean) if (r_now is not None and r_mean is not None) else 0.0
    from src.data import regional_comps as rc
    roic_rows = [r for r in rc.load_history(exchange, key, "roic", b["cohort"], level)
                 if int(r["as_of"][:4]) < date.today().year]
    roic_now = roic_rows[-1]["value"] if roic_rows else None
    d_roic = ((roic_now - b["roic_mean"])
              if (roic_now is not None and b.get("roic_mean") is not None) else 0.0)
    t1, t2 = b["b1"] * d_rate, b["b2"] * d_roic
    raw = baseline * math.exp(t1 + t2)
    lo, hi = baseline * (1 - MAX_DEVIATION), baseline * (1 + MAX_DEVIATION)
    proposed = min(max(raw, lo), hi)
    flags = []
    if proposed != raw:
        flags.append(f"clamped from {raw:.2f}x to {proposed:.2f}x (+/-20% of baseline)")
    if not (lo <= market_now <= hi):
        flags.append(f"market through-cycle multiple {market_now:.2f}x is "
                     f"{'above' if market_now > hi else 'below'} the band "
                     f"{lo:.2f}-{hi:.2f}x")
    if exchange != "US":
        flags.append("real-rate factor uses the US 10y real yield as the proxy")
    return {
        "exchange": exchange, "level": level, "key": key, "field": field,
        "baseline": baseline, "band": [lo, hi], "market_now": market_now,
        "years": sorted(series), "series": series,
        "betas": {k: b[k] for k in ("b1", "b2", "n", "shrink_weight", "note",
                                    "cohort", "regressor_corr")},
        "terms": {"rate": t1, "roic": t2}, "raw": raw,
        "proposed": round(proposed, 4), "flags": flags,
        "band_rationale": (f"Baseline is this basket's own through-cycle average "
                           f"({baseline:.2f}x over {min(series)}-{max(series)}, "
                           f"{b['cohort']} cohort); the band is the owner's +/-20% "
                           f"outlier guard around it. Auto-applied quarterly."),
        "as_of": date.today().isoformat(),
    }


def current_multiple(exchange: str, level: str, key: str, field: str) -> Optional[dict]:
    """The multiple valuations read for this basket, or None."""
    try:
        _ensure_dm_tables()
        row = _dm_db().query_one(
            "SELECT multiple, effective_at, source, pinned, baseline, band_lo, band_hi "
            "FROM dynamic_multiples WHERE exchange = ? AND level = ? AND key = ? AND field = ?",
            [exchange, level, key, field])
        return dict(row) if row else None
    except Exception:                                          # noqa: BLE001
        return None


def _baskets_with_history(exchange: str) -> list[tuple[str, str]]:
    _db = _dm_db()
    from src.data import regional_comps as rc
    rc._ensure_table()
    rows = _db.query("SELECT DISTINCT level, key FROM regional_comps_history "
                     "WHERE exchange = ? AND field IN (?, ?)",
                     [exchange, "ev_ebitda_norm", "pe_norm"]) or []
    return sorted((dict(r)["level"], dict(r)["key"]) for r in rows)


def _basket_fields(exchange: str) -> list[tuple[str, str, Optional[str]]]:
    """(level, key, field) for every basket; field None marks a skipped REIT."""
    out: list[tuple[str, str, Optional[str]]] = []
    for level, key in _baskets_with_history(exchange):
        fields = industry_fields(key)
        if not fields:
            out.append((level, key, None))
        out.extend((level, key, f) for f in fields)
    return out


def update_all(markets: tuple[str, ...], trigger: str = "quarterly") -> dict:
    """Apply the quarterly update to every basket. Returns the run summary.

    Owner decision: auto-apply within guardrails. Pinned baskets are skipped,
    moves under INERTIA are held, everything is written to the audit.
    """
    import json as _json
    import uuid
    _ensure_dm_tables()
    _db = _dm_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = f"{now[:10]}-{uuid.uuid4().hex[:8]}"
    counts = {"evaluated": 0, "updated": 0, "initial": 0, "held_inertia": 0,
              "pinned": 0, "insufficient_history": 0, "skipped_reit": 0}
    per_market: dict[str, dict] = {}
    for ex in markets:
        mc = {k: 0 for k in counts}
        for level, key, field in _basket_fields(ex):
            if field is None:
                mc["skipped_reit"] += 1
                continue
            mc["evaluated"] += 1
            cur = current_multiple(ex, level, key, field)
            if cur and cur.get("pinned"):
                mc["pinned"] += 1
                _audit(run_id, ex, level, key, field, cur["multiple"], None, cur["multiple"],
                       "pinned", "owner-pinned: the automation leaves it alone", now)
                continue
            p = propose_industry(ex, level, key, field)
            if not p:
                mc["insufficient_history"] += 1
                continue
            prev = cur["multiple"] if cur else None
            if prev and abs(p["proposed"] / prev - 1.0) < INERTIA:
                mc["held_inertia"] += 1
                _audit(run_id, ex, level, key, field, prev, p["proposed"], prev, "held",
                       f"move {p['proposed'] / prev - 1.0:+.1%} under the {INERTIA:.0%} inertia", now)
                continue
            _db.execute(
                "INSERT INTO dynamic_multiples (exchange, level, key, field, multiple, baseline, "
                "band_lo, band_hi, market_now, source, pinned, effective_at, derivation_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
                "ON CONFLICT (exchange, level, key, field) DO UPDATE SET "
                "multiple = excluded.multiple, baseline = excluded.baseline, "
                "band_lo = excluded.band_lo, band_hi = excluded.band_hi, "
                "market_now = excluded.market_now, source = excluded.source, "
                "effective_at = excluded.effective_at, derivation_json = excluded.derivation_json",
                [ex, level, key, field, p["proposed"], p["baseline"], p["band"][0], p["band"][1],
                 p["market_now"], "auto", now, _json.dumps(p, default=str)])
            action = "updated" if prev else "initial"
            mc[action] += 1
            _audit(run_id, ex, level, key, field, prev, p["proposed"], p["proposed"], action,
                   "; ".join(p["flags"]) or None, now)
        per_market[ex] = mc
        for k in counts:
            counts[k] += mc[k]
    summary = {"run_id": run_id, "run_at": now, "trigger": trigger,
               "totals": counts, "markets": per_market}
    _db.execute("INSERT INTO dynamic_multiples_runs (run_id, run_at, trigger, summary_json) "
                "VALUES (?, ?, ?, ?)", [run_id, now, trigger, _json.dumps(summary)])
    return summary


def _audit(run_id, ex, level, key, field, previous, proposed, applied, action, reason, now):
    _dm_db().execute(
        "INSERT INTO dynamic_multiples_audit (run_id, exchange, level, key, field, previous, "
        "proposed, applied, action, reason, run_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, ex, level, key, field, previous, proposed, applied, action, reason, now])


def pin(exchange: str, level: str, key: str, field: str, value: float, reviewer: str) -> dict:
    """Owner override: set a basket's multiple and exempt it from automation."""
    _ensure_dm_tables()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _dm_db().execute(
        "INSERT INTO dynamic_multiples (exchange, level, key, field, multiple, source, pinned, "
        "effective_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?) "
        "ON CONFLICT (exchange, level, key, field) DO UPDATE SET multiple = excluded.multiple, "
        "source = excluded.source, pinned = 1, effective_at = excluded.effective_at",
        [exchange, level, key, field, float(value), f"owner:{reviewer}", now])
    return current_multiple(exchange, level, key, field)


def unpin(exchange: str, level: str, key: str, field: str) -> Optional[dict]:
    """Hand a basket back to the automation (it re-proposes next quarter)."""
    _ensure_dm_tables()
    _dm_db().execute("UPDATE dynamic_multiples SET pinned = 0 WHERE exchange = ? AND level = ? "
                     "AND key = ? AND field = ?", [exchange, level, key, field])
    return current_multiple(exchange, level, key, field)


def record_usage(ticker: str, leg: str, exchange: str, level: str, key: str,
                 field: str, multiple: float, effective_at: Optional[str]) -> None:
    """Written by a valuation when a normalised leg priced on a dynamic
    multiple -- the evidence that the multiple REACHED a valuation."""
    try:
        _ensure_dm_tables()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _dm_db().execute(
            "INSERT INTO dynamic_multiples_usage (ticker, leg, exchange, level, key, field, "
            "multiple, effective_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ticker, leg) DO UPDATE SET exchange = excluded.exchange, "
            "level = excluded.level, key = excluded.key, field = excluded.field, "
            "multiple = excluded.multiple, effective_at = excluded.effective_at, "
            "used_at = excluded.used_at",
            [ticker, leg, exchange, level, key, field, float(multiple), effective_at, now])
    except Exception:                                          # noqa: BLE001
        pass


def log_report(limit_runs: int = 8) -> dict:
    """What the Model Accuracy log shows: did it run, did it reach every
    industry, did it reach valuations."""
    import json as _json
    _ensure_dm_tables()
    _db = _dm_db()
    runs = [dict(r) for r in (_db.query(
        "SELECT run_id, run_at, trigger, summary_json FROM dynamic_multiples_runs "
        "ORDER BY run_at DESC LIMIT ?", [limit_runs]) or [])]
    for r in runs:
        r["summary"] = _json.loads(r.pop("summary_json") or "{}")
    coverage = {}
    for ex in ("US", "HKSE", "SES"):
        slots = [bf for bf in _basket_fields(ex) if bf[2]]
        live = _db.query_one("SELECT COUNT(*) AS n FROM dynamic_multiples WHERE exchange = ?",
                             [ex])
        coverage[ex] = {"baskets_with_history": len({(l, k) for l, k, _ in slots}),
                        "multiple_slots": len(slots),
                        "multiples_live": int(dict(live)["n"]) if live else 0}
    last_run_at = runs[0]["run_at"] if runs else None
    usage_rows = [dict(r) for r in (_db.query(
        "SELECT exchange, level, key, field, COUNT(DISTINCT ticker) AS tickers, "
        "MAX(used_at) AS last_used FROM dynamic_multiples_usage "
        "GROUP BY exchange, level, key, field ORDER BY tickers DESC") or [])]
    since = [u for u in usage_rows if last_run_at and str(u["last_used"]) >= str(last_run_at)]
    table = [dict(r) for r in (_db.query(
        "SELECT exchange, level, key, field, multiple, baseline, band_lo, band_hi, market_now, "
        "source, pinned, effective_at FROM dynamic_multiples ORDER BY exchange, level, key") or [])]
    return {
        "runs": runs,
        "coverage": coverage,
        "reached_valuations": {
            "baskets_used": len(usage_rows),
            "baskets_used_since_last_update": len(since),
            "tickers": sum(int(u["tickers"]) for u in usage_rows),
            "by_basket": usage_rows[:200],
        },
        "multiples": table,
        "switches": {
            "auto_update_enabled": os.environ.get(
                "DYNAMIC_MULTIPLES_AUTO_DISABLED", "false").lower() != "true",
            "valuations_read_enabled": enabled(),
        },
    }


def enabled() -> bool:
    """Whether valuations read the dynamic multiples table."""
    return os.environ.get("DYNAMIC_MULTIPLES_ENABLED", "true").lower() == "true"
