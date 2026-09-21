"""The golden replay clock is frozen (Phase 0 remediation).

The golden baseline is supposed to move only when the engine's answer moves.
It did not behave that way: ``regional_comps._age_days`` computes
``datetime.now(timezone.utc) - computed_at``, so ``multiples_used.comp_age_days``
advanced by 1.0 for every day of real time. The baseline written at 14:01 UTC
carried 3.23 days and breached its own ±5% tolerance (3.39) by roughly 18:00
the same day — four tickers failing with no code change, none of them a name
the change under test had touched.

That failure mode is worse than no golden suite. A baseline that fails on the
calendar trains the next person to re-run with ``--update-snapshots`` and move
on, which is exactly the habit item 7 was built to break.

These tests pin the fix: replay runs at the instant the fixture was recorded,
the one clock-derived field is a pure function of that instant, and nothing
else in the projection knows the clock exists.

The last group also pins the mechanism's hard constraint. The obvious
implementation — replace ``datetime.datetime`` on the stdlib module, the way
freezegun does — hard-crashes the replay child on Windows (0xC0000409) after
warning that ``datetime.datetime size changed``: pandas, numpy and the psycopg
adapters are C extensions compiled against the real type layout. So the freeze
rebinds first-party modules only, and ``test_the_stdlib_datetime_module_is_never_patched``
exists to make that non-obvious constraint survive a future refactor.
"""
from __future__ import annotations

import datetime as dt_module
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import regional_comps as rc                 # noqa: E402
from src.memory import golden_capture as gc               # noqa: E402
from src.memory import golden_replay as gr                # noqa: E402
from src.data.regional_comps import _age_days             # noqa: E402

#: A fixture whose projection carries ``comp_age_days`` — one of the four that
#: drifted. The end-to-end test replays it in a subprocess twice.
_CLOCKED_FIXTURE = "C38U_SI"
_CLOCKED_FIELD = "multiples_used.comp_age_days"

_INSTANT = datetime(2026, 9, 16, 13, 22, 12, tzinfo=timezone.utc)

#: ``regional_comps`` is the module that drifted, and importing it here means
#: it is already in ``sys.modules`` — the realistic case for the rebind, since
#: a first-party module that ran ``from datetime import datetime`` before the
#: freeze holds a direct reference no stdlib patch could ever have reached.


@pytest.fixture(autouse=True)
def _restore_real_clock():
    """Never hand pytest a frozen interpreter.

    ``freeze_clock`` is process-global by design (replay is subprocess-isolated,
    so it can afford to be). Without this, one test in this module would pin the
    clock for every later test in the session — a quieter version of the bug
    under test, and one that would surface as mysterious staleness failures
    hundreds of tests away from its cause.
    """
    yield
    gr.unfreeze_clock()


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_parse_instant_reads_an_offset_timestamp():
    assert gr.parse_instant("2026-09-16T13:22:12+00:00") == _INSTANT


def test_parse_instant_normalises_a_non_utc_offset_to_utc():
    """A fixture captured in SGT and one in UTC must pin the same instant."""
    assert gr.parse_instant("2026-09-16T21:22:12+08:00") == _INSTANT


def test_parse_instant_treats_a_naive_timestamp_as_utc():
    """Matches ``_age_days``, which assumes UTC for a tz-less ``computed_at``."""
    assert gr.parse_instant("2026-09-16T13:22:12") == _INSTANT


@pytest.mark.parametrize("value", [None, "", "   ", "not a timestamp", "2026-99-99"])
def test_parse_instant_returns_none_rather_than_raising(value):
    """A fixture with no usable timestamp must not crash the replay child."""
    assert gr.parse_instant(value) is None


# ── The mechanism's hard constraint ─────────────────────────────────────────

def test_the_stdlib_datetime_module_is_never_patched():
    """Replacing the stdlib classes hard-crashes the replay child.

    Not a preference. ``datetime`` is a C extension; every C extension built
    against it caches the type layout, and swapping in a Python subclass makes
    pandas/numpy/psycopg see the wrong size. Observed in the replay child as
    ``RuntimeWarning: datetime.datetime size changed, may indicate binary
    incompatibility. Expected 48 from C header, got 56 from PyObject`` followed
    by exit 0xC0000409 and no output file.
    """
    gr.freeze_clock(_INSTANT)
    assert dt_module.datetime is datetime
    assert dt_module.date is date


