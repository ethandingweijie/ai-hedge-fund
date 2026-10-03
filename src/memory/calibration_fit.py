"""Calibration proposals (B4) and their forward shadow test (B5).

A proposal is fitted only from the outcome ledger -- never a live LLM call --
and has to clear three bars before anyone is asked to promote it:

  1. time-ordered holdout inside the fit: each cell and market is fitted on
     its older 70% of runs and kept only if it improves the newer 30%;
  2. the walk-forward backtest (BT2) over the whole proposal, with its
     lookahead guards -- a proposal that fails is stored as "rejected", with
     the reasons, so a rejection is auditable rather than silent;
  3. a forward shadow period (FT2): at least 28 days and 20 labelled runs in
     every cell it touches, on runs made after it was proposed, beating live.

What is learned (v1), and why only these: both are re-blendable from what a
run already stores, so the backtest and the shadow score them exactly as the
engine would -- no second engine run.

  profile_weights       per (sector, profile), projected gradient descent on
                        squared log error, L2-shrunk toward today's weights,
                        each weight moving at most WEIGHT_MOVE_CAP per cycle
  market_iv_multiplier  per market, the median residual log error after the
                        weights, shrunk by n/(n+10), capped at +/-LOG_BIAS_CAP

Scalars that need the engine to re-run (the China haircut, holdco discounts,
growth bands, SOTP blend weights) are not proposed here: they cannot be
scored from stored values, and a parameter that cannot be backtested cannot
be promoted.

Approximation, stated: a re-weighting is scored as live IV x (blend under the
new weights / blend under today's weights) over the run's stored method
values. That keeps everything else the run did -- composite, Gate A, proxies
-- and moves only what the weights move.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone
from statistics import median
from typing import Optional

from src.data import db as _db
from src.memory import valuation_outcomes as vo
from src.memory import walk_forward as wf

logger = logging.getLogger(__name__)

MIN_CELL_RUNS = 20
MIN_HOLDOUT = 5
HOLDOUT_SHARE = 0.30
HOLDOUT_MIN_GAIN = 0.01
WEIGHT_MOVE_CAP = 0.10
LOG_BIAS_CAP = 0.10
MIN_LOG_BIAS = 0.01
SHRINK = 10.0
L2 = 0.5
FIT_STEPS = 150
FIT_LR = 0.05
SHADOW_MIN_DAYS = 28
HORIZON_PREFERENCE = ("px_365d", "px_180d", "px_90d", "consensus_0d")

# ── pt family (self-learning loops 3 and 4, 2026-10-04) ─────────────────────
# The 12-month target is spot + capture x (IV - spot) per scenario, probability-weighted.
# Both the capture (per profile, else per market) and a shrink of the LLM's scenario
# probabilities toward 25/50/25 are recomputable from what a run stored, so the same
# walk-forward and shadow gates score them -- on the long price horizons only; the
# target is the figure the owner refused to fit toward the Street.
PT_HORIZONS = ("px_365d", "px_180d")
CAPTURE_GRID = [round(0.05 + 0.01 * i, 2) for i in range(56)]      # 0.05 .. 0.60
CAPTURE_MOVE_CAP = 0.10
LAMBDA_GRID = [round(0.05 * i, 2) for i in range(21)]              # 0 .. 1
NEUTRAL_PROBS = (0.25, 0.50, 0.25)

_DDL_VERSIONS = """
CREATE TABLE IF NOT EXISTS calibration_versions (
    version_id    TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    horizon       TEXT NOT NULL,
    status        TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    fit_json      TEXT,
    backtest_json TEXT,
    promoted_at   TEXT,
    family        TEXT NOT NULL DEFAULT 'iv'
)
"""
_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS calibration_fit_runs (
    week        TEXT PRIMARY KEY,
    finished_at TEXT NOT NULL,
    report      TEXT
)
"""
_tables_ready_key: Optional[tuple] = None


def _ensure_tables() -> None:
    global _tables_ready_key
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL_VERSIONS)
    _db.ensure_table(_DDL_RUNS)
    # Self-learning layer: one active version per family (iv / pt / est). Tables created
    # before 2026-10 lack the column; every existing row is an iv version.
    _db.add_column_if_missing("calibration_versions", "family", "TEXT NOT NULL DEFAULT 'iv'")
    _tables_ready_key = key


_VERSION_COLUMNS = ["version_id", "created_at", "horizon", "status", "params_json",
                    "fit_json", "backtest_json", "promoted_at", "family"]


