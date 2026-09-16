"""Golden end-to-end valuation suite (Phase 0, item 7).

The engine's outputs were never pinned, so eight production defects shipped
with the suite green — the reinvestment charge moved DCF values between -84%
and -309% and nothing failed. These tests close that hole: each recorded
fixture is replayed offline in a fresh subprocess and its flattened output
projection is compared against ``tests/golden/snapshots.json``.

What this suite does and does not prove:

  * It proves a code change moved a valuation, and shows which leg moved.
    That is the whole point — a silent 20% move is no longer silent.
  * It does NOT prove the pinned number is *correct*. The baseline is
    whatever the engine produced at ``30b2670``. Correctness is the job of
    the backward test (``scripts/backtest_valuation_fixes.py``) and the
    forward ledger (``src/memory/gate_forward.py``).

Coverage limits are recorded in the fixtures rather than hidden: five basket
tickers have no production ``web_runs`` row and are recorded from the curated
routing tables, so they carry no LLM-derived inputs (``state_source`` in
``meta.json`` distinguishes the two). ``management_guidance`` and
``sotp_assumptions`` are never archived by the pipeline, so the guided-growth
and SOTP-led paths are uncovered for every ticker. See
``src/memory/golden_state.py``.

Regenerating the baseline::

    GOLDEN_UPDATE_REASON="Phase 1.1: convergence fade on cyclicals" \
      pytest tests/test_golden_valuations.py --update-snapshots
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory import golden_capture as gc          # noqa: E402
from src.memory import golden_snapshots as gs         # noqa: E402

#: Per-child wall clock. A cold interpreter importing dcf_agent plus one
#: replay is seconds; this is a hang guard, not a performance budget.
_CHILD_TIMEOUT = 420

FIXTURES = gc.available_tickers()


def _child_env() -> dict:
    """Environment for the replay subprocess.

    Credentials are stripped here as well as inside ``pinned_env`` so that a
    child can never authenticate to a live service even if the harness itself
    regressed. The golden suite is offline by construction.
    """
    env = {k: v for k, v in os.environ.items() if k not in gc.SECRET_ENV_VARS}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _replay(name: str, out_dir: Path) -> dict:
    """Run one fixture's replay in a fresh interpreter and return its result."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.json"
    proc = subprocess.run(
        [sys.executable, "-m", "src.memory.golden_replay", name, str(out)],
        cwd=str(ROOT), env=_child_env(), capture_output=True, text=True,
        timeout=_CHILD_TIMEOUT, encoding="utf-8", errors="replace",
    )
    if not out.exists():
        raise AssertionError(
            f"golden replay child produced no output for {name}\n"
            f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout[-3000:]}\n"
            f"--- stderr ---\n{proc.stderr[-3000:]}"
        )
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def replay_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("golden_replays")


@pytest.fixture(scope="session")
def replays(replay_dir) -> dict:
    """Every fixture replayed once per session, keyed by fixture name."""
    return {name: _replay(name, replay_dir) for name in FIXTURES}


@pytest.fixture(scope="session")
def snapshot(replays, golden_update_reason, replay_dir) -> dict:
    """The baseline document — regenerated first when updating."""
    if golden_update_reason:
        failed = {n: r for n, r in replays.items() if not r.get("ok")}
        if failed:
            raise AssertionError(
                "refusing to rewrite the golden baseline from a failed replay: "
                + ", ".join(sorted(failed))
            )
        commit = next(
            (r.get("meta", {}).get("commit") for r in replays.values()
             if r.get("meta", {}).get("commit")), "unknown")
        doc = gs.build_doc(replays, reason=golden_update_reason, commit=commit)
        gs.save(doc, reason=golden_update_reason)
        return doc
    if not gs.exists():
        raise AssertionError(
            f"{gs.SNAPSHOT_PATH} does not exist. Generate it once with:\n"
            f"  GOLDEN_UPDATE_REASON=\"initial baseline at <commit>\" "
            f"pytest tests/test_golden_valuations.py --update-snapshots"
        )
    return gs.load()


# ── Suite-level guards ──────────────────────────────────────────────────────

def test_fixtures_exist():
    assert FIXTURES, (
        f"no golden fixtures under {gc.GOLDEN_ROOT}. Record them with:\n"
        f"  python scripts/record_golden_fixtures.py --all"
    )


