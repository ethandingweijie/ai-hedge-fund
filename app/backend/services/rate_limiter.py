"""
app/backend/services/rate_limiter.py
=====================================
Per-user rate limits for pipeline-starting endpoints (Phase 3c).

Two dimensions per scope, both enforced atomically in Redis via Lua:

* daily      — pipeline starts per UTC day (INCR counter that expires at
               day end).
* concurrent — starts within a rolling slot window (sorted set of slot
               tokens scored by start time; entries older than the window
               are pruned on every check, so slots self-clear after the
               scope's typical run duration — no release wiring into every
               completion path; a crashed run frees its slot by expiry).

FAILS OPEN: no Redis, Redis unreachable, or any error mid-check → allow.
Rate limiting is a capacity guardrail, not a security boundary, and it
must never take the app down with it. Until the Railway Redis addon
exists, every check passes and behaviour is identical to pre-3c.

Exemptions: service callers (no user identity — schedulers, the
X-Admin-Secret path) and users with role='admin' (operators must stay
able to debug a saturated system).
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ratelimit"

# R2 failure surfacing — the Redis-absent fail-open branch used to be
# silent, so a dead Redis quietly disabled ALL rate limits with no trace.
# Warn throttled to once per window (per request would spam while down).
_FAIL_OPEN_WARNED_AT = 0.0
_FAIL_OPEN_WARN_INTERVAL_S = 300.0

# Lua: prune expired slots, then acquire one if below the limit.
# KEYS[1]=sorted-set key; ARGV: prune_cutoff, limit, now_score, token, key_ttl
_SLOT_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
  return 1
end
return 0
"""

# Lua: atomic first-use-of-day increment with expiry.
# KEYS[1]=counter key; ARGV[1]=ttl seconds (until UTC midnight)
_DAILY_INCR_LUA = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return n
"""


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    return 86400 - (now.hour * 3600 + now.minute * 60 + now.second) + 60


async def _redis_or_none():
    """Connected Redis client, or None when unavailable (fail-open signal)."""
    from app.backend.services.redis_client import get_redis, redis_ready
    if not await redis_ready():
        return None
    return await get_redis()


async def check_limits(
    *,
    user,                      # User ORM row or None (service call)
    scope: str,                # "analysis" | "research" | "hedge_fund"
    daily_limit: Optional[int],
    concurrent_limit: Optional[int],
    slot_ttl_seconds: int,
) -> None:
    """Raise HTTPException(429) when the user is over the scope's limits.

    Always allows when: Redis is unavailable (fail open), user is None
    (service/scheduler call), the user is an admin, or both limits are
    None. Any unexpected error mid-check also fails open.
    """
    if user is None or (daily_limit is None and concurrent_limit is None):
        return
    if getattr(user, "role", "member") == "admin":
        return

    try:
        r = await _redis_or_none()
        if r is None:
            global _FAIL_OPEN_WARNED_AT
            _now = time.monotonic()
            if _now - _FAIL_OPEN_WARNED_AT >= _FAIL_OPEN_WARN_INTERVAL_S:
                _FAIL_OPEN_WARNED_AT = _now
                logger.warning(
                    "rate_limiter: Redis unavailable — limits NOT enforced (fail open)")
            return  # no Redis → no limits (queue mode is dormant anyway)

        user_id = user.id

        # Daily cap first: INCR is atomic; a rejected request leaves one
        # extra count, which is harmless (the user is at their cap anyway
        # and the counter resets at UTC midnight).
        if daily_limit is not None:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            daily_key = f"{_KEY_PREFIX}:{scope}:daily:{user_id}:{day}"
            used = await r.eval(
                _DAILY_INCR_LUA, 1, daily_key,
                str(_seconds_until_utc_midnight()),
            )
            if int(used) > daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail=(f"Daily {scope} limit reached ({daily_limit}/day). "
                            f"Resets at UTC midnight."),
                    headers={"Retry-After": str(_seconds_until_utc_midnight())},
                )

        # Concurrency cap: acquire a slot in the rolling window.
        if concurrent_limit is not None:
            active_key = f"{_KEY_PREFIX}:{scope}:active:{user_id}"
            now = time.time()
            acquired = await r.eval(
                _SLOT_ACQUIRE_LUA, 1, active_key,
                str(now - slot_ttl_seconds),   # prune cutoff
                str(concurrent_limit),
                str(now),
                uuid.uuid4().hex,
                str(slot_ttl_seconds + 60),    # whole-key TTL backstop
            )
            if not int(acquired):
                raise HTTPException(
                    status_code=429,
                    detail=(f"Too many concurrent {scope} runs "
                            f"(limit {concurrent_limit}). "
                            f"Wait for one to finish and retry."),
                    headers={"Retry-After": str(slot_ttl_seconds)},
                )
    except HTTPException:
        raise
    except Exception:
        # Fail open: a broken limiter must never block pipelines.
        logger.exception("Rate limiter error — allowing request (fail open)")