def record_version(*, version_id: str, horizon: str, status: str, params: dict,
                   fit: dict, backtest: dict, family: str = "iv",
                   today: Optional[date] = None) -> None:
    """Store a proposal. A new shadow supersedes the older shadows OF ITS FAMILY only."""
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    if status == "shadow":
        _db.execute("UPDATE calibration_versions SET status = 'superseded' "
                    "WHERE status = 'shadow' AND family = ?", [family])
    _db.execute(
        vo._upsert_sql("calibration_versions", "version_id", _VERSION_COLUMNS),
        [version_id, today.isoformat(), horizon, status, json.dumps(params),
         json.dumps(fit, default=str), json.dumps(backtest, default=str), None, family])


# ── re-blending from stored method values ───────────────────────────────────

def _profile_methods(sector: str, profile: str) -> list[dict]:
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
    spec = (INDUSTRY_VALUATION_PROFILES.get("RealEstate" if sector == "REIT" else sector)
            or {}).get(profile) or {}
    return [m for m in (spec.get("methods") or []) if isinstance(m, dict) and m.get("name")]


def _cell_of(row: dict) -> Optional[tuple[str, str]]:
    dr = row.get("dcf") or {}
    rt = dr.get("routing_trace") or {}
    profile = rt.get("final_profile") or dr.get("profile") or row.get("profile")
    if not profile:
        return None
    sector = rt.get("final_sector")
    if not sector:
        from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES
        hits = [s for s, ps in INDUSTRY_VALUATION_PROFILES.items()
                if isinstance(ps, dict) and profile in ps]
        if len(hits) != 1:
            return None          # ambiguous profile name: do not guess the cell
        sector = hits[0]
    return sector, profile


def _values(row: dict, methods: list[dict]) -> list[Optional[float]]:
    table = ((row.get("dcf") or {}).get("base") or {}).get("method_iv_table") or {}
    return [vo._pos(table.get(m["name"])) or vo._pos(table.get(m.get("proxy")))
            for m in methods]


def _blend(weights: list[float], values: list[Optional[float]]) -> Optional[float]:
    num = den = 0.0
    for w, v in zip(weights, values):
        if v and w > 0:
            num += w * v
            den += w
    return num / den if den > 0 else None


def _ratio(weights, w0, values) -> Optional[float]:
    b, b0 = _blend(weights, values), _blend(w0, values)
    return b / b0 if b and b0 else None


# ── weight fit ──────────────────────────────────────────────────────────────

def _project_simplex(v: list[float]) -> list[float]:
    u = sorted(v, reverse=True)
    css, theta = 0.0, 0.0
    for i, ui in enumerate(u):
        css += ui
        t = (css - 1.0) / (i + 1)
        if ui - t > 0:
            theta = t
    return [max(x - theta, 0.0) for x in v]


def _sq_loss(samples, w, w0) -> float:
    tot, n = 0.0, 0
    for live, label, values, _ in samples:
        r = _ratio(w, w0, values)
        if r:
            e = math.log(live * r / label)
            tot += e * e
            n += 1
    return (tot / n if n else 0.0) + L2 * sum((a - b) ** 2 for a, b in zip(w, w0))


def _mean_abs(samples, w, w0) -> Optional[float]:
    errs = []
    for live, label, values, _ in samples:
        r = _ratio(w, w0, values)
        if r:
            errs.append(abs(math.log(live * r / label)))
    return sum(errs) / len(errs) if errs else None


def fit_cell_weights(samples, w0: list[float], *, steps: int = FIT_STEPS,
                     lr: float = FIT_LR, cap: float = WEIGHT_MOVE_CAP) -> list[float]:
    """Weights on the simplex minimising squared log error, shrunk toward w0,
    each moving at most `cap` from w0."""
    w = list(w0)
    for _ in range(steps):
        base = _sq_loss(samples, w, w0)
        grad = []
        for i in range(len(w)):
            bumped = list(w)
            bumped[i] += 1e-4
            grad.append((_sq_loss(samples, bumped, w0) - base) / 1e-4)
        w = _project_simplex([wi - lr * gi for wi, gi in zip(w, grad)])
    for _ in range(20):
        w = [max(0.0, min(max(wi, b - cap), b + cap)) for wi, b in zip(w, w0)]
        total = sum(w)
        w = [wi / total for wi in w] if total > 0 else list(w0)
    return w