def test_frozen_values_are_still_real_datetimes():
    """A subclass, so isinstance holds and C adapters accept the value."""
    gr.freeze_clock(_INSTANT)
    now = rc.datetime.now(timezone.utc)
    assert isinstance(now, datetime)
    assert isinstance(gr._FrozenDate.today(), date)


def test_unfreeze_is_a_no_op_on_the_stdlib_module():
    gr.freeze_clock(_INSTANT)
    gr.unfreeze_clock()
    assert dt_module.datetime is datetime
    assert dt_module.date is date


# ── Freezing, seen through a first-party module ─────────────────────────────

def test_a_first_party_module_binds_the_real_class_until_frozen():
    """Precondition for everything below: there is something to rebind.

    ``regional_comps`` ran ``from datetime import datetime`` at import, so it
    holds a direct reference to the real class that no patch of the stdlib
    module could have reached — the reason the freeze rebinds modules at all.
    """
    gr.unfreeze_clock()
    assert rc.datetime is datetime


def test_freeze_clock_reports_the_instant_it_pinned():
    assert gr.freeze_clock(_INSTANT.isoformat()) == _INSTANT
    assert gr.frozen_now() == _INSTANT


def test_freeze_rebinds_a_module_that_already_imported_datetime():
    gr.freeze_clock(_INSTANT)
    assert rc.datetime is gr._FrozenDatetime


def test_freeze_installs_an_import_hook_and_unfreeze_removes_it():
    gr.freeze_clock(_INSTANT)
    assert gr._FINDER is not None and gr._FINDER in sys.meta_path
    gr.unfreeze_clock()
    assert gr._FINDER not in sys.meta_path


def test_the_hook_wraps_the_loader_for_a_first_party_module():
    """A module imported AFTER the freeze must still land on the frozen clock.

    ``find_spec`` resolves without executing anything, so this checks the
    mechanism with no side effects. It is the half of the fix that is easy to
    delete by accident: without it, ``regional_comps`` — which
    ``sector_profiles`` imports lazily from inside three functions, so it is
    NOT loaded when the engine finishes importing — binds the real class and
    the freeze reaches nothing. Shifting the clock by a hundred days left
    ``comp_age_days`` at 3.31 when that half was missing.
    """
    import importlib.util
    name = "src.data.regional_comps"
    saved = sys.modules.get(name)
    sys.modules.pop(name, None)
    try:
        gr.freeze_clock(_INSTANT)
        spec = importlib.util.find_spec(name)
        assert spec is not None
        assert isinstance(spec.loader, gr._FreezingLoader)
    finally:
        gr.unfreeze_clock()
        # Leave sys.modules exactly as it was: a second module object for
        # regional_comps would mean a second set of its caches, and the
        # resulting failures would land far from here.
        sys.modules.pop(name, None)
        if saved is not None:
            sys.modules[name] = saved


def test_the_hook_ignores_imports_it_has_no_business_touching():
    """Narrow on purpose.

    Wrapping every import would slow the whole suite and put a first-party
    loader in front of pandas and psycopg, where the type-layout problem that
    forced the stdlib to stay untouched could reappear from a new direction.
    """
    gr.freeze_clock(_INSTANT)
    try:
        for name in ("json", "os.path", "pandas", "pytest",
                     "not_installed_anywhere_xyz"):
            assert gr._FINDER.find_spec(name) is None, name
        # Already-imported first-party modules are the sweep's job, not the
        # hook's: wrapping a loader that will never run is noise.
        assert gr._FINDER.find_spec("src.memory.golden_replay") is None
    finally:
        gr.unfreeze_clock()


