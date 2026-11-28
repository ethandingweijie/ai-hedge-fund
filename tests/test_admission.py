"""Item 3 (owner, 2026-11-28): one global cap on analyses running at once, first come first served, with leases."""
import asyncio

import pytest

fakeredis = pytest.importorskip("fakeredis")
pytest.importorskip("lupa")
from fakeredis import aioredis

from app.backend.services import admission as adm


@pytest.fixture
def r(monkeypatch):
    monkeypatch.setenv("ANALYSIS_MAX_CONCURRENT", "2")
    monkeypatch.setenv("ANALYSIS_ADMISSION", "on")
    return aioredis.FakeRedis(server=fakeredis.FakeServer())


def test_cap_queue_order_and_release(r):
    async def go():
        assert await adm.acquire("a", redis_client=r, poll_s=0.01)
        assert await adm.acquire("b", redis_client=r, poll_s=0.01)
        seen = []

        async def on_wait(ahead, eta):
            seen.append((ahead, eta))

        c = asyncio.create_task(adm.acquire("c", on_wait=on_wait, redis_client=r, poll_s=0.01))
        await asyncio.sleep(0.05)
        d = asyncio.create_task(adm.acquire("d", on_wait=on_wait, redis_client=r, poll_s=0.01))
        await asyncio.sleep(0.05)
        assert not c.done() and not d.done()
        assert (1, adm.eta_minutes(1)) in seen and (2, adm.eta_minutes(2)) in seen
        await adm.release("a", redis_client=r)
        assert await asyncio.wait_for(c, 1)                       # first in line goes first
        await asyncio.sleep(0.05)
        assert not d.done()
        await adm.release("b", redis_client=r)
        assert await asyncio.wait_for(d, 1)
        snap = await adm.snapshot(r)
        assert snap["active"] == 2 and snap["waiting"] == 0 and snap["cap"] == 2
    asyncio.run(go())


def test_dead_worker_lease_expires(r, monkeypatch):
    async def go():
        monkeypatch.setattr(adm, "LEASE_S", 0)
        assert await adm.acquire("a", redis_client=r, poll_s=0.01)
        assert await adm.acquire("b", redis_client=r, poll_s=0.01)
        await asyncio.sleep(0.01)
        assert await adm.acquire("c", redis_client=r, poll_s=0.01)   # a and b's leases lapsed
    asyncio.run(go())


def test_off_or_no_redis_means_no_slot(monkeypatch):
    async def go():
        monkeypatch.setenv("ANALYSIS_ADMISSION", "off")
        assert await adm.acquire("a") is False
        monkeypatch.setenv("ANALYSIS_ADMISSION", "on")

        async def none():
            return None
        monkeypatch.setattr(adm, "_redis", none)
        assert await adm.acquire("a") is False
    asyncio.run(go())


def test_plan_concurrency():
    from types import SimpleNamespace
    from app.backend.services import billing as b
    assert b.concurrent_limit(None) is None
    assert b.concurrent_limit(SimpleNamespace(id=1, role="admin")) is None
    assert b.CONCURRENT_BY_PLAN == {"free": 1, "starter": 2, "professional": 3}
