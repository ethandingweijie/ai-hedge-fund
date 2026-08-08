"""
app/backend/worker.py
=====================
arq worker process — runs long jobs OUTSIDE the web process.

Start (Railway worker service, Phase 2g):
    arq app.backend.worker.WorkerSettings

Phase 2c of the scalability plan. This module is purely additive: nothing
enqueues these tasks until Phases 2d-2f switch the routes over, so deploying
it changes no production behaviour. When REDIS_URL is unset the web app keeps
running everything in-process exactly as today.

Tasks
-----
run_analysis_pipeline_task(ctx, ticker, model_name, api_keys,
                           selected_agents, user_id, run_id)
    Full 10-phase analysis pipeline. run_id is minted by the WEB process at
    enqueue time so SSE clients can subscribe to progress:{run_id} before the
    job even starts.

run_research_job_task(ctx, job_id, kind, params)
    /research/* background jobs. The complacency kinds are module-level sync
    runners in routes/research.py that manage their own job-store state; the
    worker just executes them on a thread.

run_hedge_fund_graph_task(ctx, run_id, user_id, request_payload)
    Agent-graph run reconstructed from the serialised HedgeFundRequest.

Progress crosses the process boundary via app.backend.services.progress_bus
(Redis pub/sub + replay buffer); web SSE endpoints subscribe to the channel
for their run_id. Terminal events always carry "completed": True so
subscribers know when to stop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from arq.connections import RedisSettings

from app.backend.services import progress_bus

logger = logging.getLogger(__name__)

QUEUE_NAME = "arq:queue"


# ── Infra ──────────────────────────────────────────────────────────────────────

def build_redis_settings() -> RedisSettings:
    """arq connection settings from REDIS_URL (localhost fallback for dev)."""
    return RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Task 1: analysis pipeline ─────────────────────────────────────────────────

async def run_analysis_pipeline_task(
    ctx: dict,
    ticker: str,
    model_name: str,
    api_keys: dict,
    selected_agents: Optional[list[str]] = None,
    user_id: Optional[int] = None,
    run_id: Optional[str] = None,
) -> dict:
    """Run the 10-phase pipeline, streaming progress over the bus.

    on_phase is a SYNC callback invoked from inside the pipeline, so events
    are queued and drained by a single forwarder task — this preserves event
    ordering (sequential create_task calls could interleave their awaits).
    """
    from app.backend.services import analysis_service

    if not run_id:
        run_id = str(uuid.uuid4())
    ticker_key = ticker.upper()
    loop = asyncio.get_running_loop()

    q: asyncio.Queue = asyncio.Queue()

    async def _forwarder() -> None:
        while True:
            item = await q.get()
            if item is None:
                return
            full_event, phase_event, tk = item
            try:
                await progress_bus.publish_event(run_id, full_event)
                await progress_bus.set_phase_event(
                    tk, phase_event["phase"], phase_event
                )
            except Exception as exc:
                logger.warning("worker: progress publish failed: %s", exc)

    fwd = loop.create_task(_forwarder())

    def on_phase(
        phase: str,
        status: str,
        summary: str,
        reasoning: str = "",
        ev_ticker: Optional[str] = None,
        timestamp: Optional[str] = None,
        partial_data: Optional[dict] = None,
    ) -> None:
        event: dict[str, Any] = {"phase": phase, "status": status, "summary": summary}
        if reasoning:
            event["reasoning"] = reasoning
        if ev_ticker:
            event["ticker"] = ev_ticker
        if timestamp:
            event["timestamp"] = timestamp
        if partial_data:
            event["partial_data"] = partial_data
        phase_event = {
            "phase": phase, "status": status, "summary": summary,
            "timestamp": timestamp or "",
        }
        q.put_nowait((event, phase_event, (ev_ticker or ticker).upper()))

    try:
        rid, _result = await analysis_service.run_analysis_pipeline(
            ticker=ticker,
            model_name=model_name,
            api_keys=api_keys,
            on_phase=on_phase,
            selected_agents=selected_agents,
            user_id=user_id,
            run_id=run_id,
        )
    except Exception as exc:
        q.put_nowait(None)
        await fwd
        err = {
            "phase": "pipeline_error",
            "status": "error",
            "summary": f"{type(exc).__name__}: {exc}",
            "run_id": run_id,
            "completed": True,
        }
        await progress_bus.publish_event(run_id, err)
        await progress_bus.set_phase_event(ticker_key, "pipeline_error", err)
        raise
    finally:
        # Ensure the forwarder never outlives the task even on odd exits.
        if not fwd.done():
            q.put_nowait(None)
            await fwd

    marker = {
        "phase": "pipeline_complete",
        "status": "done",
        "summary": f"Run completed: {rid}",
        "timestamp": _utcnow_iso(),
        "run_id": rid,
        "completed": True,
    }
    await progress_bus.publish_event(run_id, marker)
    await progress_bus.set_phase_event(ticker_key, "pipeline_complete", marker)

    # Mirror the web route: keep the ticker's live-phase state around for
    # ~2 min so reconnecting/polling clients can observe completion.
    async def _cleanup() -> None:
        await asyncio.sleep(120)
        await progress_bus.clear_ticker(ticker)

    loop.create_task(_cleanup())
    return {"run_id": rid, "ticker": ticker, "ok": True}


# ── Task 2: research jobs ──────────────────────────────────────────────────────

#: Kinds accepted by run_research_job_task. The complacency kinds map to
#: module-level sync runners in routes/research.py (they manage their own
#: job-store state); hundred_q_refresh is handled inline below.
RESEARCH_KINDS = {
    "idea_of_the_day_gen",
    "hk50_qual",
    "hk50_qual_ticker",
    "refresh",
    "score_adhoc",
    "hundred_q_refresh",
}


async def run_research_job_task(
    ctx: dict,
    job_id: str,
    kind: str,
    params: Optional[dict] = None,
) -> dict:
    """Execute one /research/* background job by kind.

    `params` carries the route's query params verbatim (e.g. {"mode": ...},
    {"top_n": 20, "force_refresh": false}). Job state (pending/running/
    complete/failed) is maintained by the runners themselves via the job
    store, so web polling endpoints work unchanged.
    """
    params = params or {}

    if kind == "hundred_q_refresh":
        await _run_hundred_q_refresh(job_id)
        return {"job_id": job_id, "kind": kind, "ok": True}

    # Lazy import: routes/research.py pulls in the whole research stack.
    from app.backend.routes import research as R

    runners = {
        "idea_of_the_day_gen": lambda: R._execute_idea_gen_job(
            job_id, params.get("mode")),
        "hk50_qual": lambda: R._execute_hk50_qual_job(
            job_id, int(params.get("top_n", 20)), bool(params.get("force_refresh", False))),
        "hk50_qual_ticker": lambda: R._execute_hk50_ticker_qual_job(
            job_id, params["needle"], bool(params.get("force_refresh", False))),
        "refresh": lambda: R._execute_refresh_job(
            job_id, int(params.get("max_workers", 3))),
        "score_adhoc": lambda: R._execute_score_job(
            job_id, params["ticker"], bool(params.get("force_qual", False))),
    }
    fn = runners.get(kind)
    if fn is None:
        raise ValueError(f"unknown research job kind: {kind!r}")
    await asyncio.to_thread(fn)
    return {"job_id": job_id, "kind": kind, "ok": True}


async def _run_hundred_q_refresh(job_id: str) -> None:
    """Same logic as the route's inline closure, worker-side."""
    from app.backend.services import complacency_job_store as job_store

    job_store.update_progress(job_id, "running", "scoring pilot universe...")
    try:
        from src.research_ideas.hundred_q.runner import run_full_quant_batch

        cohort = await asyncio.to_thread(run_full_quant_batch, 6, True, None, "adhoc")
        job_store.complete_job(job_id, {
            "run_id": cohort.run_id,
            "ticker_count": cohort.ticker_count,
            "tier_counts": cohort.tier_counts,
            "failed_tickers": cohort.failed_tickers,
        })
    except Exception as exc:
        logger.exception("hundred_q refresh job %s failed: %s", job_id, exc)
        job_store.fail_job(job_id, str(exc))


# ── Task 3: hedge-fund graph run ───────────────────────────────────────────────

async def run_hedge_fund_graph_task(
    ctx: dict,
    run_id: str,
    user_id: Optional[int],
    request_payload: dict,
) -> dict:
    """Execute an agent graph from a serialised HedgeFundRequest.

    The web process hydrates API keys into the payload BEFORE enqueueing
    (the worker has no request-scoped DB session to resolve them lazily).
    The raw graph result is returned via arq's result store; the web SSE
    layer (Phase 2f) parses decisions from it exactly as the old in-process
    path did.
    """
    from app.backend.models.schemas import HedgeFundRequest
    from app.backend.services.graph import create_graph, run_graph_async
    from app.backend.services.portfolio import create_portfolio
    from src.utils.progress import progress

    req = HedgeFundRequest(**request_payload)
    graph = create_graph(
        graph_nodes=req.graph_nodes,
        graph_edges=req.graph_edges,
    ).compile()
    portfolio = create_portfolio(
        req.initial_cash, req.margin_requirement, req.tickers,
        req.portfolio_positions,
    )

    model_provider = req.model_provider
    if hasattr(model_provider, "value"):
        model_provider = model_provider.value

    # Tag every progress event emitted on this task's context with run_id so
    # our handler can filter out other concurrent runs (same mechanism the
    # fixed web route uses).
    progress.set_run_id(run_id)
    loop = asyncio.get_running_loop()

    def _handler(agent_name, tk, status, analysis, timestamp,
                 partial_data=None, event_run_id=None):
        if event_run_id is not None and event_run_id != run_id:
            return
        event: dict[str, Any] = {
            "phase": "agent_progress",
            "agent": agent_name,
            "ticker": tk,
            "status": status,
            "timestamp": timestamp,
            "analysis": analysis,
        }
        if partial_data:
            event["partial_data"] = partial_data
        t = loop.create_task(progress_bus.publish_event(run_id, event))
        t.add_done_callback(_log_publish_outcome)

    progress.register_handler(_handler)
    try:
        result = await run_graph_async(
            graph=graph,
            portfolio=portfolio,
            tickers=req.tickers,
            start_date=req.start_date,
            end_date=req.end_date,
            model_name=req.model_name,
            model_provider=model_provider,
            request=req,
        )
    except Exception as exc:
        err = {
            "phase": "graph_error",
            "status": "error",
            "summary": f"{type(exc).__name__}: {exc}",
            "run_id": run_id,
            "completed": True,
        }
        await progress_bus.publish_event(run_id, err)
        raise
    finally:
        progress.unregister_handler(_handler)

    await progress_bus.publish_event(run_id, {
        "phase": "graph_complete",
        "status": "done",
        "summary": f"Graph run completed: {run_id}",
        "timestamp": _utcnow_iso(),
        "run_id": run_id,
        "completed": True,
    })
    return {"run_id": run_id, "user_id": user_id, "ok": bool(result)}


def _log_publish_outcome(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.warning("worker: progress publish failed: %s", task.exception())


# ── WorkerSettings — arq entry point ──────────────────────────────────────────

async def _on_startup(ctx: dict) -> None:
    logger.info(
        "arq worker started: queue=%s max_jobs=%d job_timeout=%ds",
        QUEUE_NAME, WorkerSettings.max_jobs, WorkerSettings.job_timeout,
    )


async def _on_shutdown(ctx: dict) -> None:
    from app.backend.services.redis_client import close_redis

    await close_redis()
    logger.info("arq worker stopped")


class WorkerSettings:
    """arq settings — run with: arq app.backend.worker.WorkerSettings"""

    functions = [
        run_analysis_pipeline_task,
        run_research_job_task,
        run_hedge_fund_graph_task,
    ]
    queue_name = QUEUE_NAME
    redis_settings = build_redis_settings()
    max_jobs = 10            # plan: 10 concurrent pipelines per worker
    job_timeout = 1800       # 30 min — deep-research runs take 10-12 min
    keep_result = 3600       # results retrievable for 1 h after completion
    retry_jobs = False       # failed pipelines are surfaced, not silently retried
    health_check_interval = 60
    on_startup = _on_startup
    on_shutdown = _on_shutdown
