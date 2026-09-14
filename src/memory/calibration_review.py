"""Human review of calibration proposals, and the live safeguards after (B6).

Turns what the loop measured into things a person can read and act on, and
is the ONLY path by which a calibration reaches the engine.

Two kinds of card:

  calibration  a proposal the system can apply -- "Raise US intrinsic values
               by 10.5%", "Re-weight Tech / Mature SaaS" -- with its backtest,
               its shadow record and, once eligible, a Promote action
  diagnostic   something the loop found but cannot fix by re-weighting --
               a method off by 7x, every method missing the same way, a
               routing layer that picked the wrong profile. These need a code
               change; they have no Promote action by construction

Promotion is refused unless the proposal is in shadow AND its shadow report
says eligible. At the moment of promotion the runs it would have changed are
frozen as a cohort (FT3), so its record over 90/180/365 days can never be
refit away. After promotion a daily canary (FT4) compares runs made under it
against the same runs with its effect divided out; if the calibration is
making things worse over enough labels, it is rolled back automatically and
the previous version -- or today's constants -- is restored.
"""
from __future__ import annotations

import hashlib
import logging
import math
import uuid
from datetime import date, datetime, timezone
from statistics import median
from typing import Optional

from src.data import db as _db
from src.memory import calibration
from src.memory import calibration_fit as cf
from src.memory import valuation_outcomes as vo
from src.memory import walk_forward as wf

logger = logging.getLogger(__name__)

CANARY_MIN_LABELS = 20
#: Rolled back when runs under the calibration miss by more than this much
#: more (median, log) than the same runs with its effect removed.
CANARY_TOLERANCE = math.log(1.02)
DIAG_MIN_N = 5
METHOD_MISS_FACTOR = 3.0
SHARED_BIAS_SHARE = 0.5
ROUTING_MIN_RUNS = 3
ROUTING_BETTER_SHARE = 0.6
MAX_DIAGNOSTICS = 20

_LABEL_NAMES = {vo.CONSENSUS: "Street consensus", "px_30d": "price 30 days later",
                "px_90d": "price 90 days later", "px_180d": "price 180 days later",
                "px_365d": "price a year later"}

