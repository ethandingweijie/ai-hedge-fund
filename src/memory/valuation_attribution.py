"""Where a valuation miss came from (B3).

valuation_outcomes says how far off a run was. This says why, from what the
run stored -- no engine re-run, no fetch, no LLM:

  weights    re-weighting the methods the blend carried would have come much
             closer: they straddled the label, or the closest one was near it
  routing    a profile another routing layer picked would have been closer
  shared_method_bias   every weighted method missed in the same direction,
             so no re-weighting could have fixed it -- inputs or parameters
  method_values        none of the above clearly dominates

Everything is scored on the IV layer against the label, in log space. Two
further layers are reported separately rather than as causes: how much the
engine's 12-month target and the PM's target add to or remove from the IV
miss.

Honest limits, reported alongside the numbers:
  * rows before the prediction ledger have no effective weights; their blend
    is approximated from nominal profile weights over the methods that
    produced a value ("weights_source": "nominal")
  * a routing alternative is priced with TODAY's profile weights over the
    run's own method values, and only when those values cover at least 60%
    of the alternative's weight
  * "weights" compares against the best method in hindsight. It says a
    better blend existed for that run, not that a fixed re-weighting would
    find it -- that is what the calibration fit is for.
"""
from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Optional

from src.data import db as _db
from src.memory import valuation_outcomes as vo

#: A cause must move the miss by more than this (log, ~10.5%) to be named.
CAUSE_THRESHOLD = 0.10
#: Share of an alternative profile's weight the run's method values must cover.
MIN_ALT_COVERAGE = 0.6


def _pos(v) -> Optional[float]:
    return vo._pos(v)


def _ln(value: Optional[float], label: float) -> Optional[float]:
    return math.log(value / label) if value and label else None


def _blend_weights(base: dict, table: dict[str, float]) -> tuple[dict[str, float], str]:
    """{value_key: share} and where the shares came from."""
    eff = base.get("effective_weights")
    if eff:
        out: dict[str, float] = {}
        for e in eff:
            key = e.get("value_key") or e.get("method")
            if key in table:
                out[key] = out.get(key, 0.0) + float(e.get("weight") or 0.0)
        if out:
            return out, "effective"
    nominal = {p.get("name"): float(p.get("weight") or 0.0)
               for p in (base.get("profile_weights") or []) if p.get("name") in table}
    total = sum(nominal.values())
    if total > 0:
        return {k: w / total for k, w in nominal.items()}, "nominal"
    return {}, "none"


def _alternatives(dr: dict, table: dict[str, float], label: float,
                  iv_err: float) -> list[dict]:
    """Profiles another routing layer chose or proposed, priced on this run's
    method values."""
    rt = dr.get("routing_trace") or {}
    final = (rt.get("final_sector"), rt.get("final_profile") or dr.get("profile"))
    candidates: list[tuple[str, str, str]] = []
    for step in rt.get("steps") or []:
        candidates.append((step.get("layer"), step.get("sector"), step.get("profile")))
    ir = rt.get("industry_routing") or {}
    if ir.get("routed_profile"):
        candidates.append(("industry_map", ir.get("routed_sector"), ir.get("routed_profile")))
    if not candidates:
        return []
    from src.data.sector_profiles import INDUSTRY_VALUATION_PROFILES

    out, seen = [], set()
    for layer, sector, profile in candidates:
        if not profile or (sector, profile) == final or profile == final[1] \
                or (sector, profile) in seen:
            continue
        seen.add((sector, profile))
        spec = (INDUSTRY_VALUATION_PROFILES.get("RealEstate" if sector == "REIT" else sector)
                or {}).get(profile) or {}
        methods = [m for m in (spec.get("methods") or []) if isinstance(m, dict)]
        total = sum(float(m.get("weight") or 0.0) for m in methods)
        num = covered = 0.0
        for m in methods:
            value = table.get(m.get("name")) or table.get(m.get("proxy"))
            if value:
                w = float(m.get("weight") or 0.0)
                num += w * value
                covered += w
        coverage = covered / total if total else 0.0
        alt = {"layer": layer, "sector": sector, "profile": profile,
               "coverage": round(coverage, 3), "iv": None, "gain": None}
        if coverage >= MIN_ALT_COVERAGE and covered > 0:
            alt_iv = num / covered
            alt_err = _ln(alt_iv, label)
            alt.update(iv=round(alt_iv, 4), err=round(alt_err, 6),
                       gain=round(abs(iv_err) - abs(alt_err), 6))
        out.append(alt)
    return out


