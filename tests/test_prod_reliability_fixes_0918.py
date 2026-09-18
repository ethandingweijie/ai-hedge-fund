"""
tests/test_prod_reliability_fixes_0918.py
=========================================
The three production-reliability fixes ordered after the EL stuck-run
post-mortem of 2026-09-18, plus the credential redactor that post-mortem
exposed.

What happened, in one paragraph, because every assertion below is anchored to
a piece of it. A redeploy SIGKILLed the worker 25 s into phase 4.5 of an EL
run. The Redis phase map survived with no ``completed`` marker, so
``/analysis/status`` computed ``in_progress = not bool(latest.get("completed"))``
and reported the run alive indefinitely, frozen on ``dcf_engine`` -- rendered
by the frontend as "Computing the valuation model". The browser polled 505
times over 1.5+ hours. The phase banner ``[4.5/10] DCF Engine`` never reached
the log even though the phase demonstrably ran, because Python block-buffers
stdout in a container and the buffer died with the process. And the same
service had been logging its live Redis password at INFO.

Runs without Redis, a network, or a container. The heartbeat guard is
exercised through its own seams (``progress_bus.get_phase_map``,
``redis_client.redis_ready`` / ``get_redis``). The pipeline and Dockerfile
changes are pinned at source level, where observing the behaviour would need a
real hang or a real kill.

FIX 1  PYTHONUNBUFFERED=1 in every Dockerfile -- and in the one that is built.
FIX 2  a deadline on phase 4.5, and a dead-worker guard on /analysis/status.
FIX 3  the Redis URL is redacted before it can reach a log line.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.backend.routes import analysis as A
from app.backend.services import progress_bus, redis_client as RC
from app.backend.services.redis_client import _WITHHELD, redacted_url
import src.pipeline as P

ROOT = pathlib.Path(__file__).resolve().parents[1]

DOCKERFILES = ["docker/Dockerfile", "docker/Dockerfile.web", "docker/Dockerfile.worker"]


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeRedis:
    """Just enough of redis.asyncio for the heartbeat guard: one GET."""

    def __init__(self, health):
        self.health = health
        self.asked_for: list = []

    async def get(self, key):
        self.asked_for.append(key)
        return self.health if key == A._ARQ_HEALTH_KEY else None


def _patch_redis(monkeypatch, health, ready: bool = True) -> _FakeRedis:
    """Point the guard's Redis reads at a stub.

    The guard imports ``redis_client`` INSIDE the function body, so it resolves
    these attributes at call time and patching the module reaches it.
    """
    fake = _FakeRedis(health)

    async def _ready(force: bool = False):
        return ready

    async def _get():
        return fake if ready else None

    monkeypatch.setattr(RC, "redis_ready", _ready)
    monkeypatch.setattr(RC, "get_redis", _get)
    return fake


def _stamp(age_s: float) -> str:
    """An aware ISO stamp ``age_s`` in the past -- what update_status writes."""
    return (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()


def _patch_bus(monkeypatch, latest: dict, extra: dict | None = None):
    """Serve one phase map, and record whether anything tried to delete it."""
    calls = {"cleared": []}
    phase_map = {"__latest__": latest, **(extra or {"dcf_engine": latest})}

    async def fake_map(t):
        return dict(phase_map)

    async def fake_clear(t):
        calls["cleared"].append(t)

    monkeypatch.setattr(progress_bus, "get_phase_map", fake_map)
    monkeypatch.setattr(progress_bus, "clear_ticker", fake_clear)
    return calls


def _status(monkeypatch, ticker="MSFT", in_flight=None):
    monkeypatch.setattr(A, "_in_flight", dict(in_flight or {}))
    import asyncio
    return asyncio.run(A.get_pipeline_status(ticker))


# ══════════════════════════════════════════════════════════════════════════════
# FIX 3 — the credential never reaches the log
# ══════════════════════════════════════════════════════════════════════════════

class TestRedactedUrl:
    def test_the_owners_exact_example(self):
        assert redacted_url("redis://default:hunter2@redis.railway.internal:6379") \
            == "redis://default:***@redis.railway.internal:6379"

    def test_host_port_scheme_and_db_survive(self):
        """The point of the line is WHICH Redis failed -- that part is not a
        credential and must not be masked away into uselessness."""
        out = redacted_url("rediss://default:s3cr3t@redis.internal:6380/2")
        assert out == "rediss://default:***@redis.internal:6380/2"

    def test_a_password_containing_at_does_not_leak_its_tail(self):
        """RFC 3986 ends userinfo at the LAST '@'. A first-'@' split reads the
        host as 'ss@host:6379' and publishes the password's tail in clear text
        -- on a line that still LOOKS redacted. Caught by writing this test,
        not by reasoning about the docstring."""
        out = redacted_url("redis://default:p@ss@host:6379")
        assert out == "redis://default:***@host:6379"
        assert "ss" not in out

    def test_a_password_containing_a_colon_is_fully_masked(self):
        """The username cannot contain an unencoded ':', so the FIRST colon in
        the userinfo is the separator -- masking after it takes the whole
        password even when the password itself has colons."""
        assert redacted_url("redis://u:a:b:c@h:6379") == "redis://u:***@h:6379"

    def test_no_credentials_passes_through_unchanged(self):
        """The local dev default. Nothing to hide, and the host says WHICH
        Redis failed to answer."""
        assert redacted_url("redis://localhost:6379") == "redis://localhost:6379"

    def test_a_bare_username_still_discloses_authentication(self):
        assert redacted_url("redis://user@host:6379") == "redis://user:***@host:6379"

    @pytest.mark.parametrize("url", ["", None])
    def test_empty_inputs_return_empty_not_none(self, url):
        assert redacted_url(url) == ""

    def test_an_unparseable_url_is_withheld_never_echoed(self):
        """A redactor whose failure mode is 'return the input' is worse than no
        redactor: the caller cannot tell the two apart in the log."""
        assert redacted_url("redis://[bad") == _WITHHELD
        assert "bad" not in _WITHHELD

    def test_the_secret_never_survives_any_of_the_above(self):
        for url in ("redis://default:hunter2@h:6379", "redis://u:p@ss@h:1",
                    "rediss://a:b@c:6379/3"):
            out = redacted_url(url)
            for secret in ("hunter2", "p@ss", ":b@"):
                assert secret not in out, f"{secret!r} leaked through {out!r}"


class TestNoLogSitePublishesTheRawUrl:
    """The redactor is only worth anything if both call sites use it. A
    redactor nobody calls is worse than none, because it reads as fixed."""

    def test_every_logger_call_in_the_module_wraps_redis_url(self):
        src = (ROOT / "app/backend/services/redis_client.py").read_text(encoding="utf-8")
        calls = re.findall(r"logger\.\w+\((?:[^()]|\([^()]*\))*\)", src, re.DOTALL)
        assert calls, "no logger calls found -- the regex, not the module, is wrong"
        offenders = [c for c in calls
                     if "redis_url()" in c and "redacted_url(redis_url())" not in c]
        assert offenders == [], f"raw redis_url() in a log call: {offenders}"

    def test_both_availability_branches_are_covered(self):
        """The reachable AND the unreachable branch. The second is the one that
        fires during an outage, which is exactly when someone goes log-diving."""
        src = (ROOT / "app/backend/services/redis_client.py").read_text(encoding="utf-8")
        assert src.count("redacted_url(redis_url())") == 2


# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 — pipeline stdout must survive the container
# ══════════════════════════════════════════════════════════════════════════════

class TestPythonUnbuffered:
    @pytest.mark.parametrize("rel", DOCKERFILES)
    def test_the_dockerfile_sets_it(self, rel):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        assert re.search(r"^ENV\s+PYTHONUNBUFFERED=1\s*$", txt, re.MULTILINE), \
            f"{rel} does not set PYTHONUNBUFFERED=1"

    def test_the_dockerfile_railway_actually_builds_sets_it(self):
        """THE load-bearing pin. There is one railway.toml for three services
        and it names ONE dockerfilePath; `worker_main.py`'s docstring records
        that every service inherits it and branches on START_CMD at runtime.
        So `docker/Dockerfile.worker`'s own CMD never runs in production, and
        fixing only that file would have changed nothing while looking like a
        fix. This test fails if the built file is ever not among the fixed."""
        toml = (ROOT / "railway.toml").read_text(encoding="utf-8")
        m = re.search(r'dockerfilePath\s*=\s*"([^"]+)"', toml)
        assert m, "railway.toml no longer names a dockerfilePath"
        built = m.group(1).replace("\\", "/")
        txt = (ROOT / built).read_text(encoding="utf-8")
        assert re.search(r"^ENV\s+PYTHONUNBUFFERED=1\s*$", txt, re.MULTILINE), \
            f"the BUILT Dockerfile ({built}) does not set PYTHONUNBUFFERED=1"

    def test_it_is_set_as_env_not_only_on_a_cmd(self):
        """`python -u` would cover only the processes these files name. START_CMD
        is substituted at runtime, so the worker runs `python -m
        app.backend.worker_main` from an env var the Dockerfile never mentions."""
        for rel in DOCKERFILES:
            txt = (ROOT / rel).read_text(encoding="utf-8")
            assert "ENV PYTHONUNBUFFERED=1" in txt


# ══════════════════════════════════════════════════════════════════════════════
# FIX 2a — phase 4.5 has a deadline
# ══════════════════════════════════════════════════════════════════════════════

class TestTheDcfPhaseHasADeadline:
    def test_the_constant_exists_and_is_env_gated(self):
        assert P._DCF_PHASE_TIMEOUT_S > 60.0
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        assert '_DCF_PHASE_TIMEOUT_S = _env_seconds("PIPELINE_DCF_TIMEOUT_S"' in src

    def test_the_bare_call_is_gone(self):
        """A hang is not an exception, so the try/except that was already there
        could not catch one. `state = run_dcf_agent(state)` with no join is the
        whole defect; if this string comes back the deadline has been undone."""
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        assert "state = run_dcf_agent(state)" not in src

    def test_it_joins_through_bounded_join_with_its_own_timeout(self):
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        i = src.index("_ctx_submit(_dcf_ex, run_dcf_agent, state)")
        window = src[max(0, i - 400):i + 400]
        assert "_bounded_join(" in window
        assert "_DCF_PHASE_TIMEOUT_S" in window

    def test_it_submits_through_the_contextvar_copying_helper(self):
        """`executor.submit` runs in a fresh context. `update_status` stamps
        every event with `_run_id_var.get()` so handlers for other runs can drop
        it, so a bare submit would unbind every phase event published from
        inside the DCF -- the frontend would freeze on the previous phase, which
        is the same user-visible symptom this whole fix exists to remove."""
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        assert "_ctx_submit(_dcf_ex, run_dcf_agent, state)" in src
        assert re.search(r"ThreadPoolExecutor\(max_workers=1\)\s*\n\s*try:", src)

    def test_the_crash_flag_is_initialised_before_the_cache_branch(self):
        """Both paths reach the success publish at the bottom of the block. The
        cache path never enters the try/except, so a flag defined inside it
        would NameError on every cached run.

        Anchored on the unique `_timed("4_5_dcf_engine")` marker and searched
        FORWARD from it, because a bare `src.index('_all_cached("dcf_range"')`
        finds the wrong one: that string also appears 100 lines earlier inside a
        comment about the SOTP snapshot's cache invalidation. The first draft of
        this test failed on exactly that -- a false failure, but only by luck of
        which occurrence came first.
        """
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        assert src.count('_timed("4_5_dcf_engine")') == 1
        blk = src.index('_timed("4_5_dcf_engine")')
        body = src[blk:]
        init = body.index("_dcf_crashed = False")
        cache = body.index('_all_cached("dcf_range"')
        assert init < cache, (
            "the flag is defined after the cache branch, so a cached run "
            "reaches the `if not _dcf_crashed` guard with the name unbound")
        # ...and both are inside this block, not somewhere later in the file.
        assert cache < body.index("if not _dcf_crashed:")


class TestTheSuccessStatusCannotOverwriteTheCrashStatus:
    def test_the_success_publish_is_conditional(self):
        """"✓ DCF complete" used to be published unconditionally ~40 lines after
        the handler published "DCF CRASHED — …", so /analysis/status and the SSE
        stream reported a clean valuation for a run that produced none, and
        runProgress.ts's SETTLED_RE (/^✓|\\bcomplete\\b/) marked the phase done on
        the client too. The only surviving trace was the archived error."""
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        i = src.index("progress.update_status(\"dcf_engine\", primary_ticker, _dcf_status")
        assert "if not _dcf_crashed:" in src[max(0, i - 400):i]

    def test_no_hardcoded_success_string_survives_anywhere(self):
        """REGRESSION PIN, and it caught a real defect in my own fix.

        `update_status` overwrites the agent's status wholesale, so guarding ONE
        publish is not enough — any later publish of the same success string to
        the same agent undoes the guard. The sector-card mid-run emit 16 lines
        below did exactly that: it hardcoded "✓ DCF complete", so on a crashed
        DCF the guard withheld the status and the very next emit restored it. The
        string now exists in exactly one place, the `_dcf_status` initialiser,
        and every publish reads that variable.

        Counted through the AST, not `src.count()`: the explanatory comment on
        the sector-card emit quotes the string, so a textual count returns 2 on a
        correct file. A test that fails on its own subject's documentation is a
        test nobody will trust the second time it fires.
        """
        import ast
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        hits = [n for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and n.value == "✓ DCF complete"]
        assert len(hits) == 1, (
            f"the success literal appears in {len(hits)} code positions; it must "
            f"be assigned to _dcf_status once and published through the variable")
        # ...and that one position is the initialiser, not a publish argument.
        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "_dcf_status" for t in n.targets)
                   and isinstance(n.value, ast.Constant)
                   and n.value.value == "✓ DCF complete"]
        assert len(assigns) == 1, "_dcf_status is not initialised to the success string"

    def test_every_later_status_publish_to_the_dcf_agent_reads_the_shared_string(
            self, monkeypatch):
        """Structural, so it catches a THIRD emit added later rather than only
        the two that exist today."""
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        crash = src.index("_dcf_crashed = True")
        sites = [m.start() for m in
                 re.finditer(r'update_status\(\s*"dcf_engine"', src)
                 if m.start() > crash]
        assert len(sites) >= 2, (
            f"expected the success publish and the sector-card publish after the "
            f"crash handler, found {len(sites)} -- the phase shape has changed")
        for off in sites:
            window = src[off:off + 300]
            assert "_dcf_status" in window, (
                f"a dcf_engine status publish at offset {off} does not carry "
                f"_dcf_status, so it can overwrite the crash status: {window[:120]!r}")

    def test_the_crash_status_keeps_the_exception_head(self):
        """The sector-card emit must carry the SPECIFIC message forward, not a
        generic 'it crashed'. Reusing the variable is what preserves it."""
        src = (ROOT / "src/pipeline.py").read_text(encoding="utf-8")
        assert '_dcf_status = f"DCF CRASHED — {_err_head}"' in src

    def test_the_settled_regex_still_matches_the_success_string(self):
        """Why the overwrite mattered: this is the client's own completion test,
        copied verbatim from app/frontend/src/lib/runProgress.ts. If the frontend
        ever stops treating '✓ … complete' as terminal, the overwrite becomes
        cosmetic and this test should be updated deliberately, not silently."""
        settled = re.compile(r"^✓|\bcomplete\b|\bready\b|\bskipp?(ed|ing)\b", re.I)
        assert settled.search("✓ DCF complete")
        assert not settled.search("DCF CRASHED — ValueError: boom")


# ══════════════════════════════════════════════════════════════════════════════
# FIX 2b — the polling endpoint detects a dead worker
# ══════════════════════════════════════════════════════════════════════════════

#: arq's own health string, verbatim from a live production read.
_HC_IDLE = "Sep-18 12:04:18 j_complete=34 j_failed=0 j_retried=0 j_ongoing=0 queued=0"
_HC_BUSY = "Sep-18 12:04:18 j_complete=34 j_failed=0 j_retried=0 j_ongoing=1 queued=0"


class TestTheIncidentItself:
    """The exact shape that hung: a stale phase map, no completion marker, and
    a REPLACEMENT worker that is healthy but executing nothing."""

    def test_a_stale_map_with_an_idle_worker_flips_to_terminated(self, monkeypatch):
        latest = {"phase": "dcf_engine", "status": "Computing the valuation model",
                  "summary": "s", "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert out["in_progress"] is False
        assert out["status"] == A._STATUS_CONTAINER_TERMINATED

    def test_a_missing_health_key_also_flips(self, monkeypatch):
        """No key = no healthy worker attached to this queue at all: arq writes
        it every 60 s with a 61 s TTL, the same reading /admin/diag makes."""
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, None)
        out = _status(monkeypatch)
        assert out["in_progress"] is False
        assert out["status"] == A._STATUS_CONTAINER_TERMINATED

    def test_the_phase_it_died_in_is_preserved(self, monkeypatch):
        """WHICH phase the container died in is the useful half of the answer.
        Rewriting it to null would make the report unactionable."""
        latest = {"phase": "dcf_engine", "status": "Computing the valuation model",
                  "summary": "s", "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest, extra={"dcf_engine": dict(latest),
                                               "data_router": {"phase": "data_router"}})
        _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert out["phase"] == "dcf_engine"
        assert set(out["all_phases"]) == {"dcf_engine", "data_router"}
        assert out["all_phases"]["dcf_engine"]["status"] == \
            "Computing the valuation model"

    def test_the_phase_map_is_not_deleted(self, monkeypatch):
        """An invariant that erases the evidence of its own violation cannot be
        audited. The map is the only record of where the container died."""
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        calls = _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        _status(monkeypatch)
        assert calls["cleared"] == []

    def test_the_summary_says_re_run_rather_than_nothing(self, monkeypatch):
        latest = {"phase": "dcf_engine", "status": "running", "summary": "old",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert "dcf_engine" in out["summary"]
        assert "Re-run" in out["summary"]


class TestTheGuardNeverFiresOnALiveRun:
    """Every one of these is a false positive that would tell a user their
    in-flight valuation is dead while it is still computing. Each resolves to
    'still running'."""

    def test_a_worker_executing_something_does_not_flip(self, monkeypatch):
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_BUSY)
        out = _status(monkeypatch)
        assert out["in_progress"] is True
        assert out["status"] == "running"

    def test_a_fresh_map_inside_the_grace_does_not_flip(self, monkeypatch):
        """arq samples its own health string every 60 s, so `j_ongoing` can lag
        a job claimed milliseconds ago. Without the grace the first status poll
        of every run would report it terminated."""
        latest = {"phase": "pipeline_queued", "status": "running",
                  "timestamp": _stamp(5.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert out["in_progress"] is True

    def test_the_grace_is_at_least_two_health_intervals(self, monkeypatch):
        assert A._HEARTBEAT_GRACE_S >= 2 * 60

    def test_a_long_silent_phase_is_not_death(self, monkeypatch):
        """Deep research runs ~12 min and publishes nothing while it works, so
        the worker being idle is not sufficient on its own -- j_ongoing is the
        discriminator, not the silence. With a job in flight, a 13-minute-old
        map still reads as running."""
        latest = {"phase": "deep_research", "status": "Researching",
                  "timestamp": _stamp(780.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_BUSY)
        out = _status(monkeypatch)
        assert out["in_progress"] is True
        assert out["status"] == "Researching"

    def test_an_in_process_run_is_exempt(self, monkeypatch):
        """Queue mode off (or enqueue failed) means THIS web process runs the
        pipeline in a thread and writes the same phase map. The worker's
        j_ongoing says nothing about it, and a silent phase would otherwise
        read as a dead container."""
        latest = {"phase": "deep_research", "status": "Researching",
                  "timestamp": _stamp(900.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch, in_flight={"MSFT": object()})
        assert out["in_progress"] is True

    def test_a_naive_timestamp_is_unboundable_so_it_is_refused(self, monkeypatch):
        """`web_runs.run_at` and `ticker_routing_cache.last_updated` are both
        written on a naive-local clock, and prod reads local rows as the future.
        Bounding an age against a naive stamp would be a guess, not a measure."""
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": datetime.now().isoformat()}
        _patch_bus(monkeypatch, latest)
        fake = _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert out["in_progress"] is True
        assert fake.asked_for == [], "the guard consulted Redis on a naive stamp"

    @pytest.mark.parametrize("ts", ["", None, "t0", "not-a-timestamp"])
    def test_a_missing_or_unparseable_timestamp_does_not_flip(self, monkeypatch, ts):
        latest = {"phase": "dcf_engine", "status": "running", "timestamp": ts}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE)
        assert _status(monkeypatch)["in_progress"] is True

    def test_a_missing_timestamp_key_at_all_does_not_flip(self, monkeypatch):
        _patch_bus(monkeypatch, {"phase": "dcf_engine", "status": "running"})
        _patch_redis(monkeypatch, _HC_IDLE)
        assert _status(monkeypatch)["in_progress"] is True

    def test_redis_unreachable_does_not_flip(self, monkeypatch):
        """If Redis is down there is no bus map to begin with, but the guard
        must not invent a verdict on the way to finding that out."""
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, _HC_IDLE, ready=False)
        assert _status(monkeypatch)["in_progress"] is True

    def test_an_unparseable_health_string_leaves_the_guard_inert(self, monkeypatch):
        """arq owns that string. If its format changes the guard must stop
        working rather than start guessing -- and it logs, because an inert
        guard is otherwise indistinguishable from a healthy system."""
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        _patch_redis(monkeypatch, "some future arq format j_running=0")
        assert _status(monkeypatch)["in_progress"] is True

    def test_a_redis_exception_does_not_flip(self, monkeypatch):
        latest = {"phase": "dcf_engine", "status": "running",
                  "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)

        class _Boom:
            async def get(self, key):
                raise ConnectionError("redis went away")

        async def _ready(force=False):
            return True

        async def _get():
            return _Boom()

        monkeypatch.setattr(RC, "redis_ready", _ready)
        monkeypatch.setattr(RC, "get_redis", _get)
        assert _status(monkeypatch)["in_progress"] is True

    def test_a_completed_run_never_consults_the_guard(self, monkeypatch):
        latest = {"phase": "pipeline_complete", "status": "done",
                  "completed": True, "timestamp": _stamp(600.0)}
        _patch_bus(monkeypatch, latest)
        fake = _patch_redis(monkeypatch, _HC_IDLE)
        out = _status(monkeypatch)
        assert out["in_progress"] is False
        assert out["status"] == "done"
        assert out["phase"] == "pipeline_complete"
        assert fake.asked_for == []


class TestTheGuardIsWiredIntoTheRouteNotJustDefined:
    def test_the_route_calls_it(self):
        src = (ROOT / "app/backend/routes/analysis.py").read_text(encoding="utf-8")
        assert "await _worker_heartbeat_dead(t, latest)" in src
        assert "_STATUS_CONTAINER_TERMINATED" in src

    def test_the_old_unconditional_inference_is_gone(self):
        """`in_progress = not bool(latest.get("completed"))` as the RETURNED
        value is the defect. It may still appear as the first assignment the
        guard then overrides, but not as the bus branch's answer."""
        src = (ROOT / "app/backend/routes/analysis.py").read_text(encoding="utf-8")
        assert '"in_progress": not bool(latest.get("completed"))' not in src

    def test_the_status_string_is_the_one_the_owner_named(self):
        assert A._STATUS_CONTAINER_TERMINATED == "CONTAINER_TERMINATED_PRE_COMPLETION"

    def test_it_reads_arqs_own_health_key(self):
        assert A._ARQ_HEALTH_KEY == "arq:queue:health-check"
        src = (ROOT / "app/backend/worker.py").read_text(encoding="utf-8")
        assert "health_check_interval" in src