# ── proposal (pure) ─────────────────────────────────────────────────────────

def propose(rows: list[dict], *, horizon: str) -> dict:
    """Fit a proposal from ledger rows for one horizon. No I/O."""
    by_cell: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        cell = _cell_of(r)
        if cell:
            by_cell.setdefault(cell, []).append(r)

    profile_weights: dict[str, dict[str, float]] = {}
    cells: dict[str, dict] = {}
    for (sector, profile), rs in sorted(by_cell.items()):
        key = f"{sector}|{profile}"
        methods = _profile_methods(sector, profile)
        rep: dict = {"n": len(rs)}
        cells[key] = rep
        total = sum(float(m.get("weight") or 0.0) for m in methods)
        if len(methods) < 2 or total <= 0:
            rep["status"] = "fewer_than_two_methods"
            continue
        w0 = [float(m.get("weight") or 0.0) / total for m in methods]
        samples = sorted(
            ((float(r["base_iv"]), float(r["label_value"]), _values(r, methods), r["run_date"])
             for r in rs), key=lambda s: s[3])
        samples = [s for s in samples if _blend(w0, s[2])]
        rep["usable"] = len(samples)
        if len(samples) < MIN_CELL_RUNS:
            rep["status"] = "insufficient_data"
            continue
        cut = int(len(samples) * (1 - HOLDOUT_SHARE))
        train, hold = samples[:cut], samples[cut:]
        if len(hold) < MIN_HOLDOUT:
            rep["status"] = "insufficient_holdout"
            continue
        w = fit_cell_weights(train, w0)
        live_h, cand_h = _mean_abs(hold, w0, w0), _mean_abs(hold, w, w0)
        rep.update(
            holdout_live_miss_pct=round((math.exp(live_h) - 1) * 100, 2),
            holdout_cand_miss_pct=round((math.exp(cand_h) - 1) * 100, 2),
            weights_before={m["name"]: round(x, 4) for m, x in zip(methods, w0)},
            weights_after={m["name"]: round(x, 4) for m, x in zip(methods, w)})
        if live_h - cand_h > HOLDOUT_MIN_GAIN:
            profile_weights[key] = {m["name"]: round(x * total, 6)
                                    for m, x in zip(methods, w)}
            rep["status"] = "proposed"
        else:
            rep["status"] = "no_holdout_gain"

    # market bias on the residual left after the weights
    resid: dict[str, list[tuple[date, float]]] = {}
    for r in rows:
        k = 1.0
        cell = _cell_of(r)
        if cell and f"{cell[0]}|{cell[1]}" in profile_weights:
            methods = _profile_methods(*cell)
            override = profile_weights[f"{cell[0]}|{cell[1]}"]
            w0 = [float(m.get("weight") or 0.0) for m in methods]
            w = [float(override.get(m["name"], m.get("weight") or 0.0)) for m in methods]
            k = _ratio(w, w0, _values(r, methods)) or 1.0
        resid.setdefault(r.get("market") or "?", []).append(
            (r["run_date"], math.log(float(r["base_iv"]) * k / float(r["label_value"]))))

    multipliers: dict[str, float] = {}
    markets: dict[str, dict] = {}
    for market, items in sorted(resid.items()):
        items.sort(key=lambda x: x[0])
        errs = [e for _, e in items]
        rep = {"n": len(errs)}
        markets[market] = rep
        if len(errs) < MIN_CELL_RUNS:
            rep["status"] = "insufficient_data"
            continue
        cut = int(len(errs) * (1 - HOLDOUT_SHARE))
        train, hold = errs[:cut], errs[cut:]
        if len(hold) < MIN_HOLDOUT:
            rep["status"] = "insufficient_holdout"
            continue
        b = median(train) * len(train) / (len(train) + SHRINK)
        b = max(-LOG_BIAS_CAP, min(LOG_BIAS_CAP, b))
        live_h = sum(abs(e) for e in hold) / len(hold)
        cand_h = sum(abs(e - b) for e in hold) / len(hold)
        rep.update(bias_pct=vo._pct(b), holdout_live_miss_pct=round((math.exp(live_h) - 1) * 100, 2),
                   holdout_cand_miss_pct=round((math.exp(cand_h) - 1) * 100, 2))
        if abs(b) > MIN_LOG_BIAS and live_h - cand_h > HOLDOUT_MIN_GAIN:
            multipliers[market] = round(math.exp(-b), 6)
            rep["status"] = "proposed"
        else:
            rep["status"] = "no_holdout_gain"

    return {"horizon": horizon, "n_rows": len(rows),
            "params": {"profile_weights": profile_weights,
                       "market_iv_multiplier": multipliers},
            "cells": cells, "markets": markets}


