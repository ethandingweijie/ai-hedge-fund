"""Per-phase timing summary over recent runs (A0).

Reads the phase_durations list every web run already persists and reports
p50/p90 per phase, split by the research path the run took.

No run records that path explicitly, so it is inferred from the research
phase's own duration. The two paths differ by minutes -- a covered run
reuses archived research in seconds, a first-ever ticker spends ~10 minutes
searching -- so one threshold separates them cleanly. A run with no research
phase at all (a crash, a legacy row) is reported as "unknown", not guessed.

Wall time comes from the phases' own timestamps, not from summing
durations: the front block runs five legs in parallel, and a sum would
count that window five times.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional

RESEARCH_PHASE = "3_deep_research_router"
FULL_RESEARCH_THRESHOLD_S = 240.0


def _percentile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def parse_entries(raw) -> list[dict]:
    """phase_durations as stored: a list, a JSON string, or nothing usable."""
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            return []
        return parse_entries(value) if isinstance(value, list) else []
    return []


def research_path(entries: list[dict]) -> str:
    for e in entries:
        if e.get("phase") == RESEARCH_PHASE:
            try:
                dur = float(e.get("duration_s"))
            except (TypeError, ValueError):
                return "unknown"
            return "full" if dur >= FULL_RESEARCH_THRESHOLD_S else "covered"
    return "unknown"


def wall_seconds(entries: list[dict]) -> Optional[float]:
    starts, ends = [], []
    for e in entries:
        try:
            s = datetime.fromisoformat(e["started_at"])
            f = datetime.fromisoformat(e["finished_at"])
        except (KeyError, TypeError, ValueError):
            continue
        starts.append(s)
        ends.append(f)
    if not starts:
        return None
    return (max(ends) - min(starts)).total_seconds()


def summarize(runs: Iterable) -> dict:
    """{n_runs, research_threshold_s, <path>: {n_runs, wall_p50, wall_p90,
    phases: {phase: {n, p50, p90}}}}, phases ordered slowest p50 first."""
    groups: dict[str, dict] = {}
    n_runs = 0
    for raw in runs:
        entries = parse_entries(raw)
        if not entries:
            continue
        n_runs += 1
        g = groups.setdefault(research_path(entries),
                              {"n": 0, "phases": {}, "wall": []})
        g["n"] += 1
        for e in entries:
            try:
                dur = float(e["duration_s"])
            except (KeyError, TypeError, ValueError):
                continue
            g["phases"].setdefault(str(e.get("phase") or "?"), []).append(dur)
        wall = wall_seconds(entries)
        if wall is not None:
            g["wall"].append(wall)

    out: dict = {"n_runs": n_runs,
                 "research_threshold_s": FULL_RESEARCH_THRESHOLD_S}
    for path, g in groups.items():
        phases = {
            name: {"n": len(vals), "p50": _percentile(vals, 0.5),
                   "p90": _percentile(vals, 0.9)}
            for name, vals in g["phases"].items()
        }
        out[path] = {
            "n_runs": g["n"],
            "wall_p50": _percentile(g["wall"], 0.5),
            "wall_p90": _percentile(g["wall"], 0.9),
            "phases": dict(sorted(phases.items(),
                                  key=lambda kv: -(kv[1]["p50"] or 0.0))),
        }
    return out
