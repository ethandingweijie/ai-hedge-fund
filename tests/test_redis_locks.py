"""
tests/test_redis_locks.py
=========================
Unit tests for app/backend/services/redis_locks.py (Phase 5 multi-replica
web). Uses an in-memory stub Redis — no live server required.
"""
import asyncio

import pytest

from app.backend.services import redis_locks
from app.backend.services.redis_locks import try_lock, unlock


# ── Stub Redis ────────────────────────────────────────────────────────────────

class _StubRedis:
    """Just enough of the redis.asyncio surface for try_lock/unlock."""

    def __init__(self):
        self.store: dict = {}
        self.set_calls: list = []
        self.eval_calls: list = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None  # redis-py returns None when NX loses
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls.append({"script": script, "numkeys": numkeys,
                                "keys_and_args": keys_and_args})
        key, token = keys_and_args[0], keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class _BrokenRedis:
    async def set(self, *a, **kw):
        raise ConnectionError("boom")

    async def eval(self, *a, **kw):
        raise ConnectionError("boom")


def _patch_client(monkeypatch, client):
    async def _get_redis():
        return client

    # redis_locks imports get_redis lazily inside each call, so patching the
    # source module is what the code sees.
    monkeypatch.setattr("app.backend.services.redis_client.get_redis", _get_redis)


# ── try_lock ──────────────────────────────────────────────────────────────────

def test_try_lock_sets_key_with_nx_and_ttl(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    acquired, token = asyncio.run(try_lock("vgpm_backfill", 7200))

    assert acquired is True
    assert token  # holder token returned for later release
    call = stub.set_calls[0]
    assert call["key"] == "lock:vgpm_backfill"
    assert call["nx"] is True
    assert call["ex"] == 7200
    assert call["value"] == token


def test_try_lock_second_acquire_loses(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    acquired1, token1 = asyncio.run(try_lock("vgpm_backfill", 7200))
    acquired2, token2 = asyncio.run(try_lock("vgpm_backfill", 7200))

    assert (acquired1, bool(token1)) == (True, True)
    assert (acquired2, token2) == (False, None)


def test_try_lock_independent_names_do_not_collide(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    ok1, _ = asyncio.run(try_lock("a", 60))
    ok2, _ = asyncio.run(try_lock("b", 60))
    assert ok1 and ok2


def test_try_lock_fails_open_without_redis(monkeypatch):
    _patch_client(monkeypatch, None)

    acquired, token = asyncio.run(try_lock("vgpm_backfill", 7200))
    assert acquired is True      # degraded single-process mode
    assert token is None         # nothing to release


def test_try_lock_fails_open_on_redis_error(monkeypatch):
    _patch_client(monkeypatch, _BrokenRedis())

    acquired, token = asyncio.run(try_lock("vgpm_backfill", 7200))
    assert acquired is True
    assert token is None


# ── unlock ────────────────────────────────────────────────────────────────────

def test_unlock_releases_own_lock(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    _, token = asyncio.run(try_lock("vgpm_backfill", 7200))
    assert "lock:vgpm_backfill" in stub.store

    asyncio.run(unlock("vgpm_backfill", token))
    assert "lock:vgpm_backfill" not in stub.store
    # compare-and-delete got our token, not just the key name
    assert stub.eval_calls[0]["keys_and_args"] == ("lock:vgpm_backfill", token)


def test_unlock_does_not_release_foreign_lock(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    _, token = asyncio.run(try_lock("vgpm_backfill", 7200))
    # Simulate TTL expiry + re-acquisition by someone else
    stub.store["lock:vgpm_backfill"] = "another-holders-token"

    asyncio.run(unlock("vgpm_backfill", token))
    assert stub.store["lock:vgpm_backfill"] == "another-holders-token"


def test_unlock_noop_without_token(monkeypatch):
    stub = _StubRedis()
    _patch_client(monkeypatch, stub)

    asyncio.run(unlock("vgpm_backfill", None))   # fail-open acquisition
    assert stub.eval_calls == []


def test_unlock_swallows_redis_errors(monkeypatch):
    _patch_client(monkeypatch, _BrokenRedis())
    # Must not raise — the TTL backstop expires a stranded lock.
    asyncio.run(unlock("vgpm_backfill", "some-token"))


# ── Shared constants ──────────────────────────────────────────────────────────

def test_vgpm_backfill_lock_constants():
    # The web admin route and the worker's scheduled task must agree on
    # exactly these values.
    assert redis_locks.VGPM_BACKFILL_LOCK_NAME == "vgpm_backfill"
    assert redis_locks.VGPM_BACKFILL_LOCK_TTL_S == 7200