def test_snapshot_covers_every_fixture(snapshot):
    """No unrecorded fixture, and no snapshot for a fixture that vanished."""
    pinned = set(gs.tickers_in(snapshot))
    recorded = set(FIXTURES)
    assert recorded - pinned == set(), (
        f"fixtures with no pinned baseline: {sorted(recorded - pinned)}\n"
        f"Run --update-snapshots with a GOLDEN_UPDATE_REASON."
    )
    assert pinned - recorded == set(), (
        f"snapshots with no fixture (stale, delete them): "
        f"{sorted(pinned - recorded)}"
    )


def test_replay_is_deterministic(replays, replay_dir):
    """A second replay of the same fixture must be byte-identical.

    Guards the reason replay runs in a subprocess at all: the valuation path
    has ~ten process-lifetime caches, and a leak between tickers once moved
    BN4.SI's baseline 15% depending on test order. If someone later collapses
    the children into one process, this fails.

    Compared at zero tolerance, not the suite's ±5% — a golden baseline that
    is only stable to within 5% is not a baseline, and any move here is a
    non-deterministic input (a wall-clock staleness check, a mutable file in
    the working tree) that has to be found and frozen.
    """
    name = FIXTURES[0]
    again = _replay(name, replay_dir / "again")
    first = replays[name]
    # A crashed child returns an empty projection, which would otherwise
    # surface as "every field changed" and send the investigation in exactly
    # the wrong direction.
    assert again.get("ok"), (
        f"{name}: the determinism re-run child failed — "
        f"{again.get('error') or again.get('skip_reason')}"
    )
    rows = gs.diff_fields(first["projection"], again["projection"], tol=0.0)
    assert not rows, (
        f"{name} replayed to two different projections in one session.\n"
        f"  first base_iv={first.get('base_iv')}  again base_iv={again.get('base_iv')}\n"
        f"  first diagnostics: {first.get('diagnostics')}\n"
        f"  again diagnostics: {again.get('diagnostics')}\n"
        f"{len(rows)} field(s) differ:\n{gs.format_diff(rows, 40)}\n"
        f"A process-lifetime cache is leaking between runs, or an input is "
        f"not frozen (wall-clock staleness, a mutable file in the tree)."
    )


# ── Per-ticker comparison ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", FIXTURES)
def test_golden_valuation(name, replays, snapshot):
    result = replays[name]

    diag = result.get("diagnostics") or {}
    assert result.get("ok"), (
        f"{name}: replay failed — {result.get('error') or result.get('skip_reason')}"
    )
    # An unrecorded call means the engine asked for an input the fixture does
    # not carry; an unused one means the fixture carries an input the engine no
    # longer asks for. Both mean the recording has drifted from the code.
    assert not diag.get("misses"), (
        f"{name}: {len(diag['misses'])} unrecorded call(s) during replay — "
        f"re-record the fixture.\n  " + "\n  ".join(diag["misses"][:5])
    )
    assert not diag.get("unused"), (
        f"{name}: {len(diag['unused'])} recorded call(s) never used — the "
        f"fixture is stale.\n  " + "\n  ".join(diag["unused"][:5])
    )

    expected = (snapshot.get(name) or {}).get("projection") or {}
    actual = result.get("projection") or {}
    rows = gs.diff_fields(expected, actual)
    if not rows:
        return

    meta = result.get("meta") or {}
    snap_meta = snapshot.get("_meta") or {}
    pytest.fail(
        f"\n\n{name} ({result.get('ticker')}) moved off the golden baseline.\n"
        f"\n"
        f"  baseline  pinned {snap_meta.get('generated_at', '?')} at "
        f"{str(snap_meta.get('commit', '?'))[:10]}\n"
        f"            reason: {snap_meta.get('reason', '?')}\n"
        f"  fixture   recorded {meta.get('captured_at', '?')} at "
        f"{str(meta.get('commit', '?'))[:10]} "
        f"(state: {meta.get('state_source', '?')})\n"
        f"  running   at {_head_commit()}\n"
        f"  tolerance ±{gs.TOLERANCE:.0%} on numeric leaves\n"
        f"\n"
        f"  base intrinsic value: {snapshot.get(name, {}).get('base_iv')} "
        f"-> {result.get('base_iv')}\n"
        f"\n"
        f"{len(rows)} field(s) differ:\n"
        f"{gs.format_diff(rows)}\n"
        f"\n"
        f"If this move is intended, rewrite the baseline with a reason:\n"
        f"  GOLDEN_UPDATE_REASON=\"<what changed and why>\" "
        f"pytest tests/test_golden_valuations.py --update-snapshots\n",
        pytrace=False,
    )


def _head_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            text=True).strip()
    except Exception:                                    # noqa: BLE001
        return "unknown"
