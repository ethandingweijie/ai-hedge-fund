"""Global admission control for analyses (owner, 2026-11-28, item 3).

Every worker -- however many replicas run -- shares ONE cap on analyses running at once (ANALYSIS_MAX_CONCURRENT,
default 25: Qwen's 2.5M TPM / ~40k TPM per run, with headroom). A run takes a slot on a lease in Redis before its
pipeline starts and gives it back when it ends; a lease that outlives a dead worker expires on its own. When the cap is
reached a run WAITS -- first come, first served -- and its user sees "Queued: N analyses ahead (about M min)" instead of
an error. Per-user fairness is enforced earlier, at the route: each plan has its own concurrent-run cap
(billing.CONCURRENT_BY_PLAN), so one account can never fill the queue.

No Redis -> no admission control (the run proceeds, as before). Kill switch: ANALYSIS_ADMISSION=off.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ACTIVE_KEY = "admission:active"
WAITING_KEY = "admission:waiting"
#: a slot outlives its run by at most this (the job timeout is 60 min)
LEASE_S = 65 * 60
#: average analysis wall time, for the queue ETA shown to users
AVG_RUN_MIN = 7.5

# KEYS: active zset, waiting zset. ARGV: now, run_id, cap, lease_s.
# Returns -1 when the slot is granted, else the number of runs ahead in the queue.
_TAKE = """
local now = tonumber(ARGV[1]); local cap = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZADD', KEYS[2], 'NX', now, ARGV[2])
local active = redis.call('ZCARD', KEYS[1])
local rank = redis.call('ZRANK', KEYS[2], ARGV[2])
if active < cap and rank < (cap - active) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[4]), ARGV[2])
  redis.call('ZREM', KEYS[2], ARGV[2])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
  return -1
end
redis.call('EXPIRE', KEYS[2], 7200)
return rank - math.max(0, cap - active) + 1
"""


def cap() -> int:
    try:
        return max(1, int(os.environ.get("ANALYSIS_MAX_CONCURRENT", "25")))
    except (TypeError, ValueError):
        return 25


def enabled() -> bool:
    return os.environ.get("ANALYSIS_ADMISSION", "on").strip().lower() not in ("0", "off", "false", "no")


def eta_minutes(ahead: int, slots: Optional[int] = None) -> int:
    """Rough wait: every `slots` runs ahead take one average run time to clear."""
    return max(1, math.ceil((ahead / float(slots or cap())) * AVG_RUN_MIN) if ahead > 0 else 1)


async def _redis():
    try:
        from app.backend.services.redis_client import get_redis, redis_ready
        if not await redis_ready():
            return None
        return await get_redis()
    except Exception:                                       # noqa: BLE001
        return None


async def acquire(run_id: str, on_wait: Optional[Callable[[int, int], Awaitable[None]]] = None,
                  poll_s: float = 3.0, max_wait_s: float = 30 * 60, redis_client=None) -> bool:
    """Take a run slot, waiting in line when the cap is reached. True = holding a slot (release it); False = admission
    is off or unavailable, or the wait timed out -- the run proceeds without one."""
    if not enabled():
        return False
    r = redis_client or await _redis()
    if r is None:
        return False
    start, last_ahead = time.monotonic(), None
    try:
        while True:
            ahead = int(await r.eval(_TAKE, 2, ACTIVE_KEY, WAITING_KEY, time.time(), run_id, cap(), LEASE_S))
            if ahead < 0:
                return True
            if on_wait and ahead != last_ahead:
                try:
                    await on_wait(ahead, eta_minutes(ahead))
                except Exception:                           # noqa: BLE001
                    pass
                last_ahead = ahead
            if time.monotonic() - start > max_wait_s:
                logger.warning("admission: %s waited %.0f s -- proceeding without a slot", run_id, max_wait_s)
                await r.zrem(WAITING_KEY, run_id)
                return False
            await asyncio.sleep(poll_s)
    except Exception as exc:                                # noqa: BLE001
        logger.warning("admission: Redis error (%s) -- run proceeds without a slot", exc)
        return False


async def release(run_id: str, redis_client=None) -> None:
    r = redis_client or await _redis()
    if r is None:
        return
    try:
        await r.zrem(ACTIVE_KEY, run_id)
        await r.zrem(WAITING_KEY, run_id)
    except Exception as exc:                                # noqa: BLE001
        logger.warning("admission: release failed for %s: %s", run_id, exc)


async def snapshot(redis_client=None) -> dict:
    """For /admin/diag: the cap, runs holding a slot, runs waiting."""
    r = redis_client or await _redis()
    if r is None:
        return {"enabled": enabled(), "redis": False, "cap": cap()}
    try:
        await r.zremrangebyscore(ACTIVE_KEY, "-inf", time.time())
        return {"enabled": enabled(), "redis": True, "cap": cap(), "active": await r.zcard(ACTIVE_KEY),
                "waiting": await r.zcard(WAITING_KEY)}
    except Exception as exc:                                # noqa: BLE001
        return {"enabled": enabled(), "redis": False, "cap": cap(), "error": str(exc)[:200]}