def predictor(params: dict):
    """walk_forward-compatible predict(row) for a set of calibration params."""
    pw = params.get("profile_weights") or {}
    mm = params.get("market_iv_multiplier") or {}

    def predict(row: dict) -> Optional[float]:
        k, changed = 1.0, False
        cell = _cell_of(row)
        key = f"{cell[0]}|{cell[1]}" if cell else None
        if key in pw:
            methods = _profile_methods(*cell)
            w0 = [float(m.get("weight") or 0.0) for m in methods]
            w = [float(pw[key].get(m["name"], m.get("weight") or 0.0)) for m in methods]
            ratio = _ratio(w, w0, _values(row, methods))
            if ratio:
                k *= ratio
                changed = True
        mult = mm.get(row.get("market") or "?")
        if mult:
            k *= float(mult)
            changed = True
        return float(row["base_iv"]) * k if changed else None

    return predict


# ── pt family: capture and probability shrink ───────────────────────────────

def _pt_scopes(row: dict) -> list[str]:
    out = []
    if row.get("profile"):
        out.append(f"profile:{row['profile']}")
    if row.get("market"):
        out.append(f"market:{row['market']}")
    return out


def _probs(row: dict, lam: Optional[float] = None) -> Optional[tuple[float, float, float]]:
    p = tuple(vo._pos(row.get(k)) or (0.0 if row.get(k) == 0 else None)
              for k in ("prob_bear", "prob_base", "prob_bull"))
    if any(v is None for v in p):
        return None
    tot = sum(p)
    if tot <= 0:
        return None
    p = tuple(v / tot for v in p)
    if lam:
        p = tuple((1.0 - lam) * v + lam * q for v, q in zip(p, NEUTRAL_PROBS))
    return p


def pt_from(row: dict, *, capture: Optional[float] = None, lam: Optional[float] = None) -> Optional[float]:
    """The engine's 12-month target recomputed from the row: spot + c x (IV_s - spot) per
    scenario, probability-weighted. `capture` None keeps the row's recorded capture;
    `lam` None keeps its recorded probabilities. None when the row cannot be recomputed."""
    spot = vo._pos(row.get("spot"))
    c = capture if capture is not None else vo._pos(row.get("capture"))
    if not spot or c is None:
        return None
    ivs = [vo._pos(row.get(k)) for k in ("iv_bear", "iv_base", "iv_bull")]
    probs = _probs(row, lam)
    if probs and all(ivs):
        return sum(p * (spot + c * (iv - spot)) for p, iv in zip(probs, ivs))
    if ivs[1]:
        return spot + c * (ivs[1] - spot)
    return None


def _pt_err(row: dict, pt: Optional[float]) -> Optional[float]:
    return math.log(pt / float(row["label_value"])) if pt and pt > 0 else None


def _mean_abs_pt(rows: list[dict], **kw) -> Optional[float]:
    errs = [_pt_err(r, pt_from(r, **kw)) for r in rows]
    errs = [e for e in errs if e is not None]
    return (sum(abs(e) for e in errs) / len(errs)) if errs else None


