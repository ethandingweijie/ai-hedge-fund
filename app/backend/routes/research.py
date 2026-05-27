"""
app/backend/routes/research.py
================================
Research-Ideas endpoints.

  GET  /research/ideas                         -> list of ideas (SW46 + Complacency)

SW46 (Cassandra Unchained / Scion software-46 valuation):
  GET  /research/ideas/sw46                    -> latest cohort snapshot
  GET  /research/ideas/sw46/{ticker}           -> per-ticker detail
  POST /research/ideas/sw46/refresh            -> run cohort fresh & persist
  GET  /research/ideas/sw46/runs               -> historical run list

Complacency (Ackman-style 4-pillar equity screener):
  GET  /research/ideas/complacency             -> latest cohort snapshot
  GET  /research/ideas/complacency/{ticker}    -> per-ticker detail
  POST /research/ideas/complacency/refresh     -> run cohort fresh & persist
  GET  /research/ideas/complacency/runs        -> historical run list
"""
from __future__ import annotations

import asyncio
import logging
import traceback

from fastapi import APIRouter, HTTPException

from app.backend.services import sw46_storage, complacency_storage
from app.backend.services import complacency_job_store as job_store
from app.backend.services import contrarian_storage
from src.research_ideas.sw46.runner import run_sw46
from src.research_ideas.sw46.universe import list_tickers as sw46_list_tickers
from src.research_ideas.complacency.runner import run_complacency, score_one_ticker
from src.research_ideas.complacency.universe import list_tickers as complacency_list_tickers
from src.research_ideas.contrarian.idea_generator import generate_idea_of_the_day
from src.research_ideas.contrarian.chat_agent import chat_turn as contrarian_chat_turn


logger = logging.getLogger(__name__)

router = APIRouter()


