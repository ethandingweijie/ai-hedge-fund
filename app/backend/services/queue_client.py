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
- analysis_dedup:{ticker}   STRING run_id, SETNX, TTL 65 min
  Distributed replacement for the in-memory _in_flight dict: the value is
  the runner's run_id so waiters can subscribe to the same bus channel.
  (Per-ticker since M2 Track E — the investor committee and its agent-list
  dimension are decommissioned.)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.backend.services.redis_client import get_redis, redis_ready

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "analysis_dedup:"
# The dedup slot must outlive the longest job the worker may run
# (WorkerSettings.job_timeout = 3600s) — a 35-60 min run that lost its slot
# mid-flight allowed a duplicate pipeline for the same ticker to be
# enqueued (previously 1800 < 3600). The worker releases the slot in a
# finally-block on completion/failure; the TTL only has to cover runs that
# die hard (worker kill/crash) and never get the chance.
DEDUP_TTL = 3900  # 65 min — worker job_timeout (60 min) + 5 min slack
ENQUEUE_EXPIRES = 1800  # 30 min — unclaimed jobs expire out of the queue


def build_dedup_key(ticker: str) -> str:
    """Canonical dedup slot key for one analysis run.

    MUST stay identical between the web claim site (routes/analysis.py) and
    the worker release (worker.run_analysis_pipeline_task). Per-ticker since
    the investor committee was decommissioned (M2 Track E).
    """
    return ticker

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
        user_id=user_id,
        run_id=run_id,
        _job_id=f"analysis:{run_id}",
        _queue_name=QUEUE_NAME,
        _expires=ENQUEUE_EXPIRES,
    )


async def enqueue_research_job(job_id: str, kind: str, params: dict) -> None:
    """Enqueue a /research/* background job. Raises on Redis/arq failure.

    The job row already exists in the job store (created by the route before
    enqueueing); the worker task executes the module-level runner which owns
    all status transitions (pending → running → complete/failed), so the
    polling endpoints work identically in both modes.
    """
    from app.backend.worker import QUEUE_NAME

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "run_research_job_task",
        job_id=job_id,
        kind=kind,
        params=params,
        _job_id=f"research:{job_id}",
        _queue_name=QUEUE_NAME,
        _expires=ENQUEUE_EXPIRES,
    )


async def enqueue_hedge_fund_run(run_id: str, user_id: Optional[int],
                                 request_payload: dict):
    """Enqueue a hedge-fund graph run. Raises on Redis/arq failure.

    Returns the arq Job handle — after the bus emits graph_complete the web
    SSE layer awaits job.result() to fetch the parsed final payload.
    """
    from app.backend.worker import QUEUE_NAME

    pool = await get_arq_pool()
    return await pool.enqueue_job(
        "run_hedge_fund_graph_task",
        run_id=run_id,
        user_id=user_id,
        request_payload=request_payload,
        _job_id=f"hedgefund:{run_id}",
        _queue_name=QUEUE_NAME,
        _expires=ENQUEUE_EXPIRES,
    )