def propose_pt(rows: list[dict], *, horizon: str, current: Optional[dict] = None) -> dict:
    """Fit the pt-family proposal from pt rows (walk_forward.load_rows(h, family="pt")).
    No I/O. Refuses any horizon outside PT_HORIZONS."""
    if horizon not in PT_HORIZONS:
        raise ValueError(f"the pt family is fitted on {PT_HORIZONS} only, not {horizon!r}")
    current = current or {}
    cur_c = dict(current.get("capture") or {})
    rows = sorted((r for r in rows if pt_from(r) is not None), key=lambda r: r["run_date"])
    by_scope: dict[str, list[dict]] = {}
    for r in rows:
        for sc in _pt_scopes(r):
            by_scope.setdefault(sc, []).append(r)

    captures: dict[str, float] = {}
    scopes: dict[str, dict] = {}
    for scope, rs in sorted(by_scope.items()):
        rep_s: dict = {"n": len(rs)}
        scopes[scope] = rep_s
        if len(rs) < MIN_CELL_RUNS:
            rep_s["status"] = "insufficient_data"
            continue
        cut = int(len(rs) * (1 - HOLDOUT_SHARE))
        train, hold = rs[:cut], rs[cut:]
        if len(hold) < MIN_HOLDOUT:
            rep_s["status"] = "insufficient_holdout"
            continue
        recorded = median(float(r["capture"]) for r in train if vo._pos(r.get("capture")))
        best_c = min(CAPTURE_GRID, key=lambda c: _mean_abs_pt(train, capture=c) or 9.9)
        n = len(train)
        shrunk = recorded + (best_c - recorded) * n / (n + SHRINK)
        anchor = float(cur_c.get(scope, recorded))
        proposed = anchor + max(-CAPTURE_MOVE_CAP, min(CAPTURE_MOVE_CAP, shrunk - anchor))
        proposed = round(max(CAPTURE_GRID[0], min(CAPTURE_GRID[-1], proposed)), 4)
        live_h, cand_h = _mean_abs_pt(hold), _mean_abs_pt(hold, capture=proposed)
        rep_s.update(recorded_capture=round(recorded, 4), fitted=best_c, shrunk=round(shrunk, 4),
                     proposed=proposed,
                     holdout_live_miss_pct=round((math.exp(live_h) - 1) * 100, 2) if live_h is not None else None,
                     holdout_cand_miss_pct=round((math.exp(cand_h) - 1) * 100, 2) if cand_h is not None else None)
        if live_h is not None and cand_h is not None and live_h - cand_h > HOLDOUT_MIN_GAIN \
                and abs(proposed - anchor) >= 0.005:
            captures[scope] = proposed
            rep_s["status"] = "proposed"
        else:
            rep_s["status"] = "no_holdout_gain"

    # probability shrink: one pooled scalar, scored on the capture the proposal would use
    def _c_for(r):
        for sc in _pt_scopes(r):
            if sc in captures:
                return captures[sc]
        return None

    lam_rep: dict = {"n": len(rows)}
    lam_val: Optional[float] = None
    with_p = [r for r in rows if _probs(r) is not None]
    lam_rep["n_with_probabilities"] = len(with_p)
    if len(with_p) >= MIN_CELL_RUNS:
        cut = int(len(with_p) * (1 - HOLDOUT_SHARE))
        train, hold = with_p[:cut], with_p[cut:]
        if len(hold) >= MIN_HOLDOUT:
            def score(rs, lam):
                errs = [_pt_err(r, pt_from(r, capture=_c_for(r), lam=lam)) for r in rs]
                errs = [e for e in errs if e is not None]
                return sum(abs(e) for e in errs) / len(errs) if errs else None
            best = min(LAMBDA_GRID, key=lambda l: score(train, l) or 9.9)
            live_h, cand_h = score(hold, 0.0), score(hold, best)
            lam_rep.update(fitted=best,
                           holdout_live_miss_pct=round((math.exp(live_h) - 1) * 100, 2) if live_h is not None else None,
                           holdout_cand_miss_pct=round((math.exp(cand_h) - 1) * 100, 2) if cand_h is not None else None)
            if best > 0 and live_h is not None and cand_h is not None and live_h - cand_h > HOLDOUT_MIN_GAIN:
                lam_val = best
                lam_rep["status"] = "proposed"
            else:
                lam_rep["status"] = "no_holdout_gain"
        else:
            lam_rep["status"] = "insufficient_holdout"
    else:
        lam_rep["status"] = "insufficient_data"

    return {"horizon": horizon, "n_rows": len(rows),
            "params": {"capture": captures,
                       "scenario_prob_shrink": ({"lambda": lam_val} if lam_val is not None else {})},
            "scopes": scopes, "lambda": lam_rep}


def predictor_pt(params: dict):
    """walk_forward-compatible predict(row) -> revised 12-month target, or None when the
    params touch nothing the row carries."""
    caps = params.get("capture") or {}
    lam = (params.get("scenario_prob_shrink") or {}).get("lambda")

    def predict(row: dict) -> Optional[float]:
        c = None
        for sc in _pt_scopes(row):
            if sc in caps:
                c = float(caps[sc])
                break
        if c is None and not lam:
            return None
        if lam and _probs(row) is None and c is None:
            return None
        return pt_from(row, capture=c, lam=lam)

    return predict