def attribute_run(dr: dict, label: float) -> Optional[dict]:
    """Decompose one run's IV miss against one label. None if unscorable."""
    if not isinstance(dr, dict) or not _pos(label):
        return None
    base = dr.get("base") or {}
    iv = _pos(base.get("intrinsic_value"))
    if not iv:
        return None
    table = {k: float(v) for k, v in (base.get("method_iv_table") or {}).items() if _pos(v)}
    iv_err = _ln(iv, label)
    weights, weights_source = _blend_weights(base, table)
    method_err = {k: round(_ln(v, label), 6) for k, v in table.items()}

    carried = [k for k in weights if weights[k] > 0]
    best = min(carried, key=lambda k: abs(method_err[k])) if carried else None
    headroom = (abs(iv_err) - abs(method_err[best])) if best else None
    nonzero = [method_err[k] for k in carried if method_err[k] != 0]
    shared_bias = (len(carried) >= 2 and len(nonzero) == len(carried)
                   and (all(e > 0 for e in nonzero) or all(e < 0 for e in nonzero)))
    equal_iv = (sum(table[k] for k in carried) / len(carried)) if carried else None
    alternatives = _alternatives(dr, table, label, iv_err)
    routing_gain = max((a["gain"] for a in alternatives if a["gain"] is not None),
                       default=None)

    # A re-weighting can land anywhere between the carried methods. When they
    # straddle the label some blend hits it; when they all miss the same way,
    # the best any blend can do is the closest method -- so weights are the
    # cause only if that method was itself close. The first live report named
    # "weights" for TSLA, ARM and CRWD while every method missed the same way.
    weights_can_fix = headroom is not None and (
        not shared_bias or abs(method_err[best]) <= CAUSE_THRESHOLD)
    contributions = {"weights": headroom if weights_can_fix else None,
                     "routing": routing_gain}
    named = {k: v for k, v in contributions.items() if v is not None and v > CAUSE_THRESHOLD}
    if named:
        cause = max(named, key=named.get)
    elif shared_bias:
        cause = "shared_method_bias"
    else:
        cause = "method_values"

    return {
        "iv_err": round(iv_err, 6),
        "weights_source": weights_source, "weights": weights,
        "method_err": method_err, "best_method": best,
        "weight_headroom": None if headroom is None else round(headroom, 6),
        "equal_weight_err": None if equal_iv is None else round(_ln(equal_iv, label), 6),
        "shared_bias": shared_bias,
        "alternatives": alternatives,
        "routing_gain": routing_gain,
        "cause": cause,
    }


# ── report ──────────────────────────────────────────────────────────────────

