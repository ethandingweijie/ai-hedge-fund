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

The wall clock is FROZEN to the fixture's ``captured_at``, because one
projection field (``comp_age_days``) is computed against ``datetime.now()`` and
would otherwise drift out of tolerance within hours of any regeneration. See
``freeze_clock``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date as _RealDate, datetime as _RealDatetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.memory import golden_capture as gc
from src.memory.golden_state import build_state, build_state_from_lookups

# ── Frozen clock ────────────────────────────────────────────────────────────
#
# Replay pins every input EXCEPT the one it cannot see: the wall clock.
# ``regional_comps._age_days`` subtracts a stored ``computed_at`` from
# ``datetime.now()``, so ``multiples_used.comp_age_days`` in the projection
# advanced by 1.0 for every day of real time that passed. The baseline written
# at 14:01 UTC carried 3.23 days and breached its own ±5% tolerance (3.39) by
# roughly 18:00 the SAME DAY, with no code change at all. Six fixtures carry
# the field; four of them were names Phase 1.1 never touched, so the failure
# would have pointed at the wrong change entirely.
#
# A golden suite that fails on the calendar teaches people to ignore golden
# failures, which is the one habit item 7 exists to break. So the clock is
# frozen to the instant the fixture was recorded: replaying at ``captured_at``
# reproduces the ages the engine actually saw when the inputs were captured,
# which is the faithful answer, not merely the stable one.
#
# The cosmetic field was the small half of the problem. The same age gates
# ``load_comps`` against ``MAX_AGE_DAYS``, so a fixture left to age far enough
# stops returning comps at all: shifting the frozen clock a hundred days moves
# bull intrinsic value, the P/B and EV/EBITDA cohorts, every peer count and the
# 12-month targets, while ``base_iv`` stays put and the headline number looks
# untouched. Without the freeze these fourteen fixtures would have degraded
# silently within weeks and been re-baselined as though a code change had done
# it. With it, a recorded fixture replays identically forever.
#
# ``time.time()`` is deliberately NOT frozen. Nothing in the projection reads
# it, and freezing it would make every TTL cache in the process permanently
# fresh or permanently expired — a larger behavioural change than the drift it
# would prevent. If a future projection field is found to read it, freeze it
# here too rather than dropping the field.

#: Overrides the fixture's ``captured_at``. Used by the tests to prove the
#: projection no longer depends on when replay happens to run.
_FROZEN_NOW_ENV = "GOLDEN_FROZEN_NOW"

_FROZEN_INSTANT: Optional[_RealDatetime] = None


class _FrozenDatetime(_RealDatetime):
    """``datetime`` whose ``now()`` never advances.

    Naive ``now()`` (and ``today()``) resolve to UTC rather than to the
    machine's local zone. Real ``datetime.now()`` is local, so this is a
    deliberate divergence: a baseline that depends on the timezone of whoever
    regenerated it is not a baseline. Nothing in the projection reads the naive
    path today — ``_age_days`` asks for ``now(timezone.utc)`` — so the choice
    costs nothing and buys machine-independence.
    """

    @classmethod
    def _naive_utc(cls) -> _RealDatetime:
        return cls._instant().astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _instant(cls) -> _RealDatetime:
        return _FROZEN_INSTANT or _RealDatetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):                        # noqa: D102
        if _FROZEN_INSTANT is None:               # not installed; be honest
            return _RealDatetime.now(tz)
        return cls._naive_utc() if tz is None else _FROZEN_INSTANT.astimezone(tz)

    @classmethod
    def utcnow(cls):                              # noqa: D102
        if _FROZEN_INSTANT is None:
            return _RealDatetime.utcnow()
        return cls._naive_utc()

    @classmethod
    def today(cls):                               # noqa: D102
        return cls.now().date()


class _FrozenDate(_RealDate):
    """``date`` whose ``today()`` never advances."""

    @classmethod
    def today(cls):                               # noqa: D102
        if _FROZEN_INSTANT is None:
            return _RealDate.today()
        return _FROZEN_INSTANT.astimezone(timezone.utc).date()


def parse_instant(value: Any) -> Optional[_RealDatetime]:
    """ISO-8601 -> aware UTC datetime. None when absent or unparseable.

    A fixture with no usable ``captured_at`` must not crash replay; it falls
    back to the real clock, which is the pre-fix behaviour and still correct,
    merely not stable.
    """
    if not value:
        return None
    try:
        ts = _RealDatetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _is_first_party(name: str) -> bool:
    return name.startswith("src.") or name.startswith("app.")