def scenario_reliability(horizon: str, rows: Optional[list[dict]] = None) -> dict:
    """Were the scenario probabilities honest? The realised scenario is the one whose
    target the price landed nearest; per bucket of the base probability, the predicted vs
    realised base share. Price horizons only."""
    if horizon not in vo.HORIZON_DAYS:
        raise ValueError("scenario reliability is read on price horizons only")
    rows = wf.load_rows(horizon, family="pt") if rows is None else rows
    usable = []
    for r in rows:
        probs = _probs(r)
        pts = [vo._pos(r.get(k)) for k in ("pt_bear", "pt_base", "pt_bull")]
        if probs and all(pts):
            label = float(r["label_value"])
            realised = min(range(3), key=lambda i: abs(label - pts[i]))
            usable.append((probs, realised))
    names = ("bear", "base", "bull")
    out = {"horizon": horizon, "n": len(usable),
           "status": "ok" if len(usable) >= MIN_CELL_RUNS else "insufficient", "need": MIN_CELL_RUNS}
    if not usable:
        return out
    out["mean_predicted"] = {n: round(sum(p[i] for p, _ in usable) / len(usable), 3) for i, n in enumerate(names)}
    out["realised_share"] = {n: round(sum(1 for _, rz in usable if rz == i) / len(usable), 3) for i, n in enumerate(names)}
    buckets: dict[str, list] = {}
    for probs, rz in usable:
        lo = int(probs[1] * 10) / 10.0
        buckets.setdefault(f"{lo:.1f}-{lo + 0.1:.1f}", []).append((probs[1], rz == 1))
    out["base_probability_buckets"] = {
        b: {"n": len(v), "mean_predicted": round(sum(p for p, _ in v) / len(v), 3),
            "realised_share": round(sum(1 for _, hit in v if hit) / len(v), 3)}
        for b, v in sorted(buckets.items())}
    return out


# ── recording ───────────────────────────────────────────────────────────────

def _choose_horizon(horizon: Optional[str], family: str = "iv") -> tuple[Optional[str], dict]:
    counts = {}
    pref = PT_HORIZONS if family == "pt" else HORIZON_PREFERENCE
    for h in ([horizon] if horizon else pref):
        counts[h] = len(wf.load_rows(h, family="pt")) if family == "pt" else len(wf.load_rows(h))
        if counts[h] >= MIN_CELL_RUNS:
            return h, counts
    return None, counts


def _fit_and_record_pt(*, horizon: Optional[str], today: date, write: bool) -> dict:
    from src.memory import calibration as cal
    chosen, counts = _choose_horizon(horizon, "pt")
    if chosen is None:
        return {"status": "insufficient_data", "family": "pt", "rows_by_horizon": counts,
                "min_rows": MIN_CELL_RUNS}
    rows = wf.load_rows(chosen, family="pt")
    current = (cal.active_version("pt") or {}).get("params") or {}
    proposal = propose_pt(rows, horizon=chosen, current=current)
    params = proposal["params"]
    base = {"family": "pt", "horizon": chosen, "rows_by_horizon": counts,
            "scopes": proposal["scopes"], "lambda": proposal["lambda"]}
    if not params["capture"] and not params["scenario_prob_shrink"]:
        return {"status": "no_change", **base}
    try:
        backtest = wf.walk_forward(
            rows, lambda train: predictor_pt(propose_pt(train, horizon=chosen, current=current)["params"]),
            value_key="pt_12m", band_keys=("pt_bear", "pt_bull"))
    except wf.LeakageError as exc:
        backtest = {"verdict": {"passed": False, "reasons": [f"leakage: {exc}"]}}
    status = "shadow" if backtest["verdict"]["passed"] else "rejected"
    digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    version_id = f"calpt-{today.isoformat()}-{digest}"
    if write:
        record_version(version_id=version_id, horizon=chosen, status=status, params=params,
                       fit=base, backtest=backtest, family="pt", today=today)
    return {"status": status, "version_id": version_id, "params": params,
            "backtest_verdict": backtest["verdict"], "written": write, **base}