def _load_dcf(keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    from src.memory import run_archive
    run_ids = sorted({r for r, _ in keys})
    out: dict[tuple[str, str], dict] = {}
    for i in range(0, len(run_ids), 100):
        chunk = run_ids[i:i + 100]
        rows = run_archive._fetch(
            "SELECT run_id, ticker, dcf_range_json FROM ticker_signals "
            f"WHERE run_id IN ({', '.join('?' * len(chunk))})", chunk)
        for r in rows:
            key = (r["run_id"], (r["ticker"] or "").upper())
            if key in keys:
                out[key] = vo._loads(r["dcf_range_json"]) or {}
    return out


def _signed(errs: list[float]) -> dict:
    errs = [e for e in errs if e is not None]
    if not errs:
        return {"n": 0, "signed_pct": None, "miss_factor": None}
    return {"n": len(errs), "signed_pct": vo._pct(median(errs)),
            "miss_factor": vo._factor(median(abs(e) for e in errs))}


def _change(diffs: list[float]) -> Optional[float]:
    """Median change in the size of the miss, as a factor: 1.10 = 10% larger."""
    diffs = [d for d in diffs if d is not None]
    return round(math.exp(median(diffs)), 3) if diffs else None


def attribution_report(horizon: str = vo.CONSENSUS,
                       group_by: tuple[str, ...] = ("market", "profile"),
                       worst_n: int = 10) -> dict:
    if horizon not in vo.HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}; allowed: {list(vo.HORIZONS)}")
    bad = [g for g in group_by if g not in vo._GROUPABLE]
    if bad:
        raise ValueError(f"cannot group by {bad}; allowed: {sorted(vo._GROUPABLE)}")
    vo._ensure_tables()
    cols = sorted(set(group_by) | {"run_id", "ticker", "run_date", "label_value",
                                   "iv_log_err", "pt_log_err", "pm_log_err"})
    rows = _db.query(f"SELECT {', '.join(cols)} FROM valuation_outcomes WHERE horizon = ?",
                     [horizon])
    dcf = _load_dcf({(r["run_id"], (r["ticker"] or "").upper()) for r in rows})

    groups: dict[str, dict] = {}
    worst: list[dict] = []
    for r in rows:
        dr = dcf.get((r["run_id"], (r["ticker"] or "").upper()))
        att = attribute_run(dr, r["label_value"]) if dr else None
        if att is None:
            continue
        label = " / ".join(str(r[g] or "?") for g in group_by) or "all"
        g = groups.setdefault(label, {
            "iv": [], "pt": [], "pm": [],
            "scenario_layer": [], "pm_layer": [], "equal": [],
            "methods": {}, "sources": Counter(), "causes": Counter(),
            "shared": 0, "alt_runs": 0, "alt_better": 0, "alt_gain": []})
        g["iv"].append(att["iv_err"])
        g["equal"].append(att["equal_weight_err"])
        g["pt"].append(r["pt_log_err"])
        g["pm"].append(r["pm_log_err"])
        if r["pt_log_err"] is not None:
            g["scenario_layer"].append(abs(r["pt_log_err"]) - abs(att["iv_err"]))
        if r["pm_log_err"] is not None:
            ref = r["pt_log_err"] if r["pt_log_err"] is not None else att["iv_err"]
            g["pm_layer"].append(abs(r["pm_log_err"]) - abs(ref))
        g["sources"][att["weights_source"]] += 1
        g["causes"][att["cause"]] += 1
        g["shared"] += int(att["shared_bias"])
        for key, err in att["method_err"].items():
            m = g["methods"].setdefault(key, {"err": [], "weight": []})
            m["err"].append(err)
            m["weight"].append(att["weights"].get(key, 0.0))
        priced = [a for a in att["alternatives"] if a["gain"] is not None]
        if priced:
            g["alt_runs"] += 1
            g["alt_better"] += int(att["routing_gain"] > 0)
            g["alt_gain"].append(att["routing_gain"])
        worst.append({"ticker": r["ticker"], "run_date": r["run_date"],
                      "group": label, "iv_miss_pct": vo._pct(att["iv_err"]),
                      "cause": att["cause"], "best_method": att["best_method"],
                      "shared_bias": att["shared_bias"],
                      "weights_source": att["weights_source"],
                      "_abs": abs(att["iv_err"])})

    out_groups = {}
    for label, g in sorted(groups.items()):
        n = len(g["iv"])
        out_groups[label] = {
            "n_runs": n,
            "weights_source": dict(g["sources"]),
            "layers": {"iv": _signed(g["iv"]),
                       "engine_12m_target": _signed(g["pt"]), "pm_target": _signed(g["pm"])},
            "scenario_layer_change_in_miss": _change(g["scenario_layer"]),
            "pm_layer_change_in_miss": _change(g["pm_layer"]),
            "equal_weight_blend": _signed(g["equal"]),
            "shared_bias_share": round(g["shared"] / n, 3) if n else None,
            "routing": {"runs_with_priced_alternative": g["alt_runs"],
                        "alternative_better_share": (round(g["alt_better"] / g["alt_runs"], 3)
                                                     if g["alt_runs"] else None),
                        "median_change_in_miss": _change([-x for x in g["alt_gain"]])},
            "methods": {k: {**_signed(m["err"]),
                            "median_weight": round(median(m["weight"]), 3)}
                        for k, m in sorted(g["methods"].items())},
            "causes": dict(g["causes"].most_common()),
        }
    worst.sort(key=lambda w: w["_abs"], reverse=True)
    for w in worst:
        w.pop("_abs")
    return {"horizon": horizon, "group_by": list(group_by),
            "cause_threshold_pct": round((math.exp(CAUSE_THRESHOLD) - 1) * 100, 1),
            "groups": out_groups, "worst": worst[:max(0, worst_n)]}
