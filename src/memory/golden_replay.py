"""Offline replay of one golden fixture (Phase 0, item 7).

Replay runs in a **fresh subprocess per ticker**. That is a deliberate cost:
importing ``dcf_agent`` is slow, and 14 tickers means 14 interpreter starts.
It buys the one property a golden baseline cannot do without — order
independence.

The valuation path has at least ten process-lifetime caches
(``src.data.cache._cache``, ``src.tools.fred._cache``,
``regional_comps._CLASSIFICATION_CACHE``, ``api._INDUSTRY_CACHE``,
``api._LISTING_CCY_CACHE``, ``holdco_sotp._CACHE``, ``sg.currency._cached_rate``,
``macro_regime._REGIME_CACHE``, ``calibration._cache``,
``sotp_ground_truth``'s ``lru_cache``). Enumerating and clearing them in a
shared pytest process was tried and produced a real false result: BN4.SI
replayed to 5.83 alone and 6.87 after another ticker ran first — a 15% swing
in the pinned baseline decided by test ordering. That list would also drift
every time someone added a cache, silently. Isolation by construction does not
have to be kept up to date.

The projection is FLATTENED to dotted scalar paths rather than stored as the
engine's nested dict, so a failure renders as a field-by-field table instead
of two blobs of JSON to diff by eye.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.memory import golden_capture as gc
from src.memory.golden_state import build_state, build_state_from_lookups

#: Top-level entry keys carried into the snapshot. ``gate_evaluations`` is
#: reduced to its metric names (below) rather than snapshotted whole — the
#: ledger grows with every Phase 1/2 item and snapshotting its payloads would
#: turn each landing into unrelated churn.
_SCALAR_KEYS = (
    "profile", "anchor_method", "data_source", "param_version",
    "wacc", "c_macro", "crp", "fx_rate", "leverage",
    "net_debt", "shares_outstanding", "shares_source",
    "revenue_base", "revenue_base_usd", "fcf_margin_base", "fcf_floor",
    "reported_currency", "source_currency", "fx_note",
    "12m_pt_method", "profile_fallback_used",
)

#: Nested blocks carried whole (then flattened). ``12m_targets`` and
#: ``consensus_pt`` are pinned because the 12m target engine is
#: forward-multiple-driven and decoupled from intrinsic value — it can move
#: without IV moving, and ``_convergence_bound`` exists precisely because it
#: once implied a full re-rating inside a year.
_DICT_KEYS = ("multiples_used", "routing_trace", "12m_targets", "consensus_pt")

#: Per-scenario fields. ``method_iv_table`` is the load-bearing one: it is
#: what shows WHICH valuation leg moved, and defects 1, 2 and 3 all change a
#: single leg rather than the blend.
_SCENARIO_KEYS = (
    "intrinsic_value", "intrinsic_value_pre_composite", "composite_applied",
    "growth_rate", "tgr", "fcf_margin_start", "tv_pct", "methods_count",
    "weight_dcf", "weight_multi", "growth_premium", "margin_delta_per_year",
    "iv_dcf", "iv_multi", "iv_multi_post",
    "yr1_revenue", "yr1_ebitda_est", "yr1_eps_est",
    "method_iv_table", "methods_used", "forward_flags", "effective_weights",
)

_SCENARIOS = ("bear", "base", "bull")


def _round(v: Any) -> Any:
    """Stabilise floats so a replay is not sensitive to last-bit noise."""
    if isinstance(v, float):
        return round(v, 6)
    return v


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Nested structure -> {dotted.path: scalar}. Sorted for stable diffs."""
    leaves: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            leaves.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, (list, tuple)):
        if all(x is None or isinstance(x, (str, int, float, bool)) for x in obj):
            # Homogeneous list: store as sorted strings so a reordering of
            # methods_used is not reported as a valuation change.
            leaves[prefix.rstrip(".")] = sorted(str(x) for x in obj)
        else:
            for i, v in enumerate(obj):
                leaves.update(_flatten(v, f"{prefix}{i}."))
    else:
        leaves[prefix.rstrip(".")] = _round(obj)
    return leaves


def project(entry: dict) -> dict[str, Any]:
    """Reduce a ``dcf_range[ticker]`` entry to its snapshot projection."""
    picked: dict[str, Any] = {}
    for k in _SCALAR_KEYS:
        if k in entry:
            picked[k] = entry[k]
    for k in _DICT_KEYS:
        if isinstance(entry.get(k), dict):
            picked[k] = entry[k]
    picked["methods_unavailable"] = sorted(
        str(x) for x in (entry.get("methods_unavailable") or []))

    scen: dict[str, Any] = {}
    for s in _SCENARIOS:
        block = entry.get(s)
        if not isinstance(block, dict):
            continue
        scen[s] = {k: block[k] for k in _SCENARIO_KEYS if k in block}
    picked["scenarios"] = scen

    # The forward-test ledger's SHAPE is worth pinning (which gates fired);
    # its payload is not.
    gates = entry.get("gate_evaluations")
    if isinstance(gates, list):
        picked["gate_metrics"] = sorted(
            str((g or {}).get("metric")) for g in gates if isinstance(g, dict))

    return dict(sorted(_flatten(picked).items()))


def replay_fixture(name: str) -> dict:
    """Replay one fixture directory offline. Never touches network or DB."""
    calls, meta = gc.read_fixture(name)
    ticker = meta["ticker"]
    web = gc.read_web_run(name)
    if web is not None:
        state = build_state(ticker, web["data"], meta["end_date"], api_key="dummy")
    else:
        state = build_state_from_lookups(ticker, meta["end_date"], api_key="dummy")

    from src.agents.analysis.dcf_agent import run_dcf_agent

    with gc.pinned_env(), gc.Replayer(calls) as rp:
        out = run_dcf_agent(state)

    entry = (out.get("data", {}).get("dcf_range") or {}).get(ticker)
    skip = (out.get("data", {}).get("dcf_skip_reasons") or {}).get(ticker)
    return {
        "ticker": ticker,
        "fixture": name,
        "ok": entry is not None,
        "skip_reason": skip,
        "projection": project(entry) if entry else {},
        "base_iv": (entry or {}).get("base", {}).get("intrinsic_value"),
        "diagnostics": {
            "recorded_calls": len(calls),
            "served": len(rp.served),
            "misses": rp.misses,
            "unused": rp.unused(),
        },
        "meta": {k: meta.get(k) for k in
                 ("commit", "captured_at", "end_date", "state_source",
                  "source_run_id")},
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m src.memory.golden_replay <fixture> <out.json>``"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__.splitlines()[0] + "\nusage: golden_replay <fixture> <out.json>",
              file=sys.stderr)
        return 2
    name, out_path = argv
    try:
        result = replay_fixture(name)
    except Exception as exc:                            # noqa: BLE001
        result = {"ticker": None, "fixture": name, "ok": False,
                  "error": f"{type(exc).__name__}: {exc}",
                  "projection": {}, "diagnostics": {}}
    Path(out_path).write_text(
        json.dumps(result, indent=1, default=str, sort_keys=False),
        encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
