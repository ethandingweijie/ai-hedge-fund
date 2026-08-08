"""
app/backend/services/queue_client.py
=====================================
Web-process helpers for queue mode (Phase 2d).

When Redis is reachable, /analysis/run hands execution to the arq worker
(app/backend/worker.py) and streams progress back over progress_bus.
When Redis is absent every helper here reports "unavailable" and the
route falls back to the in-process execution path — so production keeps
working identically until the Redis addon exists.

Redis keys:
- analysis_dedup:{ticker}::{agents}   STRING run_id, SETNX, TTL 30 min
  Distributed replacement for the in-memory _in_flight dict: the value is
  the runner's run_id so waiters can subscribe to the same bus channel.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.backend.services.redis_client import get_redis, redis_ready

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "analysis_dedup:"
DEDUP_TTL = 1800  # 30 min — matches the worker's job_timeout

_arq_pool = None


async def queue_mode_enabled() -> bool:
    """True when Redis answers — queue mode is safe to use."""
    return await redis_ready()


async def claim_run(dedup_key: str, run_id: str) -> bool:
    """SETNX the dedup slot. True → this caller is the runner."""
    r = await get_redis()
    ok = await r.set(DEDUP_PREFIX + dedup_key, run_id, nx=True, ex=DEDUP_TTL)
    return bool(ok)


async def get_runner_run_id(dedup_key: str) -> Optional[str]:
    """The run_id currently holding the dedup slot, or None."""
    r = await get_redis()
    return await r.get(DEDUP_PREFIX + dedup_key)


async def release_run(dedup_key: str) -> None:
    """Drop the dedup slot (used when enqueue fails after claiming)."""
    try:
        r = await get_redis()
        await r.delete(DEDUP_PREFIX + dedup_key)
    except Exception as exc:
        logger.warning("queue_client: dedup release failed: %s", exc)


async def get_arq_pool():
    """Shared arq Redis pool for enqueueing (lazy-created)."""
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool

        from app.backend.worker import build_redis_settings

        _arq_pool = await create_pool(build_redis_settings())
    return _arq_pool


async def enqueue_analysis(
    ticker: str,
    model_name: str,
    api_keys: dict,
    selected_agents: Optional[list[str]],
    user_id: Optional[int],
    run_id: str,
) -> None:
    """Enqueue the pipeline task. Raises on Redis/arq failure."""
    from app.backend.worker import QUEUE_NAME

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "run_analysis_pipeline_task",
        ticker=ticker,
        model_name=model_name,
        api_keys=api_keys,
        selected_agents=selected_agents,
        user_id=user_id,
        run_id=run_id,
        _job_id=f"analysis:{run_id}",
        _queue_name=QUEUE_NAME,
        _expires=DEDUP_TTL,
    )
