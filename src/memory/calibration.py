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


def active_version() -> Optional[dict]:
    """{"version_id": str, "params": dict} for the active calibration, or None."""
    if _disabled():
        return None
    with _lock:
        hit = _cache.get("active")
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    value = None
    try:
        row = _db.query_one(
            "SELECT version_id, params_json FROM calibration_versions "
            "WHERE status = 'active' ORDER BY promoted_at DESC LIMIT 1")
        if row:
            params = json.loads(row["params_json"])
            if isinstance(params, dict):
                value = {"version_id": row["version_id"], "params": params}
    except Exception:                                      # noqa: BLE001
        value = None       # no table yet, or unreadable: constants stand
    with _lock:
        _cache["active"] = (time.monotonic(), value)
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