def _rebind_module(mod) -> None:
    """Point one module's ``datetime``/``date`` at the frozen classes."""
    bound = getattr(mod, "datetime", None)
    if bound is _RealDatetime or bound is _FrozenDatetime:
        mod.datetime = _FrozenDatetime if _FROZEN_INSTANT else _RealDatetime
    bound = getattr(mod, "date", None)
    if bound is _RealDate or bound is _FrozenDate:
        mod.date = _FrozenDate if _FROZEN_INSTANT else _RealDate


def _rebind_all() -> None:
    for name, mod in list(sys.modules.items()):
        if mod is not None and _is_first_party(name):
            _rebind_module(mod)


class _FreezingLoader:
    """Wraps a loader so a module is rebound the instant it finishes loading."""

    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        fn = getattr(self._inner, "create_module", None)
        return fn(spec) if fn is not None else None

    def exec_module(self, module):
        self._inner.exec_module(module)
        _rebind_module(module)

    def __getattr__(self, name):
        # get_code, is_package, get_filename, ... — importlib probes several
        # optional loader attributes and must see the real loader's answers.
        return getattr(self._inner, name)


class _FreezeFinder:
    """Catches first-party modules imported AFTER the clock was frozen.

    This is not a theoretical hole. ``sector_profiles`` imports
    ``regional_comps`` from inside three different functions, so at the moment
    the engine finishes importing — the natural place to rebind — the module
    that computes ``comp_age_days`` is not in ``sys.modules`` yet. It loads
    later, binds the real ``datetime`` from the untouched stdlib module, and
    the freeze silently reaches nothing. Verified, not conjectured: shifting the
    frozen clock by a hundred days left ``comp_age_days`` at 3.31.

    Enumerating the lazy modules would work today and drift the first time
    someone adds one, which is the same argument this module already makes for
    subprocess isolation over a hand-maintained cache list. So the freeze
    installs itself on the import path instead.
    """

    def find_spec(self, fullname, path=None, target=None):
        if _FROZEN_INSTANT is None or not _is_first_party(fullname):
            return None
        if fullname in sys.modules:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue
            spec = find(fullname, path, target)
            if spec is None:
                continue
            if spec.loader is not None and not isinstance(spec.loader, _FreezingLoader):
                spec.loader = _FreezingLoader(spec.loader)
            return spec
        return None


_FINDER: Optional[_FreezeFinder] = None


def _install_finder() -> None:
    global _FINDER
    if _FINDER is None:
        _FINDER = _FreezeFinder()
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)


def _remove_finder() -> None:
    global _FINDER
    if _FINDER is not None and _FINDER in sys.meta_path:
        sys.meta_path.remove(_FINDER)


def freeze_clock(value: Any) -> Optional[_RealDatetime]:
    """Pin the wall clock for the replay. Returns the instant actually used.

    Only ``src.*`` and ``app.*`` modules are rebound. The stdlib ``datetime``
    module itself is deliberately left ALONE, and that is not a stylistic
    preference: replacing ``datetime.datetime`` with a Python subclass makes
    every C extension compiled against the real type see a different object
    layout. In the replay child that produced

        RuntimeWarning: datetime.datetime size changed, may indicate binary
        incompatibility. Expected 48 from C header, got 56 from PyObject

    followed by a hard crash (0xC0000409) with no output file — pandas, numpy
    and the psycopg adapters all cache the type they were built against.
    ``tests/test_golden_clock.py`` asserts the stdlib classes are never
    replaced, so this cannot be "fixed" by someone reaching for the obvious
    freezegun approach without a test telling them why it explodes.

    Two mechanisms cover both import orders: modules already loaded are
    rebound in place, and ``_FreezeFinder`` rebinds each first-party module as
    it finishes loading for as long as the clock stays frozen.

    Process-global and never undone in production — replay is subprocess-
    isolated per ticker precisely so installation cannot leak between fixtures.
    ``unfreeze_clock`` exists for tests that freeze in-process.
    """
    global _FROZEN_INSTANT
    instant = parse_instant(value)
    _FROZEN_INSTANT = instant
    if instant is None:
        _remove_finder()
        return None
    _install_finder()
    _rebind_all()
    return instant


def unfreeze_clock() -> None:
    """Undo ``freeze_clock`` — restores the real classes in every module.

    Production replay never calls this: the child process exits. It exists so a
    test can freeze the clock, assert on it, and hand the interpreter back to
    pytest without quietly pinning every later test in the session to one
    instant, or leaving an import hook on ``sys.meta_path``. That would be a
    nastier version of the bug this module fixes.
    """
    global _FROZEN_INSTANT
    _FROZEN_INSTANT = None
    _remove_finder()
    _rebind_all()