def fit_and_record(*, horizon: Optional[str] = None, today: Optional[date] = None,
                   write: bool = True, family: str = "iv") -> dict:
    """Fit a proposal, backtest it, and store it as shadow / rejected.
    family="pt" fits the capture and the probability shrink on the price horizons."""
    vo._ensure_tables()
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    if family == "pt":
        return _fit_and_record_pt(horizon=horizon, today=today, write=write)
    chosen, counts = _choose_horizon(horizon)
    if chosen is None:
        return {"status": "insufficient_data", "rows_by_horizon": counts,
                "min_rows": MIN_CELL_RUNS}
    rows = wf.load_rows(chosen)
    proposal = propose(rows, horizon=chosen)
    params = proposal["params"]
    base = {"horizon": chosen, "rows_by_horizon": counts,
            "cells": proposal["cells"], "markets": proposal["markets"]}
    if not params["profile_weights"] and not params["market_iv_multiplier"]:
        return {"status": "no_change", **base}

    try:
        backtest = wf.walk_forward(
            rows, lambda train: predictor(propose(train, horizon=chosen)["params"]))
    except wf.LeakageError as exc:
        backtest = {"verdict": {"passed": False, "reasons": [f"leakage: {exc}"]}}
    status = "shadow" if backtest["verdict"]["passed"] else "rejected"
    digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    version_id = f"cal-{today.isoformat()}-{digest}"

    if write:
        record_version(version_id=version_id, horizon=chosen, status=status, params=params,
                       fit=base, backtest=backtest, family="iv", today=today)
    return {"status": status, "version_id": version_id, "params": params,
            "backtest_verdict": backtest["verdict"], "written": write, **base}