def test_the_wrapping_loader_rebinds_the_module_it_executes():
    """The loader half of the hook, exercised without importing anything real.

    The obvious version of this test pops ``src.data.regional_comps`` out of
    ``sys.modules`` and re-imports it under the freeze. It was written that way
    first, and it left a lasting side effect on shared state: two layering
    tests in ``test_regional_comps`` then failed with the live comps treated as
    stale, and only when the modules ran in that order. Re-executing a
    production module is not a neutral act, so the loader is driven directly
    against a synthetic module instead.
    """
    import types
    executed: list = []

    class _Inner:
        """Stands in for the real loader; mimics ``from datetime import …``."""

        def exec_module(self, module):
            executed.append(module)
            module.datetime = datetime
            module.date = date

    probe = types.ModuleType("src.memory._loader_probe")
    gr.freeze_clock(_INSTANT)
    try:
        gr._FreezingLoader(_Inner()).exec_module(probe)
        assert probe.datetime is gr._FrozenDatetime
        assert probe.date is gr._FrozenDate
        assert probe.datetime.now(timezone.utc) == _INSTANT
    finally:
        gr.unfreeze_clock()
    assert executed == [probe], "the wrapped loader must still do the loading"


def test_the_rebind_reaches_the_date_class_too():
    """``regional_comps`` imports no ``date``, so probe the branch directly.

    A first-party module that did ``from datetime import date`` and called
    ``date.today()`` is the same bug wearing a different hat; the rebind covers
    it, and this is the only place that proves it.
    """
    import types
    probe = types.ModuleType("src.memory._clock_probe")
    probe.datetime, probe.date = datetime, date
    sys.modules[probe.__name__] = probe
    try:
        gr.freeze_clock(_INSTANT)
        assert probe.datetime is gr._FrozenDatetime
        assert probe.date is gr._FrozenDate
        assert probe.date.today() == date(2026, 9, 16)
        gr.unfreeze_clock()
        assert probe.datetime is datetime
        assert probe.date is date
    finally:
        sys.modules.pop(probe.__name__, None)


@pytest.mark.parametrize("value", [None, "", "garbage"])
def test_freeze_clock_pins_nothing_for_an_unusable_instant(value):
    """Falls back to the live clock — pre-fix behaviour, correct if unstable."""
    assert gr.freeze_clock(value) is None
    assert gr.frozen_now() is None
    assert rc.datetime is datetime


def test_now_does_not_advance_while_frozen():
    gr.freeze_clock(_INSTANT)
    first = rc.datetime.now(timezone.utc)
    time.sleep(0.05)
    second = rc.datetime.now(timezone.utc)
    assert first == second == _INSTANT


def test_now_is_naive_utc_when_no_timezone_is_requested():
    """Deliberate divergence from the real ``now()``, which is naive LOCAL.

    A baseline that depends on the timezone of whoever regenerated it is not a
    baseline. Nothing in the projection reads the naive path today — the field
    that drifted asks for ``now(timezone.utc)`` — so this costs nothing and
    makes the snapshot reproducible on any machine.
    """
    gr.freeze_clock(_INSTANT)
    naive = rc.datetime.now()
    assert naive.tzinfo is None
    assert naive == datetime(2026, 9, 16, 13, 22, 12)


def test_now_converts_when_a_timezone_is_requested():
    gr.freeze_clock(_INSTANT)
    sgt = timezone(timedelta(hours=8))
    assert rc.datetime.now(sgt) == datetime(2026, 9, 16, 21, 22, 12, tzinfo=sgt)


def test_utcnow_is_naive_utc():
    gr.freeze_clock(_INSTANT)
    assert rc.datetime.utcnow() == datetime(2026, 9, 16, 13, 22, 12)


def test_datetime_today_and_date_today_are_the_frozen_date():
    gr.freeze_clock(_INSTANT)
    assert rc.datetime.today() == date(2026, 9, 16)
    assert gr._FrozenDate.today() == date(2026, 9, 16)


def test_a_frozen_clock_still_constructs_and_parses_real_timestamps():
    """Subclassing must not break ordinary use.

    ``fromisoformat``, arithmetic and ``isoformat`` all run on the frozen class
    inside the engine. If any of them returned the pinned instant instead of
    the requested value, the freeze would corrupt far more than one field.
    """
    gr.freeze_clock(_INSTANT)
    parsed = rc.datetime.fromisoformat("2020-01-02T03:04:05+00:00")
    assert parsed == datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert (parsed + timedelta(days=1)).day == 3
    assert rc.datetime(2021, 5, 6, tzinfo=timezone.utc).isoformat() == \
        "2021-05-06T00:00:00+00:00"


