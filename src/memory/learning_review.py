"""The self-learning layer's one page of numbers (Phase G).

overview() gathers what every loop has learned so far, each block guarded so a loop that
has no table yet or no data reports `insufficient` rather than taking the page down:

  ledger            run_features counts and what the agent-only filter removes
  estimates         loop 1 scorecards by archetype / confidence / profile / market
  guidance          management's credibility (met-or-beaten and within-band shares)
  scenarios         loop 4 reliability per price horizon (dormant until labels mature)
  gates             loop 6 verdict shares per gate
  overrides         loop 5 agent vs user per horizon, and the fields users change
  prior_misses      loop 7: how many profile cells carry a gap today
  families          the active calibration version per family (iv / pt / est)
  kill_switches     every loop's switch and its state

Proposals themselves live where every calibration lives: calibration_review.overview().
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

SWITCHES = {
    "RUN_FEATURES_DISABLED": "run_features ledger",
    "ESTIMATE_OUTCOMES_DISABLED": "loop 1: estimate accuracy",
    "GATE_OUTCOMES_DISABLED": "loop 6: gate precision",
    "OVERRIDE_OUTCOMES_DISABLED": "loop 5: override outcomes",
    "VALUATION_CALIBRATION_DISABLED": "every calibration family (iv / pt / est) read by the engine",
    "VALUATION_OUTCOMES_DISABLED": "the daily outcomes job that runs the learning sweep",
    "CALIBRATION_FIT_DISABLED": "the weekly fit (iv, est, pt, gate review)",
    "PROFILE_LESSONS": "loop 7: profile lessons (on unless false)",
    "PRIOR_MISSES": "loop 7: prior misses in prompts (on unless false)",
    "AGENT_LESSONS": "M1 ticker lessons (on unless false)",
}


def _block(name: str, fn: Callable, out: dict, **kw) -> None:
    try:
        out[name] = fn(**kw)
    except Exception as exc:                               # noqa: BLE001
        logger.warning("[learning_review] %s failed: %s", name, exc)
        out[name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "status": "unavailable"}


def _switches() -> dict:
    out = {}
    for k, what in SWITCHES.items():
        raw = os.environ.get(k)
        if k.endswith("_DISABLED"):
            off = (raw or "").strip().lower() in ("1", "true", "yes", "on")
        else:
            off = (raw or "true").strip().lower() in ("0", "false", "no", "off", "")
        out[k] = {"what": what, "state": "off" if off else "on", "value": raw}
    return out


def _families() -> dict:
    from src.memory import calibration as cal
    out = {}
    for fam in cal.FAMILIES:
        v = cal.active_version(fam)
        out[fam] = {"version_id": v["version_id"], "params": v["params"]} if v else None
    return out


def _scenarios() -> dict:
    from src.memory import calibration_fit as cf
    return {h: cf.scenario_reliability(h) for h in cf.PT_HORIZONS}


def _estimates() -> dict:
    from src.memory import estimate_outcomes as eo
    return {"fy": {g: eo.scorecard(g, period_kind="fy") for g in ("archetype", "confidence", "market")},
            "q_track": {"archetype": eo.scorecard("archetype", period_kind="q_track")},
            "counts": eo.counts(), "min_n": eo.MIN_SCORECARD_N}


def _gates() -> dict:
    from src.memory import gate_outcomes as go
    return go.report()


def _overrides() -> dict:
    from src.memory import override_outcomes as oo
    rep = oo.report()
    rep["estimate_fields"] = {k: v for k, v in oo.estimate_field_outcomes().items() if k != "rows"}
    return rep


def _prior_misses() -> dict:
    """How many (market, profile) cells carry a gap today, and which."""
    from src.memory import agent_lessons as al
    from src.memory import run_features as rf
    cells = {}
    for r in rf.rows(where="profile IS NOT NULL"):
        cells.setdefault((r["market"], r["profile"]), 0)
        cells[(r["market"], r["profile"])] += 1
    gaps = []
    for (market, profile), n in sorted(cells.items()):
        gap = al.detect_profile_gap(market, profile)
        if gap:
            gaps.append({"market": market, "profile": profile, "n": gap["n"],
                         "median_miss_pct": gap["median_miss_pct"], "reason": gap["gap_reason"]})
    lessons = [l for l in al.list_lessons() if l.get("scope") == "profile"]
    return {"cells": len(cells), "gaps": gaps, "profile_lessons_active": len(lessons),
            "enabled": al.profile_lessons_enabled(), "prior_misses_enabled": al.prior_misses_enabled()}


def _ledger() -> dict:
    from src.memory import run_features as rf
    return {**rf.counts(), "enabled": rf.enabled()}


def _guidance() -> dict:
    from src.memory import estimate_outcomes as eo
    return eo.guidance_credibility()


def overview() -> dict:
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _block("ledger", _ledger, out)
    _block("estimates", _estimates, out)
    _block("guidance", _guidance, out)
    _block("scenarios", _scenarios, out)
    _block("gates", _gates, out)
    _block("overrides", _overrides, out)
    _block("prior_misses", _prior_misses, out)
    _block("families", _families, out)
    out["kill_switches"] = _switches()
    return out


def estimates(group_by: str = "archetype", period_kind: str = "fy") -> dict:
    from src.memory import estimate_outcomes as eo
    return eo.scorecard(group_by, period_kind=period_kind)


def guidance_credibility(ticker=None) -> dict:
    from src.memory import estimate_outcomes as eo
    return eo.guidance_credibility(ticker)
