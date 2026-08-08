"""
app/backend/services/redis_client.py
=====================================
Async Redis client singleton with graceful degradation.

- REDIS_URL set (Railway Redis addon auto-injects it) → real Redis.
- REDIS_URL unset → local fallback attempt at redis://localhost:6379,
  and if nothing answers, every helper reports "not available" so the
  app falls back to in-process behaviour (local dev keeps working with
  no Redis installed).

Usage:
    from app.backend.services.redis_client import get_redis, redis_ready

    r = await get_redis()          # client or None
    if await redis_ready():        # cached ping result
        await r.publish(...)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_client = None
_client_init_lock = asyncio.Lock()

# Cached availability probe (avoids pinging Redis on every request)
_ready: Optional[bool] = None
_ready_checked_at: float = 0.0
_READY_TTL_SECONDS = 30.0
_PROBE_TIMEOUT = 1.5


def redis_url() -> Optional[str]:
    """REDIS_URL from env, defaulting to localhost for local dev."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


async def get_redis():
    """Return the shared redis.asyncio client, or None if redis-py is missing."""
    global _client
    if _client is None:
        async with _client_init_lock:
            if _client is None:
                try:
                    import redis.asyncio as aioredis
                    _client = aioredis.from_url(
                        redis_url(),
                        decode_responses=True,
                        socket_connect_timeout=2,
                        socket_timeout=5,
                    )
                except ImportError:
                    logger.warning("redis package not installed — queue features disabled")
                    return None
    return _client


async def redis_ready(force: bool = False) -> bool:
    """True if Redis answers a PING. Result cached for 30s."""
    global _ready, _ready_checked_at
    now = time.monotonic()
    if not force and _ready is not None and (now - _ready_checked_at) < _READY_TTL_SECONDS:
        return _ready

    client = await get_redis()
    if client is None:
        _ready = False
        _ready_checked_at = now
        return False
    try:
        await asyncio.wait_for(client.ping(), timeout=_PROBE_TIMEOUT)
        if _ready is not True:
            logger.info("Redis available at %s", redis_url())
        _ready = True
    except Exception:
        if _ready is not False:
            logger.info("Redis not reachable at %s — falling back to in-process mode", redis_url())
        _ready = False
    _ready_checked_at = now
    return _ready


async def close_redis() -> None:
    """Close the client on shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