def test_unfreeze_restores_the_real_classes_in_first_party_modules():
    gr.freeze_clock(_INSTANT)
    gr.unfreeze_clock()
    assert gr.frozen_now() is None
    assert rc.datetime is datetime
    # regional_comps imports `date` since the NTM blend (2026-09-21), which dates
    # each analyst-estimate row against today. `_rebind_module` freezes and
    # restores it alongside `datetime`; this is the assertion the old guard
    # ("gained a `date` import; assert it is restored too") asked for.
    assert rc.date is date


def test_unfreeze_hands_back_a_live_clock():
    gr.freeze_clock(_INSTANT)
    gr.unfreeze_clock()
    first = rc.datetime.now()
    time.sleep(0.05)
    assert rc.datetime.now() > first


# ── The field that actually drifted ─────────────────────────────────────────

_COMPUTED_AT = "2026-09-13T13:22:12+00:00"   # exactly three days before _INSTANT


def test_comp_age_days_is_a_pure_function_of_the_frozen_clock():
    gr.freeze_clock(_INSTANT)
    assert _age_days(_COMPUTED_AT) == pytest.approx(3.0, abs=1e-9)


def test_comp_age_days_tracks_the_frozen_clock_and_nothing_else():
    """The regression, stated as an invariant rather than as one number.

    Three days of age at the pinned instant, thirteen days ten days later. The
    drift was real time leaking into a supposedly frozen replay; this fails the
    moment it leaks again.
    """
    gr.freeze_clock(_INSTANT)
    assert _age_days(_COMPUTED_AT) == pytest.approx(3.0, abs=1e-9)
    gr.freeze_clock((_INSTANT + timedelta(days=10)).isoformat())
    assert _age_days(_COMPUTED_AT) == pytest.approx(13.0, abs=1e-9)


def test_on_the_live_clock_the_age_keeps_moving_which_is_why_it_is_pinned():
    """Documents the pre-fix behaviour rather than only asserting the fix."""
    gr.unfreeze_clock()
    assert gr.frozen_now() is None
    live = _age_days(_COMPUTED_AT)
    assert live is not None and live > 0


# ── Replay wiring ───────────────────────────────────────────────────────────

def test_replay_freezes_on_entry_and_sweeps_after_the_engine_is_imported():
    """Source-order pin, same technique as the growth-gate ordering test.

    Both halves matter. Freezing on entry installs the import hook before
    anything is loaded, so the engine's whole import graph is rebound as it
    arrives; the sweep after the deferred ``dcf_agent`` import catches whatever
    reached ``sys.modules`` by a route the hook does not see. Drop either line
    and the fix silently stops working while every unit test above still
    passes.
    """
    src = Path(gr.__file__).read_text(encoding="utf-8")
    body = src[src.index("def replay_fixture"):]
    freeze_at = body.index("freeze_clock(")
    build_state_at = body.index("build_state(")
    import_at = body.index("from src.agents.analysis.dcf_agent import")
    sweep_at = body.index("_rebind_all()")
    assert freeze_at < build_state_at, "the instant must be pinned before state"
    assert import_at < sweep_at, (
        "the sweep must come after the engine import, or it cannot reach the "
        "modules that import pulled in"
    )


def test_replay_honours_the_env_override_before_the_fixture_timestamp():
    """Caller > env > fixture, so a test can pin the clock without a fixture.

    Asserted on the source because the ordering is a one-line ``or`` chain:
    swapping two of its operands changes which instant every baseline is pinned
    to, and nothing else in the suite would notice.
    """
    src = Path(gr.__file__).read_text(encoding="utf-8")
    body = src[src.index("def replay_fixture"):]
    start = body.index("freeze_clock(")
    end = body.index("captured_at", start) + len("captured_at")
    window = body[start:end]
    # The env var appears in the source as its NAME, not as its value.
    assert (window.index("frozen_now") < window.index("_FROZEN_NOW_ENV")
            < window.index("captured_at")), (
        f"expected caller > env > captured_at, got: {window!r}"
    )


