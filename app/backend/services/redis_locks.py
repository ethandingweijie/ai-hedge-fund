"""
app/backend/services/redis_locks.py
====================================
Tiny distributed lock helpers (Redis SET NX EX) for the multi-replica web
service (Phase 5). Used wherever two web replicas — or a web replica and
the arq worker — must not do the same heavy job at once.

Semantics match the scheduler service's slot locks: FAIL OPEN. If Redis is
unreachable the lock is treated as acquired (with a None token) so local
dev and a Redis outage degrade to the old single-process behaviour instead
of blocking admin work. Production runs with the Railway Redis addon, so
the fail-open path is never exercised in normal operation.

Usage:
    from app.backend.services.redis_locks import try_lock, unlock

    acquired, token = await try_lock("vgpm_backfill", ttl_s=7200)
    if not acquired:
        raise HTTPException(409, "already running")
    try:
        do_work()
    finally:
        await unlock("vgpm_backfill", token)   # no-op when token is None
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

KEY_PREFIX = "lock:"

# Canonical lock identities shared across services (web route ↔ arq worker).
# VGPM backfill: the admin trigger and the daily scheduled task must not run
# at once — they share one FMP token bucket and slow each other down badly.
# TTL exceeds worst-case runtime; the happy path deletes the key early.
VGPM_BACKFILL_LOCK_NAME = "vgpm_backfill"
VGPM_BACKFILL_LOCK_TTL_S = 2 * 3600

# Atomic compare-and-delete: only remove the key if it still holds OUR
# token, so a slow holder can never delete a lock that has already been
# re-acquired by someone else after the TTL expiry.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def try_lock(name: str, ttl_s: int) -> Tuple[bool, Optional[str]]:
    """Try to acquire ``lock:{name}`` with SET NX EX.

    Returns ``(acquired, token)``:
      * ``(True, "<token>")``  — lock held by us; pass token to unlock().
      * ``(False, None)``      — someone else holds the lock.
      * ``(True, None)``       — FAIL OPEN: Redis is missing or errored;
        behaves like the old in-process-only world. unlock() is a no-op.
    """
    from app.backend.services.redis_client import get_redis

    token = uuid.uuid4().hex
    key = f"{KEY_PREFIX}{name}"
    try:
        client = await get_redis()
        if client is None:
            return True, None
        ok = await client.set(key, token, nx=True, ex=ttl_s)
        return (True, token) if ok else (False, None)
    except Exception as exc:
        logger.warning("redis_locks: try_lock(%s) failed (%s) — failing open", name, exc)
        return True, None


async def unlock(name: str, token: Optional[str]) -> None:
    """Release ``lock:{name}`` if we own it. No-op when token is None
    (fail-open acquisition) or when Redis is unavailable."""
    if not token:
        return
    from app.backend.services.redis_client import get_redis

    key = f"{KEY_PREFIX}{name}"
    try:
        client = await get_redis()
        if client is None:
            return
        await client.eval(_RELEASE_LUA, 1, key, token)
    except Exception as exc:
        logger.warning("redis_locks: unlock(%s) failed (%s) — TTL will expire it", name, exc)