def frozen_now() -> Optional[_RealDatetime]:
    """The instant replay is pinned to, or None when the clock is live."""
    return _FROZEN_INSTANT

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
    # Item 3b audit field. Scenario-invariant, so it lives at the top level
    # rather than being triplicated into each scenario.
    "normalized_net_income",
)

#: Nested blocks carried whole (then flattened). ``12m_targets`` and
#: ``consensus_pt`` are pinned because the 12m target engine is
#: forward-multiple-driven and decoupled from intrinsic value — it can move
#: without IV moving, and ``_convergence_bound`` exists precisely because it
#: once implied a full re-rating inside a year.
#:
#: ``composite_bridge`` is item 3b: the Q/R/C decomposition and ``bank_clamp``
#: behind ``composite_applied``. Pinning it means a change to the composite's
#: sub-scores moves the baseline even in the cases where the resulting multiple
#: is unchanged by rounding — and Decision 1 narrowed ``bank_clamp`` to a real
#: bank test, a change the baseline could not previously see at all.
_DICT_KEYS = ("multiples_used", "routing_trace", "12m_targets", "consensus_pt",
              "composite_bridge")

#: Per-scenario fields. ``method_iv_table`` is the load-bearing one: it is
#: what shows WHICH valuation leg moved, and defects 1, 2 and 3 all change a
#: single leg rather than the blend.
_SCENARIO_KEYS = (
    "intrinsic_value", "intrinsic_value_pre_composite", "composite_applied",
    "growth_rate", "tgr", "fcf_margin_start", "tv_pct", "methods_count",
    "weight_dcf", "weight_multi", "growth_premium",
    # Both spellings of the same one-shot absolute margin delta are pinned:
    # `margin_delta_absolute` is authoritative, `margin_delta_per_year` is the
    # deprecated read-compatibility alias. Carrying both means a replay that
    # ever lets them diverge fails the baseline instead of shipping it.
    "margin_delta_absolute", "margin_delta_per_year",
    "iv_dcf", "iv_multi", "iv_multi_post",
    "yr1_revenue", "yr1_ebitda_est", "yr1_eps_est",
    "method_iv_table", "methods_used", "forward_flags", "effective_weights",
    # Item 3b audit fields. These drove Gate B and the growth-premium quality
    # gate while being recoverable only by regex-ing them out of
    # `forward_flags` prose — which is literally how the `md_abs * 10` defect
    # was measured. `sector_g_avg_basis` carries the COHORT the average was
    # read from, so the `_peer_for_gp` market_cap divergence (it passes none,
    # the three legs pass a real one) is checkable from the baseline instead of
    # inferable from source.
    "forward_roic", "roic_source", "sector_g_avg", "sector_g_avg_basis",
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


def replay_fixture(name: str, *, frozen_now: Any = None) -> dict:
    """Replay one fixture directory offline. Never touches network or DB.

    The clock is pinned to ``frozen_now``, else ``$GOLDEN_FROZEN_NOW``, else
    the fixture's own ``captured_at`` — the instant the inputs were recorded,
    so the ages the engine computes are the ages it saw then.
    """
    calls, meta = gc.read_fixture(name)
    instant = freeze_clock(
        frozen_now or os.environ.get(_FROZEN_NOW_ENV) or meta.get("captured_at"))
    ticker = meta["ticker"]
    web = gc.read_web_run(name)
    if web is not None:
        state = build_state(ticker, web["data"], meta["end_date"], api_key="dummy")
    else:
        state = build_state_from_lookups(ticker, meta["end_date"], api_key="dummy")

    from src.agents.analysis.dcf_agent import run_dcf_agent

    if instant is not None:
        # Belt and braces. ``_FreezeFinder`` rebinds each first-party module as
        # it loads, so by here the whole valuation graph is covered; this sweep
        # also catches anything that reached sys.modules by a route the finder
        # does not see (a reload, a second name for one module).
        _rebind_all()

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
        "meta": {**{k: meta.get(k) for k in
                   ("commit", "captured_at", "end_date", "state_source",
                    "source_run_id")},
                 # Recorded so a future drift can be told apart from a bad
                 # freeze: if this is null, replay ran on the live clock and
                 # any age-shaped field in the projection is suspect.
                 "frozen_now": instant.isoformat() if instant else None},
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
