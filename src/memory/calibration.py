"""The active valuation calibration -- the one loader every engine hook reads (B4).

Nothing is active until a proposal is promoted (B6). Until then, and whenever
the table is absent, unreadable or VALUATION_CALIBRATION_DISABLED is set, the
loader returns None and every hook hands its input back untouched, so today's
constants hold bit-for-bit.

A calibration carries two kinds of parameter, both fitted from the outcome
ledger and both re-blendable from what a run stores:

  profile_weights       {"<sector>|<profile>": {method: weight}}
  market_iv_multiplier  {"<market>": k}   (bias correction on the blended IV)

The loader caches for five minutes per process, so promotion reaches every
worker without a deploy and without a DB read per ticker.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Optional

from src.data import db as _db

_CACHE_TTL_S = 300.0
_cache: dict = {}
_lock = threading.Lock()
_UNSET = object()


def _disabled() -> bool:
    return os.environ.get("VALUATION_CALIBRATION_DISABLED", "false").strip().lower() in (
        "1", "true", "yes", "on")


#: Calibration families, one active version each (self-learning layer, 2026-10-03):
#:   iv   profile_weights + market_iv_multiplier            (B4, re-blended from stored legs)
#:   pt   capture + scenario_prob_shrink                    (loops 3 and 4, price horizons only)
#:   est  guidance_growth_adj + archetype_growth_adj        (loop 1, scored on reported prints)
FAMILIES = ("iv", "pt", "est")


def active_version(family: str = "iv") -> Optional[dict]:
    """{"version_id": str, "params": dict, "family": str} for the active calibration of
    this family, or None. A table without the family column (pre-2026-10 rows) holds iv
    versions only."""
    if _disabled() or family not in FAMILIES:
        return None
    key = f"active:{family}"
    with _lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    value = None
    try:
        try:
            row = _db.query_one(
                "SELECT version_id, params_json FROM calibration_versions "
                "WHERE status = 'active' AND family = ? ORDER BY promoted_at DESC LIMIT 1",
                [family])
        except Exception:                                  # noqa: BLE001
            row = None
            if family == "iv":                             # legacy table: no family column yet
                row = _db.query_one(
                    "SELECT version_id, params_json FROM calibration_versions "
                    "WHERE status = 'active' ORDER BY promoted_at DESC LIMIT 1")
        if row:
            params = json.loads(row["params_json"])
            if isinstance(params, dict):
                value = {"version_id": row["version_id"], "params": params, "family": family}
    except Exception:                                      # noqa: BLE001
        value = None       # no table yet, or unreadable: constants stand
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def cell_key(sector: str, profile: str) -> str:
    return f"{sector}|{profile}"


def apply_profile_weights(profile_data, sector: str, profile_name: str,
                          active=_UNSET):
    """Copy of profile_data with the active calibration's weights for this
    (sector, profile); the SAME object when nothing applies. Never mutates --
    profile_data is a live reference into INDUSTRY_VALUATION_PROFILES."""
    active = active_version() if active is _UNSET else active
    if not active or not isinstance(profile_data, dict):
        return profile_data
    override = (active["params"].get("profile_weights") or {}).get(
        cell_key(sector, profile_name))
    if not override:
        return profile_data
    methods = profile_data.get("methods") or []
    if not any(isinstance(m, dict) and m.get("name") in override for m in methods):
        return profile_data
    return {**profile_data, "methods": [
        {**m, "weight": float(override[m["name"]])}
        if isinstance(m, dict) and m.get("name") in override else m
        for m in methods]}


def _adj(raw) -> Optional[float]:
    """A finite growth adjustment (a fraction, e.g. -0.02 = two points lower), or None."""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f != 0.0 and abs(f) <= 0.25 else None


def guidance_adj(ticker: str, sector: Optional[str], active=_UNSET) -> Optional[float]:
    """The est family's adjustment to management's guided revenue growth before the
    guidance channel uses it: the sector scope first, then the ticker's market. None when
    nothing is active -- the channel then runs exactly as before."""
    active = active_version("est") if active is _UNSET else active
    if not active:
        return None
    from src.memory.valuation_outcomes import market_of
    table = active["params"].get("guidance_growth_adj") or {}
    for scope in (f"sector:{sector}" if sector else None, f"market:{market_of(ticker)}"):
        if scope and scope in table:
            v = _adj(table[scope])
            if v is not None:
                return v
    return None


def archetype_adj_map(active=_UNSET) -> dict:
    """{archetype_code: adjustment} from the active est calibration, with "_version_id";
    {} when nothing is active. Read by guidance_forecast.load_cfg()."""
    active = active_version("est") if active is _UNSET else active
    if not active:
        return {}
    out = {}
    for code, raw in (active["params"].get("archetype_growth_adj") or {}).items():
        v = _adj(raw)
        if v is not None:
            out[str(code)] = v
    if out:
        out["_version_id"] = active["version_id"]
    return out


def iv_multiplier(ticker: str, active=_UNSET) -> Optional[float]:
    """The active calibration's IV multiplier for this ticker's market, or None."""
    active = active_version() if active is _UNSET else active
    if not active:
        return None
    from src.memory.valuation_outcomes import market_of
    raw = (active["params"].get("market_iv_multiplier") or {}).get(market_of(ticker))
    try:
        k = float(raw)
    except (TypeError, ValueError):
        return None
    return k if k > 0 and math.isfinite(k) and k != 1.0 else None
