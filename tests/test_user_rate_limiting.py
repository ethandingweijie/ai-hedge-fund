"""Phase 3c — per-user rate limiting (Redis sliding window, fails open).

The limiter guards pipeline-starting endpoints:
* daily cap      — INCR counter per UTC day (atomic Lua)
* concurrency cap — sorted-set of slot tokens pruned by score, so slots
                    self-expire after the scope's run window — no release
                    wiring into completion paths needed.

Fail-open is a hard requirement: no Redis / any error → allow. Until the
Railway Redis addon exists, nothing changes in production.

The _FakeRedis below mirrors the semantics of the two Lua scripts — the
tests exercise check_limits through its real code path (key layout,
atomicity contract, prune cutoffs) with the client swapped.
"""

import asyncio
import time

import pytest
from fastapi import HTTPException

from app.backend.services import rate_limiter as RL
from app.backend.services import redis_client


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Implements eval() for exactly the two scripts rate_limiter uses."""

    def __init__(self):
        self.zsets = {}      # key -> {member: score}
        self.counters = {}   # key -> int
        self.eval_count = 0

    async def eval(self, script, numkeys, *args):
        self.eval_count += 1
        key = args[0]
        if "ZADD" in script:
            prune_cutoff, limit, now, token, _ttl = args[1:]
            members = self.zsets.setdefault(key, {})
            stale = [m for m, s in members.items() if s <= float(prune_cutoff)]
            for m in stale:
                del members[m]
            if len(members) < int(limit):
                members[token] = float(now)
                return 1
            return 0
        # daily counter script
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _ready(force=False):
        return True

    async def _get():
        return fake

    monkeypatch.setattr(redis_client, "redis_ready", _ready)
    monkeypatch.setattr(redis_client, "get_redis", _get)
    return fake


@pytest.fixture()
def redis_down(monkeypatch):
    async def _ready(force=False):
        return False

    monkeypatch.setattr(redis_client, "redis_ready", _ready)
    return None


class _User:
    def __init__(self, uid=1, role="member"):
        self.id = uid
        self.role = role


def _check(fake_clock, **kwargs):
    """Drive check_limits with a controllable clock (slot scoring)."""
    import asyncio
    defaults = dict(user=_User(), scope="analysis",
                    daily_limit=None, concurrent_limit=None,
                    slot_ttl_seconds=100)
    defaults.update(kwargs)
    return asyncio.run(RL.check_limits(**defaults))


@pytest.fixture()
def clock(monkeypatch):
    """Controllable time.time for slot score/prune math."""
    state = {"now": 1_000_000.0}

    class _T:
        @staticmethod
        def time():
            return state["now"]

    monkeypatch.setattr(RL, "time", _T)
    return state


# ---------------------------------------------------------------------------
# Exemptions + fail-open
# ---------------------------------------------------------------------------

def test_service_call_no_user_always_allowed(fake_redis):
    _check(None, user=None, daily_limit=1, concurrent_limit=1)
    assert fake_redis.eval_count == 0  # no Redis traffic at all


def test_both_limits_none_is_noop(fake_redis):
    _check(None, daily_limit=None, concurrent_limit=None)
    assert fake_redis.eval_count == 0


def test_admin_exempt(fake_redis):
    for _ in range(5):
        _check(None, user=_User(role="admin"), daily_limit=1, concurrent_limit=1)
    assert fake_redis.eval_count == 0


def test_fail_open_when_redis_unavailable(redis_down):
    # Limits that would trip immediately — must still pass with no Redis.
    _check(None, daily_limit=0, concurrent_limit=0)


def test_fail_open_on_redis_error(fake_redis, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(fake_redis, "eval", _boom)
    _check(None, daily_limit=1, concurrent_limit=1)  # must not raise


# ---------------------------------------------------------------------------
# Daily cap
# ---------------------------------------------------------------------------

def test_daily_limit_enforced(fake_redis):
    for _ in range(20):
        _check(None, daily_limit=20)
    with pytest.raises(HTTPException) as exc:
        _check(None, daily_limit=20)
    assert exc.value.status_code == 429
    assert "Daily" in exc.value.detail
    assert exc.value.headers["Retry-After"]


def test_daily_key_scoped_by_user_day_and_scope(fake_redis):
    _check(None, user=_User(uid=7), scope="research", daily_limit=5)
    keys = list(fake_redis.counters.keys())
    assert len(keys) == 1
    key = keys[0]
    assert key.startswith("ratelimit:research:daily:7:")
    assert len(key.rsplit(":", 1)[1]) == 8  # yyyymmdd


def test_daily_counter_separate_days(fake_redis, monkeypatch):
    """Counter keys carry the UTC date — a new day gets a fresh counter."""
    from datetime import datetime, timezone

    class _DT(datetime):
        _fixed = None

        @classmethod
        def now(cls, tz=None):
            return cls._fixed

    monkeypatch.setattr(RL, "datetime", _DT)

    _DT._fixed = datetime(2026, 8, 9, 23, 59, 0, tzinfo=timezone.utc)
    _check(None, daily_limit=5)
    _DT._fixed = datetime(2026, 8, 10, 0, 1, 0, tzinfo=timezone.utc)
    _check(None, daily_limit=5)

    assert len(fake_redis.counters) == 2  # one key per UTC day


# ---------------------------------------------------------------------------
# Concurrency cap (rolling slot window)
# ---------------------------------------------------------------------------

def test_concurrent_limit_enforced(clock, fake_redis):
    with pytest.raises(HTTPException) as exc:
        for _ in range(3):
            _check(clock, concurrent_limit=2)
    assert exc.value.status_code == 429
    assert "concurrent" in exc.value.detail


def test_slots_free_after_window(clock, fake_redis):
    _check(clock, concurrent_limit=2)
    _check(clock, concurrent_limit=2)
    # Window elapses → both slots pruned → next start allowed.
    clock["now"] += 101
    _check(clock, concurrent_limit=2)


def test_slot_window_rolls_not_fixed(clock, fake_redis):
    _check(clock, concurrent_limit=1)          # t=0
    clock["now"] += 60
    with pytest.raises(HTTPException):
        _check(clock, concurrent_limit=1)      # t=60 — still inside window
    clock["now"] += 41
    _check(clock, concurrent_limit=1)          # t=101 — first slot expired


def test_concurrency_keyed_per_user(clock, fake_redis):
    """User B's slots are independent of user A's."""
    _check(clock, user=_User(uid=1), concurrent_limit=1)
    _check(clock, user=_User(uid=2), concurrent_limit=1)  # must not raise
    assert len(fake_redis.zsets) == 2


def test_daily_rejection_consumes_no_slot(fake_redis):
    """Calls rejected on the daily cap must not also take a slot;
    calls that pass the daily check do (correctly) hold one."""
    for _ in range(2):
        _check(None, daily_limit=2, concurrent_limit=5)
    slots_before = sum(len(v) for v in fake_redis.zsets.values())
    assert slots_before == 2  # the two allowed starts hold slots
    with pytest.raises(HTTPException):
        _check(None, daily_limit=2, concurrent_limit=5)
    slots_after = sum(len(v) for v in fake_redis.zsets.values())
    assert slots_after == slots_before  # rejected call added nothing


# ---------------------------------------------------------------------------
# Route wiring — research triggers as the exemplar (all six endpoints use
# the identical check_limits call after their dedupe check)
# ---------------------------------------------------------------------------

from src.data import db                                        # noqa: E402
from app.backend.services import complacency_job_store as _js  # noqa: E402
from app.backend.routes import research as R                   # noqa: E402


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "rl_run_archive.db"))
    db.close_all_connections()
    yield
    db.close_all_connections()


@pytest.fixture()
def no_queue(monkeypatch):
    """Queue mode off — patch queue_client directly: it holds an
    import-time reference to redis_ready, so patching redis_client alone
    would still ping localhost:6379 (a ~5s hang on this machine)."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.backend.services import queue_client

    async def _off():
        return False

    monkeypatch.setattr(queue_client, "queue_mode_enabled", _off)


