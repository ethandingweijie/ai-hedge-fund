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
    finally:
        # Release the distributed dedup slot on EVERY exit path (success,
        # pipeline error, unexpected exception) — otherwise an immediate
        # re-run of the same ticker waits out the full 65-min DEDUP_TTL, and
        # a crashed run leaves its slot locked until expiry. In-process runs
        # never claim a Redis slot, so the DEL is a no-op there;
        # release_run also swallows Redis failures itself.
        try:
            from app.backend.services import queue_client
            await queue_client.release_run(
                queue_client.build_dedup_key(ticker, selected_agents))
        except Exception as exc:
            logger.warning("worker: dedup release failed: %s", exc)


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
    Progress streams over the bus; the parsed final payload (decisions /
    analyst_signals / current_prices — same shape as the in-process route's
    CompleteEvent data) is returned via arq's result store, which the web
    SSE layer fetches after the graph_complete terminal event.
    """
    from app.backend.models.schemas import HedgeFundRequest
    from app.backend.services.graph import (
        create_graph,
        parse_hedge_fund_response,
        run_graph_async,
    )
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

    # Same shape as the in-process route's CompleteEvent data. None when the
    # graph produced no messages — the web layer turns that into the same
    # "Failed to generate hedge fund decisions" error the old path emitted.
    final_data = None
    if result and result.get("messages"):
        final_data = {
            "decisions": parse_hedge_fund_response(result["messages"][-1].content),
            "analyst_signals": result.get("data", {}).get("analyst_signals", {}),
            "current_prices": result.get("data", {}).get("current_prices", {}),
        }
    return {"run_id": run_id, "user_id": user_id, "ok": bool(result),
            "final_data": final_data}


# ── Tasks 4-10: scheduled jobs (Phase 4) ─────────────────────────────────────
# The dedicated scheduler service (app/backend/scheduler_service.py) owns the
# fire TIMES and enqueues these at each slot; the heavy work runs here. Every
# cycle function keeps its own DB-timestamp idempotency check, so a double
# enqueue (overlapping deploys, accidental second replica) is a cheap no-op.

async def run_vgpm_backfill_task(ctx: dict) -> dict:
    """Daily VGPM master-universe backfill (ex-web-process as of Phase 4).

    Idempotency moved verbatim from main.py::_should_backfill_now: skip if
    master_universe was already backfilled since today's 09:00 UTC slot
    (Railway containers run UTC, matching the old in-web behaviour).
    """
    def _should_run() -> bool:
        from app.backend.services.screener_service import (
            _ensure_tables, _get_master_universe_cached_at,
        )
        try:
            _ensure_tables()
            cached_at = _get_master_universe_cached_at()
        except Exception:
            return True  # introspection failed — let the backfill itself decide
        if not cached_at:
            return True
        try:
            last = datetime.fromisoformat(cached_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            slot = datetime.now(timezone.utc).replace(
                hour=9, minute=0, second=0, microsecond=0)
            return last < slot
        except Exception:
            return True

    if not await asyncio.to_thread(_should_run):
        logger.info("vgpm backfill task: already ran since today's 09:00 UTC slot — skipping")
        return {"ran": False}

    # Phase 5 (multi-replica web): same Redis lock the admin route
    # /screener/admin/backfill-universe takes. If an admin-triggered backfill
    # is running on a web replica, skip this scheduled run — they share one
    # FMP token bucket and must not overlap. Fail-open when Redis is down
    # (the DB-timestamp gate above still provides idempotency).
    from app.backend.services.redis_locks import (
        VGPM_BACKFILL_LOCK_NAME,
        VGPM_BACKFILL_LOCK_TTL_S,
        try_lock,
        unlock,
    )

    acquired, token = await try_lock(VGPM_BACKFILL_LOCK_NAME, VGPM_BACKFILL_LOCK_TTL_S)
    if not acquired:
        logger.info("vgpm backfill task: lock held (admin backfill in progress) — skipping")
        return {"ran": False}

    def _run():
        from app.backend.services.screener_service import backfill_master_universe
        logger.info("vgpm backfill task: starting full backfill...")
        return backfill_master_universe(batch_size=50, passes=5, delay=8)

    try:
        result = await asyncio.to_thread(_run)
    finally:
        await unlock(VGPM_BACKFILL_LOCK_NAME, token)
    logger.info("vgpm backfill task: complete — %d/%d scored",
                result.get("scored", 0), result.get("total", 0))
    return {"ran": True, "scored": result.get("scored"), "total": result.get("total")}


async def run_idea_of_the_day_task(ctx: dict) -> dict:
    """Daily research-idea generation. The cycle self-gates on its 20h
    idempotency window and handles its own Slack notification."""
    from src.research_ideas.contrarian.scheduler import _generate_and_notify
    await asyncio.to_thread(_generate_and_notify)
    return {"ran": True}


async def run_iv15_sweep_task(ctx: dict) -> dict:
    """Daily IV15 fair-value sweep. Self-gates on its 20h window."""
    from src.research_ideas.alerts.iv15_scheduler import _run_sweep_cycle
    await asyncio.to_thread(_run_sweep_cycle)
    return {"ran": True}


async def run_fundflow_brief_task(ctx: dict) -> dict:
    """Weekly geographic fund-flow brief. Self-gates on its ~6-day window."""
    from src.research_ideas.fundflow.scheduler import run_weekly_cycle
    payload = await asyncio.to_thread(run_weekly_cycle)
    return {"ran": payload is not None}


async def run_hundred_q_daily_sweep_task(ctx: dict) -> dict:
    """Daily 100-Q event-trigger sweep. Self-gates on its 20h window."""
    from src.research_ideas.hundred_q.scheduler import run_daily_sweep_cycle
    fired = await asyncio.to_thread(run_daily_sweep_cycle)
    return {"ran": fired is not None}


async def run_hundred_q_weekly_batch_task(ctx: dict) -> dict:
    """Weekly 100-Q full quant batch. Self-gates on its ~6-day window."""
    from src.research_ideas.hundred_q.scheduler import run_weekly_batch_cycle
    cohort = await asyncio.to_thread(run_weekly_batch_cycle)
    return {"ran": cohort is not None}


async def run_hundred_q_backstop_task(ctx: dict) -> dict:
    """Quarterly 100-Q annual backstop. Self-gates on its ~80-day window."""
    from src.research_ideas.hundred_q.scheduler import run_backstop_cycle
    touched = await asyncio.to_thread(run_backstop_cycle)
    return {"ran": touched is not None}


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
        # Phase 4 — scheduled jobs enqueued by app/backend/scheduler_service.py
        run_vgpm_backfill_task,
        run_idea_of_the_day_task,
        run_iv15_sweep_task,
        run_fundflow_brief_task,
        run_hundred_q_daily_sweep_task,
        run_hundred_q_weekly_batch_task,
        run_hundred_q_backstop_task,
    ]
    queue_name = QUEUE_NAME
    redis_settings = build_redis_settings()
    max_jobs = 10            # plan: 10 concurrent pipelines per worker
    job_timeout = 3600       # 60 min — deep research ~12 min, full VGPM backfill
                             # can exceed 30 min; timeout is a cap, not a delay
    keep_result = 3600       # results retrievable for 1 h after completion
    retry_jobs = False       # failed pipelines are surfaced, not silently retried
    health_check_interval = 60
    on_startup = _on_startup
    on_shutdown = _on_shutdown