# â”€â”€â”€ Background-task strong-reference set â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Python's asyncio docs (3.11+) explicitly warn:
#   "The event loop only keeps WEAK references to tasks. A task that isn't
#    referenced elsewhere may get garbage-collected at any time, even
#    before it's done."
# So every asyncio.create_task() in this module MUST store the task in this
# set; otherwise the long-running Complacency refresh / force-qual / IoTD
# generation jobs get silently killed mid-flight, leaving the job_store
# row stuck in 'pending' status forever. The done-callback removes the
# task from the set once it completes (success or failure).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro):
    """Wrap asyncio.create_task with strong-reference + done-cleanup."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    def _on_done(t: asyncio.Task):
        try:
            exc = t.exception()
            if exc is not None:
                logger.exception(
                    "Background task %s crashed: %s",
                    t.get_name(), exc,
                )
        except asyncio.CancelledError:
            logger.warning("Background task %s was cancelled", t.get_name())
        except Exception:
            pass

    task.add_done_callback(_on_done)
    return task


# ─── Heartbeat — pulses progress_msg every 30s while a job is running ────
# Without this, the front-end toast text is frozen at the initial "scoring
# NVDA (force_qual)" message for the entire 5-10 min job, and the user
# can't tell if the backend is alive. The heartbeat thread writes
# "scoring NVDA (force_qual) · 3m 15s elapsed" every 30s so the toast
# updates live.
import threading
import time as _time


def _with_heartbeat(job_id: str, base_msg: str, work_fn):
    """
    Run `work_fn()` synchronously while a daemon thread updates the job's
    progress_msg with elapsed time every 30s. Returns the work_fn return
    value. Exceptions propagate.
    """
    stop_flag = threading.Event()
    start_ts = _time.time()

    def _pulse():
        while not stop_flag.wait(30):
            elapsed_s = int(_time.time() - start_ts)
            mm, ss = divmod(elapsed_s, 60)
            try:
                job_store.update_progress(
                    job_id, "running",
                    f"{base_msg} · {mm}m {ss}s elapsed",
                )
            except Exception:
                pass

    hb = threading.Thread(target=_pulse, name=f"hb-{job_id[:8]}", daemon=True)
    hb.start()
    try:
        return work_fn()
    finally:
        stop_flag.set()
        # Don't join hb; daemon will exit naturally. Joining adds latency
        # on the happy-path completion.


# â”€â”€â”€ Idea catalogue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/ideas")
async def list_research_ideas():
    """Catalogue of research ideas. v1: SW46 + Complacency."""
    latest_sw46 = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
    latest_compl = await asyncio.to_thread(complacency_storage.get_latest_complacency_run)

    sw46_meta = {
        "id": "sw46",
        "name": "SW46 â€” Software Cohort Valuation",
        "blurb": (
            "Cassandra Unchained / Scion methodology: Tragic Algebra owner "
            "earnings, fully-adjusted ROIC, AI Competitive Threat tiering, "
            "and IV15 (15-year / 15% required-return hybrid intrinsic value)."
        ),
        "ticker_count": len(sw46_list_tickers()),
        "last_run_at": latest_sw46.get("created_at") if latest_sw46 else None,
        "last_pooled_delta_e": latest_sw46.get("cohort_pooled_delta_e") if latest_sw46 else None,
        "headline_metric_label": "Pooled Î”E",
    }

    complacency_meta = {
        "id": "complacency",
        "name": "Complacency Detector â€” Ackman 4-Pillar",
        "blurb": (
            "Bill Ackman-style equity screener: detects when price has "
            "decoupled from fundamentals across Valuation, Behavioral, "
            "Technical, and Quality pillars. Composite â‰¥6/8 + all pillars â‰¥1 "
            "flags structural complacency."
        ),
        "ticker_count": len(complacency_list_tickers()),
        "last_run_at": latest_compl.get("created_at") if latest_compl else None,
        "last_gate_passers": latest_compl.get("gate_passers") if latest_compl else None,
        "headline_metric_label": "Gate passers",
    }

    # "Research Idea of the Day" â€” AI-generated contrarian deep-value
    # hypothesis. v1 surfaces just the meta + the latest idea's headline.
    latest_iotd = await asyncio.to_thread(contrarian_storage.get_latest_idea)
    shortlist_count = len(await asyncio.to_thread(contrarian_storage.list_shortlist, 100))
    iotd_meta = {
        "id": "idea_of_the_day",
        "name": "Research Idea of the Day",
        "blurb": (
            "AI-generated deep-value, asymmetric, contrarian hypothesis "
            "with live web-search backing. Discuss with the agent to "
            "stress-test, then add to your shortlist if it earns conviction."
        ),
        "ticker_count": shortlist_count,         # repurposed: # shortlisted
        "headline_metric_label": "Shortlisted",
        "is_ai_generated": True,
        "last_run_at": latest_iotd.get("generated_at") if latest_iotd else None,
        "latest_idea_ticker": latest_iotd.get("ticker") if latest_iotd else None,
        "latest_idea_id": latest_iotd.get("idea_id") if latest_iotd else None,
        "latest_idea_hypothesis": latest_iotd.get("hypothesis") if latest_iotd else None,
        "latest_idea_conviction": latest_iotd.get("conviction_score") if latest_iotd else None,
    }

    return {"ideas": [iotd_meta, sw46_meta, complacency_meta]}


# â”€â”€â”€ Research Idea of the Day (contrarian deep-value) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/ideas/idea-of-the-day")
async def get_idea_of_the_day():
    """Latest non-deleted idea, or empty payload if none."""
    try:
        idea = await asyncio.to_thread(contrarian_storage.get_latest_idea)
        if not idea:
            return {"idea": None}
        shortlisted = await asyncio.to_thread(contrarian_storage.is_shortlisted, idea["idea_id"])
        idea["_shortlisted"] = shortlisted
        return {"idea": idea}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_idea_of_the_day failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/idea-of-the-day/list")
async def list_recent_ideas(limit: int = 10):
    """Recent generated ideas (non-deleted), most-recent first."""
    try:
        ideas = await asyncio.to_thread(contrarian_storage.list_ideas, limit, False)
        return {"ideas": ideas}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_recent_ideas failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


def _execute_idea_gen_job(job_id: str) -> None:
    """Background worker for idea generation (~30-90s via Qwen web search)."""
    base_msg = "Qwen searching the web for contrarian opportunities"
    job_store.update_progress(job_id, "running", base_msg)
    try:
        # Exclude tickers from the last 7 generated ideas to avoid same-day duplicates
        recent = contrarian_storage.list_ideas(limit=7, include_deleted=True)
        exclude = [r.get("ticker") for r in recent if r.get("ticker")]

        idea = _with_heartbeat(
            job_id, base_msg,
            lambda: generate_idea_of_the_day(exclude_tickers=exclude),
        )
        if not idea:
            job_store.fail_job(job_id, "generation returned empty (Qwen failed or invalid JSON)")
            return
        contrarian_storage.save_idea(idea)
        job_store.complete_job(job_id, idea)
    except Exception as exc:
        logger.exception("idea-of-the-day generation job %s failed: %s", job_id, exc)
        job_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/ideas/idea-of-the-day/generate")
async def trigger_idea_generation():
    """Kick off a new idea-of-the-day generation as a background job."""
    in_flight = job_store.find_in_flight_job("idea_of_the_day_gen")
    if in_flight:
        return {**in_flight, "deduped": True}

    job_id = job_store.create_job("idea_of_the_day_gen")

    async def _run():
        await asyncio.to_thread(_execute_idea_gen_job, job_id)

    _spawn_background(_run())
    return {"job_id": job_id, "status": "pending", "started_at": None, "deduped": False}


@router.delete("/ideas/idea-of-the-day/{idea_id}")
async def delete_idea(idea_id: str):
    """Soft-delete (preserves chat history)."""
    try:
        ok = await asyncio.to_thread(contrarian_storage.soft_delete_idea, idea_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Idea {idea_id} not found or already deleted")
        return {"deleted": True, "idea_id": idea_id}
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("delete_idea(%s) failed: %s", idea_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/idea-of-the-day/{idea_id}")
async def get_idea(idea_id: str):
    try:
        idea = await asyncio.to_thread(contrarian_storage.get_idea, idea_id)
        if not idea:
            raise HTTPException(status_code=404, detail=f"Idea {idea_id} not found")
        idea["_shortlisted"] = await asyncio.to_thread(
            contrarian_storage.is_shortlisted, idea_id,
        )
        return idea
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_idea(%s) failed: %s", idea_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/diag/llm-health")
async def diag_llm_health():
    """
    Diagnostic: which LLM env vars are set on this deployment, and can we
    actually reach Qwen? Returns presence-only for env vars (no key values
    are exposed). Helps debug "DEEP_RESEARCH_API_KEY missing or Qwen failed"
    surfacing in chat / qualitative scoring.

    Run via:  curl https://<your-railway-app>/research/diag/llm-health
    """
    import os as _os

    keys_to_check = [
        "DEEP_RESEARCH_API_KEY",
        "DEEP_RESEARCH_SEARCH_BASE_URL",
        "DEEP_RESEARCH_BASE_URL",
        "DEEP_RESEARCH_MODEL",
        "QWEN_MODEL",
        "COMPLACENCY_QUAL_MODEL",
        "CONTRARIAN_CHAT_MODEL",
        "ALIBABA_API_KEY",
        "DASHSCOPE_API_KEY",
        "TAVILY_API_KEY",
        "FMP_API_KEY",
        "ANTHROPIC_API_KEY",
    ]
    env_state = {
        k: {
            "set": bool(_os.environ.get(k)),
            "length": len(_os.environ.get(k, "")) if _os.environ.get(k) else 0,
        }
        for k in keys_to_check
    }

    # Live Qwen connectivity test — single tiny call so it costs ~$0.0001
    qwen_test: dict = {"ok": False, "error": None, "model": None, "base_url": None, "elapsed_ms": None}
    try:
        from openai import OpenAI
        import time as _time
        api_key = _os.environ.get("DEEP_RESEARCH_API_KEY")
        if not api_key:
            qwen_test["error"] = "DEEP_RESEARCH_API_KEY not set"
        else:
            base_url = _os.environ.get(
                "DEEP_RESEARCH_SEARCH_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            )
            model = _os.environ.get("CONTRARIAN_CHAT_MODEL", "qwen3.6-plus")
            qwen_test["base_url"] = base_url
            qwen_test["model"] = model

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
            t0 = _time.time()
            resp = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                    max_tokens=10,
                    temperature=0.0,
                )
            )
            qwen_test["elapsed_ms"] = int((_time.time() - t0) * 1000)
            content = (resp.choices[0].message.content or "").strip()[:30]
            qwen_test["ok"] = True
            qwen_test["sample_response"] = content
    except ImportError as exc:
        qwen_test["error"] = f"openai package not installed: {exc}"
    except Exception as exc:
        qwen_test["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    # Throttle stats — visibility into rate-limit coordination
    try:
        from src.research_ideas.complacency import qwen_throttle
        throttle_stats = qwen_throttle.stats()
    except Exception as exc:
        throttle_stats = {"error": str(exc)}

    return {
        "env": env_state,
        "qwen_test": qwen_test,
        "qwen_throttle": throttle_stats,
        "summary": (
            "ALL OK" if (env_state["DEEP_RESEARCH_API_KEY"]["set"] and qwen_test["ok"])
            else "FAILED — see env_state['DEEP_RESEARCH_API_KEY']['set'] and qwen_test['error']"
        ),
    }


@router.get("/ideas/idea-of-the-day/{idea_id}/chat")
async def get_chat(idea_id: str):
    try:
        messages = await asyncio.to_thread(contrarian_storage.list_chat_messages, idea_id)
        return {"idea_id": idea_id, "messages": messages}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_chat(%s) failed: %s", idea_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.post("/ideas/idea-of-the-day/{idea_id}/chat")
async def post_chat(idea_id: str, body: dict):
    """
    Send a user message. Body: {"content": "..."}. Returns the appended
    user message + assistant response (synchronous; chat is fast enough).
    """
    content = (body or {}).get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="empty message")

    idea = await asyncio.to_thread(contrarian_storage.get_idea, idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail=f"Idea {idea_id} not found")

    history = await asyncio.to_thread(contrarian_storage.list_chat_messages, idea_id)
    user_msg = await asyncio.to_thread(
        contrarian_storage.append_chat_message, idea_id, "user", content, None,
    )
    try:
        response = await asyncio.to_thread(contrarian_chat_turn, idea, history, content)
    except Exception as exc:
        logger.exception("chat_turn failed for %s: %s", idea_id, exc)
        response = None

    # chat_turn now embeds the ACTUAL failure reason in response["content"]
    # (e.g. "missing_env_var:DEEP_RESEARCH_API_KEY" or a specific Qwen API
    # exception) rather than returning None, so the user sees real diagnostic
    # info instead of the generic "DEEP_RESEARCH_API_KEY missing or Qwen failed".
    if response is None:
        assistant_msg = await asyncio.to_thread(
            contrarian_storage.append_chat_message,
            idea_id, "assistant",
            "(agent unavailable - openai package not installed on server; "
            "see GET /research/diag/llm-health for env diagnostics)",
            None,
        )
    else:
        assistant_msg = await asyncio.to_thread(
            contrarian_storage.append_chat_message,
            idea_id, "assistant",
            response.get("content", "(agent unavailable - empty response)"),
            response.get("cost_usd"),
        )
    return {"user_message": user_msg, "assistant_message": assistant_msg}


@router.post("/ideas/idea-of-the-day/{idea_id}/shortlist")
async def add_to_shortlist(idea_id: str, body: dict | None = None):
    note = ((body or {}).get("user_note") or None)
    try:
        entry = await asyncio.to_thread(contrarian_storage.add_to_shortlist, idea_id, note)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Idea {idea_id} not found")
        return entry
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("add_to_shortlist(%s) failed: %s", idea_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/idea-of-the-day/shortlist/all")
async def list_shortlist(limit: int = 50):
    try:
        entries = await asyncio.to_thread(contrarian_storage.list_shortlist, limit)
        return {"shortlist": entries}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_shortlist failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.delete("/ideas/idea-of-the-day/shortlist/{idea_id}")
async def remove_from_shortlist(idea_id: str):
    try:
        ok = await asyncio.to_thread(contrarian_storage.remove_from_shortlist, idea_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Idea {idea_id} not in shortlist")
        return {"removed": True, "idea_id": idea_id}
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("remove_from_shortlist(%s) failed: %s", idea_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# â”€â”€â”€ SW46 endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/ideas/sw46")
async def get_sw46_cohort():
    """Most-recent persisted SW46 cohort snapshot."""
    try:
        latest = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
        if not latest:
            # Empty shape (UI shows "No runs yet â€” click Refresh").
            return {
                "run_id": None,
                "created_at": None,
                "cohort_pooled_delta_e": None,
                "ticker_count": 0,
                "failed_tickers": [],
                "results": [],
            }
        return latest
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_sw46_cohort failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/sw46/runs")
async def list_sw46_runs(limit: int = 20):
    """Historical SW46 cohort runs (header-only â€” no results JSON)."""
    try:
        runs = await asyncio.to_thread(sw46_storage.list_sw46_runs, limit)
        return {"runs": runs}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_sw46_runs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/sw46/{ticker}")
async def get_sw46_ticker(ticker: str):
    """Per-ticker detail â€” pulls from the latest cohort run."""
    ticker = ticker.upper()
    try:
        latest = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
        if not latest:
            raise HTTPException(status_code=404, detail="No SW46 cohort run yet â€” refresh first")
        for r in latest.get("results", []):
            if r.get("ticker") == ticker:
                return r
        raise HTTPException(status_code=404, detail=f"{ticker} not in SW46 universe")
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_sw46_ticker(%s) failed: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.post("/ideas/sw46/refresh")
async def refresh_sw46(history_years: int = 7, max_workers: int = 6):
    """
    Trigger a fresh cohort run. Synchronous (returns once persisted) â€”
    full run takes ~2-3 min for 46 tickers on FMP free tier due to per-year
    avg-price calls. Frontend should display a spinner.
    """
    try:
        cohort = await asyncio.to_thread(
            run_sw46,
            history_years=history_years,
            max_workers=max_workers,
            save=True,
        )
        return {
            "run_id": cohort.run_id,
            "created_at": cohort.created_at,
            "cohort_pooled_delta_e": cohort.pooled_delta_e,
            "ticker_count": cohort.ticker_count,
            "failed_tickers": cohort.failed_tickers,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("refresh_sw46 failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# â”€â”€â”€ Complacency endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/ideas/complacency")
async def get_complacency_cohort():
    try:
        latest = await asyncio.to_thread(complacency_storage.get_latest_complacency_run)
        if not latest:
            return {
                "run_id": None,
                "created_at": None,
                "universe": "complacency-default",
                "ticker_count": 0,
                "gate_passers": 0,
                "failed_tickers": [],
                "results": [],
            }
        return latest
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_complacency_cohort failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/complacency/runs")
async def list_complacency_runs(limit: int = 20):
    try:
        runs = await asyncio.to_thread(complacency_storage.list_complacency_runs, limit)
        return {"runs": runs}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_complacency_runs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/complacency/{ticker}")
async def get_complacency_ticker(ticker: str):
    ticker = ticker.upper()
    try:
        latest = await asyncio.to_thread(complacency_storage.get_latest_complacency_run)
        if not latest:
            raise HTTPException(status_code=404, detail="No Complacency cohort run yet â€” refresh first")
        for r in latest.get("results", []):
            if r.get("ticker") == ticker:
                return r
        raise HTTPException(status_code=404, detail=f"{ticker} not in Complacency universe")
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_complacency_ticker(%s) failed: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# â”€â”€â”€ Long-running ops use async-job pattern â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Cohort refresh + force-qual re-scoring can take 5-10 min when deep-
# research escalates for multiple indicators. Synchronous HTTP fails on
# iOS Safari (kills fetches on backgrounding / cellular flakes). So we
# return a job_id immediately and let the frontend poll GET /jobs/{id}.


def _execute_refresh_job(job_id: str, max_workers: int) -> None:
    """Backend worker for the refresh job (runs in asyncio.to_thread)."""
    base_msg = f"running cohort ({max_workers} workers)"
    job_store.update_progress(job_id, "running", base_msg)
    try:
        cohort = _with_heartbeat(
            job_id, base_msg,
            lambda: run_complacency(max_workers=max_workers, save=True),
        )
        result = {
            "run_id": cohort.run_id,
            "created_at": cohort.created_at,
            "universe": cohort.universe,
            "ticker_count": cohort.ticker_count,
            "gate_passers": cohort.gate_passers,
            "failed_tickers": cohort.failed_tickers,
        }
        job_store.complete_job(job_id, result)
        logger.info("Complacency refresh job %s completed", job_id)
    except Exception as exc:
        logger.exception("Complacency refresh job %s failed: %s", job_id, exc)
        job_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/ideas/complacency/refresh")
async def refresh_complacency(max_workers: int = 4):
    """
    Kicks off a full cohort refresh as a BACKGROUND task. Returns immediately
    with {job_id, status: 'pending'}. Frontend polls
    GET /research/ideas/complacency/jobs/{job_id} for completion.

    If a refresh is already in flight, returns its existing job_id (dedupes).
    """
    in_flight = job_store.find_in_flight_job("refresh")
    if in_flight:
        return {
            "job_id":    in_flight["job_id"],
            "status":    in_flight["status"],
            "started_at": in_flight["started_at"],
            "deduped":   True,
        }

    job_id = job_store.create_job("refresh")

    async def _run():
        await asyncio.to_thread(_execute_refresh_job, job_id, max_workers)

    _spawn_background(_run())
    return {
        "job_id":    job_id,
        "status":    "pending",
        "started_at": None,
        "deduped":   False,
    }


def _execute_score_job(job_id: str, ticker: str, force_qual: bool) -> None:
    """Backend worker for ad-hoc / force-qual scoring (runs in asyncio.to_thread)."""
    base_msg = f"scoring {ticker}{' (force_qual)' if force_qual else ''}"
    job_store.update_progress(job_id, "running", base_msg)
    try:
        result = _with_heartbeat(
            job_id, base_msg,
            lambda: score_one_ticker(ticker, None, None, None, force_qual),
        )
        if isinstance(result, dict) and result.get("reason"):
            job_store.fail_job(job_id, f"{ticker}: {result.get('reason')}")
            return
        payload = result.model_dump() if hasattr(result, "model_dump") else result

        persisted = False
        if force_qual:
            try:
                persisted = complacency_storage.update_ticker_in_latest_cohort(payload)
            except Exception as exc:
                logger.warning("Cohort patch for %s after force_qual failed: %s", ticker, exc)
        if isinstance(payload, dict):
            payload["_persisted_to_cohort"] = persisted
        job_store.complete_job(job_id, payload)
    except Exception as exc:
        logger.exception("score_one_ticker job %s for %s failed: %s", job_id, ticker, exc)
        job_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/ideas/complacency/score/{ticker}")
async def score_complacency_adhoc(ticker: str, force_qual: bool = False):
    """
    Kicks off ad-hoc scoring (with optional force_qual) as a BACKGROUND task.
    Returns {job_id, status: 'pending'} immediately. Frontend polls
    GET /research/ideas/complacency/jobs/{job_id} for completion.

    Used for two flows:
      1. Brand-new ticker not in the curated universe â€” full fresh score.
      2. Existing-cohort ticker with force_qual=true â€” runs qualitative
         regardless of verdict; patched back into cohort storage.

    If a job is already in flight for this (ticker, force_qual=any), returns
    its existing job_id (dedupes).
    """
    ticker = ticker.strip().upper()
    if not ticker or not ticker.isalnum() or len(ticker) > 6:
        raise HTTPException(status_code=400, detail=f"Invalid ticker: {ticker!r}")

    in_flight = job_store.find_in_flight_job("score_adhoc", ticker=ticker)
    if in_flight:
        return {
            "job_id":    in_flight["job_id"],
            "status":    in_flight["status"],
            "started_at": in_flight["started_at"],
            "deduped":   True,
        }

    job_id = job_store.create_job("score_adhoc", ticker=ticker)

    async def _run():
        await asyncio.to_thread(_execute_score_job, job_id, ticker, force_qual)

    _spawn_background(_run())
    return {
        "job_id":    job_id,
        "status":    "pending",
        "started_at": None,
        "deduped":   False,
    }


@router.get("/ideas/complacency/jobs/{job_id}")
async def get_complacency_job(job_id: str):
    """Return current state of a complacency background job."""
    try:
        job = await asyncio.to_thread(job_store.get_job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_complacency_job(%s) failed: %s", job_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/complacency/jobs")
async def list_complacency_jobs(limit: int = 20):
    """List recent complacency jobs across all kinds."""
    try:
        jobs = await asyncio.to_thread(job_store.list_recent_jobs, limit)
        return {"jobs": jobs}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_complacency_jobs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.post("/ideas/complacency/refresh-sectors")
async def refresh_complacency_sectors(max_workers: int = 8):
    """
    Manually re-pull S&P 500 EV/Sales + FCF Yield and recompute the per-sector
    medians cached in `sector_medians`. Takes ~5 min on FMP Premium. The
    complacency cohort runner also calls this auto-magically when the cache
    is >7 days old.
    """
    try:
        from src.research_ideas.complacency.sector_medians import refresh_sector_medians
        summary = await asyncio.to_thread(
            refresh_sector_medians, max_workers=max_workers, persist=True
        )
        return summary
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("refresh_complacency_sectors failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/complacency/sectors")
async def list_complacency_sector_medians(metric: str = "ev_sales"):
    """Latest cached sector medians for the given metric (default ev_sales)."""
    try:
        from app.backend.services.sector_medians_storage import (
            list_latest_sector_medians,
            get_latest_refresh_timestamp,
        )
        rows = await asyncio.to_thread(list_latest_sector_medians, metric)
        latest_at = await asyncio.to_thread(get_latest_refresh_timestamp)
        return {
            "metric": metric,
            "latest_refresh_at": latest_at,
            "rows": rows,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_complacency_sector_medians failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")