_DDL_COHORT = """
CREATE TABLE IF NOT EXISTS calibration_cohorts (
    cohort_key TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    run_date   TEXT NOT NULL,
    live_iv    REAL NOT NULL,
    cand_iv    REAL NOT NULL,
    frozen_at  TEXT NOT NULL
)
"""
_DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS calibration_events (
    event_id   TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    action     TEXT NOT NULL,
    actor      TEXT,
    reason     TEXT,
    at         TEXT NOT NULL
)
"""
_tables_ready_key: Optional[tuple] = None


class PromotionRefused(RuntimeError):
    """The requested state change is not allowed for this version now."""


def _ensure_tables() -> None:
    global _tables_ready_key
    cf._ensure_tables()
    # The cohort report reads valuation_outcomes; on a database where the
    # outcome sweep has not run yet that table does not exist.
    vo._ensure_tables()
    key = ("pg",) if _db.is_postgres() else ("sqlite", _db.get_db_path())
    if key == _tables_ready_key:
        return
    _db.ensure_table(_DDL_COHORT)
    _db.ensure_table(_DDL_EVENTS)
    _tables_ready_key = key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version(version_id: str):
    row = _db.query_one(
        "SELECT version_id, created_at, horizon, status, params_json, fit_json, "
        "backtest_json, promoted_at FROM calibration_versions WHERE version_id = ?",
        [version_id])
    if row is None:
        raise KeyError(version_id)
    return row


def _event(version_id: str, action: str, actor: Optional[str], reason: str = "") -> None:
    _db.execute("INSERT INTO calibration_events (event_id, version_id, action, actor, "
                "reason, at) VALUES (?, ?, ?, ?, ?, ?)",
                [uuid.uuid4().hex, version_id, action, actor or "service",
                 (reason or "")[:1000], _now()])


# ── plain English ───────────────────────────────────────────────────────────

def describe_params(params: dict) -> list[str]:
    lines = []
    for market, k in sorted((params.get("market_iv_multiplier") or {}).items()):
        move = (float(k) - 1.0) * 100
        lines.append(f"{'Raise' if move > 0 else 'Lower'} {market} intrinsic values "
                     f"by {abs(move):.1f}%")
    for key, weights in sorted((params.get("profile_weights") or {}).items()):
        sector, _, profile = key.partition("|")
        current = {m["name"]: float(m.get("weight") or 0.0)
                   for m in cf._profile_methods(sector, profile)}
        changes = [f"{name} {current.get(name, 0.0):.2f} → {float(w):.2f}"
                   for name, w in weights.items()
                   if abs(float(w) - current.get(name, 0.0)) >= 0.005]
        lines.append(f"Re-weight {sector} / {profile}: " + (", ".join(changes) or "no material change"))
    return lines


def _stage(status: str, shadow: Optional[dict], promoted_at: Optional[str]) -> str:
    if status == "shadow" and shadow:
        if shadow["eligible_for_promotion"]:
            return "Eligible for promotion"
        return (f"In shadow — day {shadow['days_in_shadow']} of {cf.SHADOW_MIN_DAYS}")
    return {"rejected": "Rejected by the backtest",
            "active": f"Live since {str(promoted_at or '')[:10]}",
            "rolled_back": "Rolled back", "retired": "Retired",
            "superseded": "Superseded by a newer proposal",
            "dismissed": "Dismissed"}.get(status, status)


def version_card(row) -> dict:
    params = vo._loads(row["params_json"]) or {}
    fit = vo._loads(row["fit_json"]) or {}
    backtest = vo._loads(row["backtest_json"]) or {}
    shadow = None
    if row["status"] == "shadow":
        try:
            shadow = cf.shadow_report(row["version_id"])
        except Exception as exc:                           # noqa: BLE001
            logger.warning("calibration_review: shadow for %s failed: %s",
                           row["version_id"], exc)
    changes = describe_params(params)
    verdict = backtest.get("verdict") or {}
    evidence = {
        "markets": {m: rep for m, rep in (fit.get("markets") or {}).items()
                    if m in (params.get("market_iv_multiplier") or {})},
        "cells": {c: rep for c, rep in (fit.get("cells") or {}).items()
                  if c in (params.get("profile_weights") or {})},
    }
    return {
        "id": row["version_id"], "kind": "calibration",
        "title": changes[0] if len(changes) == 1 else f"{len(changes)} calibration changes",
        "changes": changes, "status": row["status"],
        "stage": _stage(row["status"], shadow, row["promoted_at"]),
        "horizon": row["horizon"], "label": _LABEL_NAMES.get(row["horizon"], row["horizon"]),
        "created_at": row["created_at"], "promoted_at": row["promoted_at"],
        "backtest": {"passed": bool(verdict.get("passed")),
                     "reasons": verdict.get("reasons") or [],
                     "folds": backtest.get("folds") or [],
                     "pooled": backtest.get("pooled")},
        "shadow": (None if shadow is None else
                   {k: shadow[k] for k in ("days_in_shadow", "eligible_for_promotion",
                                           "reasons", "horizons")}),
        "evidence": evidence,
        "actions": {"promote": bool(shadow and shadow["eligible_for_promotion"]),
                    "rollback": row["status"] == "active",
                    "dismiss": row["status"] in ("shadow", "rejected")},
    }


# ── diagnostics ─────────────────────────────────────────────────────────────

def _label_counts() -> dict[str, int]:
    rows = _db.query("SELECT horizon, COUNT(*) AS n FROM valuation_outcomes GROUP BY horizon")
    return {r["horizon"]: int(r["n"]) for r in rows}


def _diag_id(*parts) -> str:
    return "diag-" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def diagnostics(horizon: Optional[str] = None) -> tuple[Optional[str], list[dict]]:
    """Cards for what re-weighting cannot fix, from the scorecard and the
    attribution report on the longest horizon that has data."""
    from src.memory import valuation_attribution as va
    counts = _label_counts()
    if horizon is None:
        horizon = next((h for h in (*reversed(tuple(vo.HORIZON_DAYS)), vo.CONSENSUS)
                        if counts.get(h, 0) >= DIAG_MIN_N), None)
    if horizon is None:
        return None, []
    label = _LABEL_NAMES.get(horizon, horizon)
    cards: list[dict] = []

    card = vo.scorecard(("market",))
    for market, g in card["groups"].items():
        h = g["horizons"].get(horizon) or {}
        signed = h.get("median_signed_pct")
        if (h.get("n") or 0) >= DIAG_MIN_N and signed is not None and abs(signed) >= 10:
            cards.append({
                "id": _diag_id("bias", market, horizon), "kind": "diagnostic",
                "category": "bias", "severity": abs(signed) / 100,
                "title": f"{market} 12-month targets run {abs(signed):.0f}% "
                         f"{'above' if signed > 0 else 'below'} the {label}",
                "detail": (f"Median over {h['n']} runs; typical miss {h.get('median_miss_factor')}x, "
                           f"direction right {round((h.get('direction_hit_rate') or 0) * 100)}% "
                           f"of the time. A market-wide bias is what a calibration proposal "
                           f"corrects — see the proposals above."),
                "group": market, "horizon": horizon, "action": "calibration"})

    report = va.attribution_report(horizon, ("market", "profile"), worst_n=0)
    for group, g in report["groups"].items():
        n = g["n_runs"]
        if n < DIAG_MIN_N:
            continue
        for method, m in g["methods"].items():
            factor, weight = m.get("miss_factor"), m.get("median_weight") or 0.0
            if (m.get("n") or 0) >= DIAG_MIN_N and factor and factor >= METHOD_MISS_FACTOR \
                    and weight > 0:
                cards.append({
                    "id": _diag_id("method", group, method, horizon), "kind": "diagnostic",
                    "category": "method", "severity": math.log(factor),
                    "title": f"{method} misses by {factor}x on {group} runs",
                    "detail": (f"Across {m['n']} runs it carried {weight:.0%} of the blend in the "
                               f"typical run (signed {m.get('signed_pct')}%). Re-weighting cannot "
                               f"repair a method this far off — the method needs work."),
                    "group": group, "horizon": horizon, "action": "code_change"})
        share = g.get("shared_bias_share")
        if share is not None and share >= SHARED_BIAS_SHARE:
            cards.append({
                "id": _diag_id("shared", group, horizon), "kind": "diagnostic",
                "category": "shared_bias", "severity": share,
                "title": f"{group}: every method missed the same way in {share:.0%} of runs",
                "detail": ("The weights are not the problem here — the inputs or parameters "
                           "feeding all the methods are. Typical IV miss "
                           f"{g['layers']['iv'].get('miss_factor')}x over {n} runs."),
                "group": group, "horizon": horizon, "action": "code_change"})
        routing = g.get("routing") or {}
        priced, better = routing.get("runs_with_priced_alternative") or 0, routing.get("alternative_better_share")
        if priced >= ROUTING_MIN_RUNS and better is not None and better >= ROUTING_BETTER_SHARE:
            cards.append({
                "id": _diag_id("routing", group, horizon), "kind": "diagnostic",
                "category": "routing", "severity": better,
                "title": f"{group}: another routing layer's profile was closer in {better:.0%} "
                         f"of {priced} runs",
                "detail": "Review the profile routing for these names.",
                "group": group, "horizon": horizon, "action": "code_change"})

    cards.sort(key=lambda c: c["severity"], reverse=True)
    return horizon, cards[:MAX_DIAGNOSTICS]


# ── overview ────────────────────────────────────────────────────────────────

def overview() -> dict:
    _ensure_tables()
    vo._ensure_tables()
    rows = _db.query(
        "SELECT version_id, created_at, horizon, status, params_json, fit_json, "
        "backtest_json, promoted_at FROM calibration_versions ORDER BY created_at DESC")
    cards = [version_card(r) for r in rows[:25]]
    active = next((c for c in cards if c["status"] == "active"), None)
    horizon, diags = diagnostics()
    return {
        "generated_at": _now(),
        "labels": _label_counts(),
        "active": active,
        "proposals": [c for c in cards if c["status"] in ("shadow", "rejected")],
        "history": [c for c in cards if c["status"] not in ("shadow", "rejected", "active")],
        "diagnostics": diags, "diagnostics_horizon": horizon,
        "eligible_count": sum(1 for c in cards if c["actions"]["promote"]),
    }


def eligible_count() -> int:
    _ensure_tables()
    n = 0
    for r in _db.query("SELECT version_id FROM calibration_versions WHERE status = 'shadow'"):
        try:
            n += int(cf.shadow_report(r["version_id"])["eligible_for_promotion"])
        except Exception:                                  # noqa: BLE001
            continue
    return n


# ── state changes ───────────────────────────────────────────────────────────

def _runs_since(since: str) -> list[dict]:
    """Every archived run made on or after `since`, labelled or not, shaped
    like a walk_forward row."""
    from src.memory import run_archive
    rows = run_archive._fetch(
        "SELECT ts.run_id, ts.ticker, r.run_at, ts.dcf_range_json "
        "FROM ticker_signals ts JOIN runs r ON r.run_id = ts.run_id "
        "WHERE ts.dcf_range_json IS NOT NULL AND substr(r.run_at, 1, 10) >= ?",
        [since[:10]])
    out = []
    for r in rows:
        dr = vo._loads(r["dcf_range_json"]) or {}
        iv = vo._pos((dr.get("base") or {}).get("intrinsic_value"))
        run_date = wf._d(r["run_at"])
        if not iv or not run_date or dr.get("is_cache_copy"):
            continue
        ticker = (r["ticker"] or "").upper()
        out.append({"run_id": r["run_id"], "ticker": ticker, "run_date": run_date,
                    "base_iv": iv, "market": vo.market_of(ticker),
                    "profile": dr.get("profile"), "dcf": dr})
    return out


def promote(version_id: str, *, actor: Optional[str] = None,
            today: Optional[date] = None) -> dict:
    _ensure_tables()
    row = _version(version_id)
    if row["status"] != "shadow":
        raise PromotionRefused(f"{version_id} is {row['status']!r}; only a proposal in "
                               f"shadow can be promoted")
    report = cf.shadow_report(version_id, today=today)
    if not report["eligible_for_promotion"]:
        raise PromotionRefused("not eligible yet: " + "; ".join(report["reasons"]))

    params = vo._loads(row["params_json"]) or {}
    predict = cf.predictor(params)
    now = _now()
    cohort = []
    for r in _runs_since(str(row["created_at"])):
        cand = predict(r)
        if cand:
            cohort.append([f"{version_id}|{r['run_id']}|{r['ticker']}", version_id,
                           r["run_id"], r["ticker"], r["run_date"].isoformat(),
                           r["base_iv"], cand, now])

    _db.execute("UPDATE calibration_versions SET status = 'retired' WHERE status = 'active'")
    _db.execute("UPDATE calibration_versions SET status = 'active', promoted_at = ? "
                "WHERE version_id = ?", [now, version_id])
    if cohort:
        _db.executemany(
            vo._upsert_sql("calibration_cohorts", "cohort_key",
                           ["cohort_key", "version_id", "run_id", "ticker", "run_date",
                            "live_iv", "cand_iv", "frozen_at"]), cohort)
    _event(version_id, "promote", actor, f"cohort of {len(cohort)} run(s) frozen")
    calibration.clear_cache()
    return {"version_id": version_id, "status": "active", "promoted_at": now,
            "cohort_size": len(cohort)}


def rollback(version_id: str, *, actor: Optional[str] = None, reason: str = "") -> dict:
    _ensure_tables()
    row = _version(version_id)
    if row["status"] != "active":
        raise PromotionRefused(f"{version_id} is {row['status']!r}; only the active "
                               f"calibration can be rolled back")
    _db.execute("UPDATE calibration_versions SET status = 'rolled_back' WHERE version_id = ?",
                [version_id])
    prior = _db.query_one(
        "SELECT version_id FROM calibration_versions WHERE status = 'retired' "
        "AND promoted_at IS NOT NULL ORDER BY promoted_at DESC LIMIT 1")
    restored = None
    if prior is not None:
        restored = prior["version_id"]
        _db.execute("UPDATE calibration_versions SET status = 'active' WHERE version_id = ?",
                    [restored])
    _event(version_id, "rollback", actor,
           f"{reason} -> restored {restored or 'constants'}".strip())
    calibration.clear_cache()
    return {"rolled_back": version_id, "restored": restored or "constants"}


def dismiss(version_id: str, *, actor: Optional[str] = None, reason: str = "") -> dict:
    _ensure_tables()
    row = _version(version_id)
    if row["status"] not in ("shadow", "rejected"):
        raise PromotionRefused(f"{version_id} is {row['status']!r}; only a proposal can "
                               f"be dismissed")
    _db.execute("UPDATE calibration_versions SET status = 'dismissed' WHERE version_id = ?",
                [version_id])
    _event(version_id, "dismiss", actor, reason)
    return {"version_id": version_id, "status": "dismissed"}


# ── live safeguards ─────────────────────────────────────────────────────────

def canary_check(*, auto_rollback: bool = True) -> dict:
    """FT4: runs made under the active calibration vs the same runs with its
    effect divided out, on its own horizon."""
    _ensure_tables()
    row = _db.query_one("SELECT version_id, horizon, params_json, promoted_at FROM "
                        "calibration_versions WHERE status = 'active' "
                        "ORDER BY promoted_at DESC LIMIT 1")
    if row is None:
        return {"active": None}
    version_id = row["version_id"]
    predict = cf.predictor(vo._loads(row["params_json"]) or {})
    promoted = wf._d(row["promoted_at"])
    promoted_errs, prior_errs = [], []
    for r in wf.load_rows(row["horizon"]):
        if promoted and r["run_date"] < promoted:
            continue
        if not str((r.get("dcf") or {}).get("param_version") or "").startswith(version_id):
            continue                   # not actually made under this calibration
        with_it = predict(r)
        if not with_it:
            continue
        live = float(r["base_iv"])
        k = with_it / live
        label = float(r["label_value"])
        promoted_errs.append(abs(math.log(live / label)))
        prior_errs.append(abs(math.log((live / k) / label)))
    report = {"version_id": version_id, "horizon": row["horizon"], "n": len(promoted_errs)}
    if len(promoted_errs) < CANARY_MIN_LABELS:
        return {**report, "status": "collecting", "need": CANARY_MIN_LABELS}
    promoted_miss, prior_miss = median(promoted_errs), median(prior_errs)
    report.update(promoted_miss_pct=round((math.exp(promoted_miss) - 1) * 100, 2),
                  prior_miss_pct=round((math.exp(prior_miss) - 1) * 100, 2))
    if promoted_miss - prior_miss > CANARY_TOLERANCE:
        report["status"] = "regressed"
        if auto_rollback:
            report["rollback"] = rollback(
                version_id, actor="canary",
                reason=(f"live miss {report['promoted_miss_pct']}% vs "
                        f"{report['prior_miss_pct']}% without it over {len(promoted_errs)} labels"))
        return report
    return {**report, "status": "healthy"}


def cohort_report(version_id: str) -> dict:
    """FT3: the frozen cohort's record, horizon by horizon, never refit."""
    _ensure_tables()
    cohort = _db.query("SELECT run_id, ticker, live_iv, cand_iv FROM calibration_cohorts "
                       "WHERE version_id = ?", [version_id])
    if not cohort:
        return {"version_id": version_id, "n_frozen": 0, "horizons": {}}
    by_key = {(c["run_id"], c["ticker"]): c for c in cohort}
    run_ids = sorted({c["run_id"] for c in cohort})
    per_h: dict[str, list[tuple[float, float]]] = {}
    for i in range(0, len(run_ids), 100):
        chunk = run_ids[i:i + 100]
        for r in _db.query("SELECT run_id, ticker, horizon, label_value FROM valuation_outcomes "
                           f"WHERE run_id IN ({', '.join('?' * len(chunk))})", chunk):
            c = by_key.get((r["run_id"], (r["ticker"] or "").upper()))
            if c and vo._pos(r["label_value"]):
                label = float(r["label_value"])
                per_h.setdefault(r["horizon"], []).append(
                    (math.log(c["live_iv"] / label), math.log(c["cand_iv"] / label)))
    horizons = {}
    for h in vo.HORIZONS:
        pairs = per_h.get(h)
        if pairs:
            horizons[h] = {"n": len(pairs),
                           "live_miss_pct": wf._miss_pct([p[0] for p in pairs]),
                           "cand_miss_pct": wf._miss_pct([p[1] for p in pairs])}
    return {"version_id": version_id, "n_frozen": len(cohort), "horizons": horizons}


def detail(version_id: str) -> dict:
    _ensure_tables()
    row = _version(version_id)
    events = _db.query("SELECT action, actor, reason, at FROM calibration_events "
                       "WHERE version_id = ? ORDER BY at", [version_id])
    return {
        "card": version_card(row),
        "fit": vo._loads(row["fit_json"]) or {},
        "backtest": vo._loads(row["backtest_json"]) or {},
        "cohort": (cohort_report(version_id)
                   if row["status"] in ("active", "retired", "rolled_back") else None),
        "events": [{"action": e["action"], "actor": e["actor"], "reason": e["reason"],
                    "at": e["at"]} for e in events],
    }