def mark_fit_run(report: dict, today: Optional[date] = None) -> None:
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    _db.execute(vo._upsert_sql("calibration_fit_runs", "week",
                               ["week", "finished_at", "report"]),
                [today.strftime("%G-W%V"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 json.dumps(report, default=str)[:4000]])


def fit_ran_this_week(today: Optional[date] = None) -> bool:
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    return _db.query_one("SELECT week FROM calibration_fit_runs WHERE week = ?",
                         [today.strftime("%G-W%V")]) is not None


def list_versions() -> list[dict]:
    _ensure_tables()
    rows = _db.query("SELECT version_id, created_at, horizon, status, params_json, "
                     "promoted_at FROM calibration_versions ORDER BY created_at DESC")
    out = []
    for r in rows:
        params = vo._loads(r["params_json"]) or {}
        out.append({"version_id": r["version_id"], "created_at": r["created_at"],
                    "horizon": r["horizon"], "status": r["status"],
                    "promoted_at": r["promoted_at"],
                    "cells": sorted((params.get("profile_weights") or {})),
                    "markets": params.get("market_iv_multiplier") or {}})
    return out


# ── B5: forward shadow ──────────────────────────────────────────────────────

def shadow_report(version_id: str, *, today: Optional[date] = None) -> dict:
    """Live vs the proposal on runs made AFTER it was proposed (FT2). An est-family
    proposal is scored on reported prints instead (estimate_outcomes.shadow_report)."""
    _ensure_tables()
    today = today or datetime.now(timezone.utc).date()
    row = _db.query_one("SELECT version_id, created_at, horizon, status, params_json, family "
                        "FROM calibration_versions WHERE version_id = ?", [version_id])
    if row is None:
        raise KeyError(version_id)
    if (row["family"] or "iv") == "est":
        from src.memory import estimate_outcomes as _eo
        return _eo.shadow_report(version_id, today=today)
    if (row["family"] or "iv") == "pt":
        return _shadow_report_pt(row, today)
    params = vo._loads(row["params_json"]) or {}
    created = date.fromisoformat(str(row["created_at"])[:10])
    predict = predictor(params)
    cells = set(params.get("profile_weights") or {})
    markets = set(params.get("market_iv_multiplier") or {})
    days = (today - created).days

    horizons = list(dict.fromkeys([row["horizon"], vo.CONSENSUS]))
    report = {"version_id": version_id, "status": row["status"],
              "created_at": created.isoformat(), "days_in_shadow": days,
              "horizons": {}}
    reasons: list[str] = []
    if days < SHADOW_MIN_DAYS:
        reasons.append(f"{days} day(s) in shadow; need {SHADOW_MIN_DAYS}")

    for h in horizons:
        rows = [r for r in wf.load_rows(h) if r["run_date"] > created]
        scored = [s for s in wf._scored(rows, predict) if s["cand_err"] != s["live_err"]]
        per_key: dict[str, list[dict]] = {}
        for s in scored:
            cell = _cell_of(s)
            if cell and f"{cell[0]}|{cell[1]}" in cells:
                per_key.setdefault(f"{cell[0]}|{cell[1]}", []).append(s)
            if (s.get("market") or "?") in markets:
                per_key.setdefault(f"market:{s.get('market')}", []).append(s)
        live_miss = wf._miss_pct([s["live_err"] for s in scored])
        cand_miss = wf._miss_pct([s["cand_err"] for s in scored])
        report["horizons"][h] = {
            "n_touched": len(scored), "live_miss_pct": live_miss, "cand_miss_pct": cand_miss,
            "by_key": {k: {"n": len(v), "live_miss_pct": wf._miss_pct([s["live_err"] for s in v]),
                           "cand_miss_pct": wf._miss_pct([s["cand_err"] for s in v])}
                       for k, v in sorted(per_key.items())},
        }
        if h == row["horizon"]:
            wanted = [f"{c}" for c in sorted(cells)] + [f"market:{m}" for m in sorted(markets)]
            for k in wanted:
                n = len(per_key.get(k, []))
                if n < MIN_CELL_RUNS:
                    reasons.append(f"{k}: {n} labelled run(s) in shadow on {h}; need {MIN_CELL_RUNS}")
        if scored and cand_miss is not None and live_miss is not None and cand_miss >= live_miss:
            reasons.append(f"{h}: candidate miss {cand_miss}% not below live {live_miss}%")
        if h == row["horizon"] and not scored:
            reasons.append(f"{h}: no labelled runs touched since the proposal")

    report["eligible_for_promotion"] = not reasons and row["status"] == "shadow"
    if row["status"] != "shadow":
        reasons.append(f"status is {row['status']!r}, not 'shadow'")
    report["reasons"] = reasons
    return report


def _shadow_report_pt(row, today: date) -> dict:
    """The pt family's forward shadow: live target vs the proposal's on runs made after it
    was proposed, per touched scope, on its own price horizon."""
    params = vo._loads(row["params_json"]) or {}
    created = date.fromisoformat(str(row["created_at"])[:10])
    predict = predictor_pt(params)
    keys = sorted(params.get("capture") or {})
    if (params.get("scenario_prob_shrink") or {}).get("lambda"):
        keys.append("lambda")
    days = (today - created).days
    report = {"version_id": row["version_id"], "status": row["status"], "family": "pt",
              "created_at": created.isoformat(), "days_in_shadow": days, "horizons": {}}
    reasons: list[str] = []
    if days < SHADOW_MIN_DAYS:
        reasons.append(f"{days} day(s) in shadow; need {SHADOW_MIN_DAYS}")
    h = row["horizon"]
    rows = [r for r in wf.load_rows(h, family="pt") if r["run_date"] > created]
    scored = [s for s in wf._scored(rows, predict, value_key="pt_12m", band_keys=("pt_bear", "pt_bull"))
              if s["cand_err"] != s["live_err"]]
    per_key: dict[str, list[dict]] = {}
    for s_ in scored:
        for sc in _pt_scopes(s_):
            if sc in (params.get("capture") or {}):
                per_key.setdefault(sc, []).append(s_)
        if "lambda" in keys and _probs(s_) is not None:
            per_key.setdefault("lambda", []).append(s_)
    live_miss = wf._miss_pct([s_["live_err"] for s_ in scored])
    cand_miss = wf._miss_pct([s_["cand_err"] for s_ in scored])
    report["horizons"][h] = {
        "n_touched": len(scored), "live_miss_pct": live_miss, "cand_miss_pct": cand_miss,
        "by_key": {k: {"n": len(v), "live_miss_pct": wf._miss_pct([s_["live_err"] for s_ in v]),
                       "cand_miss_pct": wf._miss_pct([s_["cand_err"] for s_ in v])}
                   for k, v in sorted(per_key.items())}}
    for k in keys:
        n = len(per_key.get(k, []))
        if n < MIN_CELL_RUNS:
            reasons.append(f"{k}: {n} labelled run(s) in shadow on {h}; need {MIN_CELL_RUNS}")
    if scored and cand_miss is not None and live_miss is not None and cand_miss >= live_miss:
        reasons.append(f"{h}: candidate miss {cand_miss}% not below live {live_miss}%")
    if not scored:
        reasons.append(f"{h}: no labelled runs touched since the proposal")
    report["eligible_for_promotion"] = not reasons and row["status"] == "shadow"
    if row["status"] != "shadow":
        reasons.append(f"status is {row['status']!r}, not 'shadow'")
    report["reasons"] = reasons
    return report