@pytest.fixture()
def captured_spawns(monkeypatch):
    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()

    monkeypatch.setattr(R, "_spawn_background", fake_spawn)
    return spawned


def test_research_route_429_creates_no_job(
        temp_db, no_queue, captured_spawns, fake_redis):
    """Over-limit trigger → 429 propagates, no job row, nothing spawned."""
    for _ in range(30):  # burn the research daily cap through the limiter
        asyncio.run(RL.check_limits(
            user=_User(uid=9), scope="research",
            daily_limit=30, concurrent_limit=None, slot_ttl_seconds=2100))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.refresh_complacency(max_workers=3, actor=_User(uid=9)))
    assert exc.value.status_code == 429
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM complacency_jobs")["n"] == 0
    assert captured_spawns == []


def test_research_route_dedupe_bypasses_limiter(
        temp_db, no_queue, captured_spawns, fake_redis):
    """Re-attaching to an in-flight job must not consume quota."""
    first = asyncio.run(
        R.refresh_complacency(max_workers=3, actor=_User(uid=9)))
    assert first["deduped"] is False
    evals_after_first = fake_redis.eval_count

    second = asyncio.run(
        R.refresh_complacency(max_workers=3, actor=_User(uid=9)))
    assert second["deduped"] is True
    assert second["job_id"] == first["job_id"]
    assert fake_redis.eval_count == evals_after_first  # no quota consumed
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM complacency_jobs")["n"] == 1


def test_research_route_service_call_unlimited(
        temp_db, no_queue, captured_spawns, fake_redis):
    """actor=None (X-Admin-Secret / scheduler) skips limits entirely."""
    for _ in range(3):
        resp = asyncio.run(R.refresh_complacency(max_workers=3, actor=None))
        if resp["deduped"]:
            # complete the job so the next trigger is a fresh start
            _js.complete_job(resp["job_id"])
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM complacency_jobs")["n"] >= 2
    assert fake_redis.eval_count == 0  # never consulted for service calls