# ── End to end, in the subprocess the suite actually uses ───────────────────

def _replay_at(name: str, out_dir: Path, frozen: str | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = (frozen or "default").replace(":", "").replace("+", "")
    out = out_dir / f"{name}-{tag}.json"
    env = {k: v for k, v in os.environ.items() if k not in gc.SECRET_ENV_VARS}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop(gr._FROZEN_NOW_ENV, None)
    if frozen:
        env[gr._FROZEN_NOW_ENV] = frozen
    proc = subprocess.run(
        [sys.executable, "-m", "src.memory.golden_replay", name, str(out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=420, encoding="utf-8", errors="replace",
    )
    assert out.exists(), (
        f"child produced no output\nexit={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-1500:]}\n--- stderr ---\n{proc.stderr[-1500:]}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_a_replay_pins_itself_to_the_fixtures_captured_at(tmp_path):
    result = _replay_at(_CLOCKED_FIXTURE, tmp_path, None)
    assert result["ok"], result.get("error") or result.get("skip_reason")
    assert result["meta"]["frozen_now"] == result["meta"]["captured_at"], (
        "replay must default to the instant the fixture was recorded, so the "
        "ages it computes are the ages the engine saw"
    )


def test_moving_the_wall_clock_moves_only_the_clock_field(tmp_path):
    """The whole point, proved end to end.

    Ten days of real time used to be able to move a pinned valuation. Here the
    frozen clock is shifted ten days explicitly: ``comp_age_days`` moves by
    exactly ten, and every other field of the projection is identical. The
    blast radius of the calendar is now one field, known and named — and if a
    lazily imported module ever adds a second one, this test names it.
    """
    baseline = _replay_at(_CLOCKED_FIXTURE, tmp_path, None)
    assert baseline["ok"], baseline.get("error") or baseline.get("skip_reason")
    captured = datetime.fromisoformat(baseline["meta"]["captured_at"])

    shifted = _replay_at(_CLOCKED_FIXTURE, tmp_path,
                         (captured + timedelta(days=10)).isoformat())
    assert shifted["ok"], shifted.get("error") or shifted.get("skip_reason")

    a, b = baseline["projection"], shifted["projection"]
    assert _CLOCKED_FIELD in a, (
        f"{_CLOCKED_FIXTURE} no longer projects {_CLOCKED_FIELD}; pick another "
        f"clock-dependent fixture or this test proves nothing"
    )
    # Since 2026-09-20 replay serves each fixture's comps -- including their
    # age -- from comps.json, so the clock no longer reaches even this field:
    # the calendar's blast radius went from one named field to none.
    assert b[_CLOCKED_FIELD] == a[_CLOCKED_FIELD]

    others = sorted(k for k in set(a) | set(b) if k != _CLOCKED_FIELD)
    moved = [k for k in others if a.get(k) != b.get(k)]
    assert not moved, (
        f"{len(moved)} field(s) other than {_CLOCKED_FIELD} responded to the "
        f"clock — each is an unfrozen input:\n  " + "\n  ".join(
            f"{k}: {a.get(k)!r} -> {b.get(k)!r}" for k in moved[:20])
    )
    assert baseline["base_iv"] == shifted["base_iv"]


def test_the_ntm_blend_dates_estimates_against_the_frozen_day_not_the_wall_clock():
    """A replayed refresh must weight FY1/FY2 by the fixture's date. Frozen at
    2026-09-16, a 31 December year end is 106 days out; on the wall clock it
    would drift a day at a time and move every NTM multiple with it."""
    rows = [{"date": "2026-12-31", "epsAvg": 10.0}, {"date": "2027-12-31", "epsAvg": 20.0}]
    gr.freeze_clock(_INSTANT)
    try:
        assert rc.date.today() == date(2026, 9, 16)
        w = 106 / 365.0
        assert rc.ntm_blend(rows, "epsAvg") == pytest.approx(w * 10.0 + (1 - w) * 20.0)
    finally:
        gr.unfreeze_clock()
