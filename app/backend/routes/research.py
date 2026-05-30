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
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException

from app.backend.services import sw46_storage, complacency_storage
from app.backend.services import complacency_job_store as job_store
from app.backend.services import contrarian_storage
from app.backend.services import hk50_storage
from src.research_ideas.sw46.runner import run_sw46
from src.research_ideas.sw46.universe import list_tickers as sw46_list_tickers
from src.research_ideas.hk50.runner import run_hk50
from src.research_ideas.hk50.universe import list_tickers as hk50_list_tickers
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
    latest_hk50 = await asyncio.to_thread(hk50_storage.get_latest_hk50_run)

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
        # Richer fields for the hero-card excerpt (mode + thematic context
        # + catalyst preview). All optional — frontend renders gracefully
        # if any are null.
        "latest_idea_mode": latest_iotd.get("idea_mode") if latest_iotd else None,
        "latest_idea_region": latest_iotd.get("region") if latest_iotd else None,
        "latest_idea_sector": latest_iotd.get("sector") if latest_iotd else None,
        "latest_idea_company": latest_iotd.get("company_name") if latest_iotd else None,
        "latest_idea_theme": (
            (latest_iotd.get("theme") or latest_iotd.get("industry_theme"))
            if latest_iotd else None
        ),
        "latest_idea_catalyst": latest_iotd.get("primary_catalyst") if latest_iotd else None,
        "latest_idea_vehicle": latest_iotd.get("expression_vehicle") if latest_iotd else None,
    }

    # HK50 — "Long China / HK" two-screener cohort. The hero card shows the
    # top-5 of each screen, so build those preview lists from the latest run.
    def _top5(results: list, key: str) -> list[dict]:
        ranked = sorted(results, key=lambda r: (r.get(key) or 0.0), reverse=True)[:5]
        return [
            {
                "ticker": r.get("ticker"),
                "name": r.get("name"),
                "score": r.get(key),
            }
            for r in ranked
        ]

    hk50_results = (latest_hk50.get("results") or []) if latest_hk50 else []
    hk50_meta = {
        "id": "hk50",
        "name": "Long China / HK — Growth & Dividend Screeners",
        "blurb": (
            "A 50-name China/Hong Kong universe scored by two independent "
            "0-100 screens — High Growth and High Dividend — and valued with "
            "the IV15 engine (AICT-modulated for software / internet names). "
            "Toggle between the Growth and Dividend rankings."
        ),
        "ticker_count": len(hk50_list_tickers()),
        "last_run_at": latest_hk50.get("created_at") if latest_hk50 else None,
        "last_avg_growth": latest_hk50.get("avg_growth") if latest_hk50 else None,
        "last_avg_dividend": latest_hk50.get("avg_dividend") if latest_hk50 else None,
        "headline_metric_label": "Avg Growth / Dividend",
        "top5_growth": _top5(hk50_results, "growth_score"),
        "top5_dividend": _top5(hk50_results, "dividend_score"),
    }

    return {"ideas": [iotd_meta, sw46_meta, complacency_meta, hk50_meta]}


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


def _execute_idea_gen_job(job_id: str, mode: str | None = None) -> None:
    """Background worker for idea generation (~30-90s via Qwen web search)."""
    base_msg = (
        f"Qwen searching the web for contrarian opportunities (mode: {mode})"
        if mode else
        "Qwen searching the web for contrarian opportunities"
    )
    job_store.update_progress(job_id, "running", base_msg)
    try:
        # Exclude tickers from the last 7 generated ideas to avoid same-day duplicates
        recent = contrarian_storage.list_ideas(limit=7, include_deleted=True)
        exclude = [r.get("ticker") for r in recent if r.get("ticker")]

        idea = _with_heartbeat(
            job_id, base_msg,
            lambda: generate_idea_of_the_day(exclude_tickers=exclude, mode=mode),
        )
        if not idea:
            job_store.fail_job(job_id, "generation returned empty (Qwen failed or invalid JSON)")
            return
        contrarian_storage.save_idea(idea)

        # Best-effort Slack push (no-op if SLACK_WEBHOOK_URL not set).
        # Manual generation triggers Slack same as the daily scheduler so
        # users can opt to fire ad-hoc ideas to the channel.
        try:
            import os as _os
            from src.research_ideas.contrarian.notifier import notify_slack
            notify_slack(idea, app_base_url=_os.environ.get("APP_BASE_URL"))
        except Exception as exc:
            logger.warning("Slack notify failed for idea %s: %s", idea.get("idea_id"), exc)

        job_store.complete_job(job_id, idea)
    except Exception as exc:
        logger.exception("idea-of-the-day generation job %s failed: %s", job_id, exc)
        job_store.fail_job(job_id, f"{type(exc).__name__}: {exc}")


@router.post("/ideas/idea-of-the-day/generate")
async def trigger_idea_generation(mode: str | None = None):
    """
    Kick off a new idea-of-the-day generation as a background job.

    Optional `mode` query param forces a specific generation methodology:
      'deep_value'           — bottom-up contrarian US single-stock pick
      'thematic_geographic'  — top-down country/region thesis → stock
      'thematic_sector'      — top-down industry trend → stock
      'special_situation'    — spin-off, M&A arb, restructuring

    If omitted, rotates daily based on date.
    """
    valid_modes = {"deep_value", "thematic_geographic", "thematic_sector", "special_situation"}
    if mode is not None and mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {mode!r}. Valid: {sorted(valid_modes)}",
        )

    in_flight = job_store.find_in_flight_job("idea_of_the_day_gen")
    if in_flight:
        return {**in_flight, "deduped": True}

    job_id = job_store.create_job("idea_of_the_day_gen")

    async def _run():
        await asyncio.to_thread(_execute_idea_gen_job, job_id, mode)

    _spawn_background(_run())
    return {"job_id": job_id, "status": "pending", "started_at": None, "deduped": False, "mode": mode}


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


# ─── HK50 ("Long China / HK") endpoints ──────────────────────────────────


@router.get("/ideas/hk50")
async def get_hk50_cohort():
    """Most-recent persisted HK50 cohort snapshot (both screen scores per row)."""
    try:
        latest = await asyncio.to_thread(hk50_storage.get_latest_hk50_run)
        if not latest:
            return {
                "run_id": None,
                "created_at": None,
                "ticker_count": 0,
                "avg_growth": None,
                "avg_dividend": None,
                "median_p_iv15": None,
                "lead_growth_count": 0,
                "failed_tickers": [],
                "results": [],
            }
        return latest
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_hk50_cohort failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/hk50/runs")
async def list_hk50_runs(limit: int = 20):
    """Historical HK50 cohort runs (header-only — no results JSON)."""
    try:
        runs = await asyncio.to_thread(hk50_storage.list_hk50_runs, limit)
        return {"runs": runs}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_hk50_runs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/hk50/{ticker}")
async def get_hk50_ticker(ticker: str):
    """Per-ticker detail — pulls from the latest cohort run. `ticker` matches
    either the reported (ADR/HK) ticker or the canonical HK ticker."""
    needle = ticker.upper()
    try:
        latest = await asyncio.to_thread(hk50_storage.get_latest_hk50_run)
        if not latest:
            raise HTTPException(status_code=404, detail="No HK50 cohort run yet — refresh first")
        for r in latest.get("results", []):
            if (r.get("ticker") or "").upper() == needle or (r.get("hk_ticker") or "").upper() == needle:
                return r
        raise HTTPException(status_code=404, detail=f"{ticker} not in HK50 universe")
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_hk50_ticker(%s) failed: %s", ticker, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.post("/ideas/hk50/refresh")
async def refresh_hk50(max_workers: int = 6):
    """
    Trigger a fresh HK50 cohort run. Synchronous (returns once persisted) —
    a full run takes ~2-3 min for 50 names across FMP (ADRs) + AKShare/yfinance
    (native HK). Frontend should display a spinner.
    """
    try:
        cohort = await asyncio.to_thread(
            run_hk50,
            max_workers=max_workers,
            save=True,
        )
        return {
            "run_id": cohort.run_id,
            "created_at": cohort.created_at,
            "ticker_count": cohort.ticker_count,
            "avg_growth": cohort.avg_growth,
            "avg_dividend": cohort.avg_dividend,
            "median_p_iv15": cohort.median_p_iv15,
            "lead_growth_count": cohort.lead_growth_count,
            "failed_tickers": cohort.failed_tickers,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("refresh_hk50 failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# ─── HK50 qualitative deep-research (two-phase, background job) ───────────
# Phase 1 (quant + curated overlay) is ALREADY persisted by /ideas/hk50/refresh
# — the cards show growth/dividend/IV15 + curated policy/moat tiers
# immediately. This job is Phase 2: it runs the LLM web-research qualitative
# pass over the top-N names by lead score, SEQUENTIALLY (one name at a time),
# patching each cohort row's `qualitative` overlay live as sub-metrics land.
#
# SEQUENTIAL at the cohort level is the core 429 defense: each name fires up
# to `max_workers` (3) concurrent Qwen calls; running N names in parallel
# would mean 3×N concurrent calls → guaranteed DashScope 429 cascade. On top
# of that, hk50_qualitative adds the shared qwen_throttle bucket, a 1.5s
# submission stagger, and a per-name deep-research cap.


def _execute_hk50_qual_job(job_id: str, top_n: int, force_refresh: bool) -> None:
    """Phase-2 LLM qualitative worker for the HK50 cohort (see module note)."""
    import os as _os
    from src.research_ideas.hk50.hk50_qualitative import assess_hk50_qualitative

    latest = hk50_storage.get_latest_hk50_run()
    if not latest or not latest.get("results"):
        job_store.fail_job(
            job_id,
            "No HK50 cohort yet — run POST /ideas/hk50/refresh first so the "
            "quant cards exist before deep research.",
        )
        return

    if not _os.environ.get("DEEP_RESEARCH_API_KEY"):
        job_store.complete_job(job_id, {
            "phase2_skipped": "DEEP_RESEARCH_API_KEY missing",
            "ticker_count": latest.get("ticker_count"),
        })
        return

    results = latest.get("results") or []

    def _lead(r: dict) -> float:
        return max(
            float(r.get("growth_score") or 0.0),
            float(r.get("dividend_score") or 0.0),
        )

    # Gate: the top-N names by lead screen score. These are exactly where a
    # great screen can mask a policy/moat trap (the WuXi Bio / QUANT-RICH
    # case), so they earn the deep-research spend first.
    gate = sorted(results, key=_lead, reverse=True)
    if top_n and top_n > 0:
        gate = gate[:top_n]

    summary = {
        "gate_tickers": [r.get("hk_ticker") or r.get("ticker") for r in gate],
        "qual_scored": [],
        "deep_by_ticker": {},
        "total_deep_escalations": 0,
        "failed": [],
    }

    for idx, r in enumerate(gate, 1):
        hk_ticker = r.get("hk_ticker") or r.get("ticker")
        rep = r.get("ticker") or hk_ticker
        nm = r.get("name") or hk_ticker
        lead = _lead(r)

        def _patch_partial(code, indicator_score, needle=hk_ticker):
            try:
                hk50_storage.update_ticker_in_latest_cohort_partial_qual(
                    needle, code, indicator_score.model_dump(),
                )
            except Exception as exc:
                logger.warning("HK50 partial qual patch %s/%s: %s", needle, code, exc)

        job_store.update_progress(
            job_id, "running",
            f"phase 2: qual {idx}/{len(gate)} · {nm} ({hk_ticker})",
        )

        try:
            qual = assess_hk50_qualitative(
                hk_ticker,
                report_ticker=rep,
                name=nm,
                quant_lead_score=lead,
                use_llm=True,
                force_refresh=force_refresh,
                on_indicator_done=_patch_partial,
            )
            if qual:
                qual.incomplete = qual.incomplete or False
                hk50_storage.set_ticker_qualitative_in_latest_cohort(
                    hk_ticker, qual.model_dump(),
                )
                n_deep = 0
                for dim in (qual.policy, qual.moat):
                    if not dim:
                        continue
                    for s in (dim.indicators or {}).values():
                        if "deep" in (getattr(s, "model_used", "") or "").lower():
                            n_deep += 1
                summary["qual_scored"].append(hk_ticker)
                summary["deep_by_ticker"][hk_ticker] = n_deep
                summary["total_deep_escalations"] += n_deep
        except Exception as exc:
            logger.warning(
                "HK50 phase-2 qual for %s threw — keeping curated/partial: %s",
                hk_ticker, exc,
            )
            summary["failed"].append({"ticker": hk_ticker, "reason": str(exc)[:200]})

    job_store.complete_job(job_id, {
        "run_id": latest.get("run_id"),
        "ticker_count": latest.get("ticker_count"),
        "gate_count": len(gate),
        "phase2_summary": summary,
    })
    logger.info(
        "HK50 qual job %s complete: %d gate names, %d scored, %d deep escalations",
        job_id, len(gate), len(summary["qual_scored"]),
        summary["total_deep_escalations"],
    )


@router.post("/ideas/hk50/qual-deep-research")
async def hk50_qual_deep_research(top_n: int = 20, force_refresh: bool = False):
    """
    Kick off the Phase-2 LLM qualitative (Policy + Moat) deep-research pass as
    a BACKGROUND job. The quant cards must already exist (run /refresh first).
    Returns {job_id, status} immediately; poll GET /ideas/hk50/jobs/{job_id}.

    Dedupes: a second click while one is in flight returns the existing job.
    """
    in_flight = job_store.find_in_flight_job("hk50_qual")
    if in_flight:
        return {
            "job_id": in_flight["job_id"],
            "status": in_flight["status"],
            "started_at": in_flight["started_at"],
            "deduped": True,
        }

    job_id = job_store.create_job("hk50_qual")

    async def _run():
        await asyncio.to_thread(_execute_hk50_qual_job, job_id, top_n, force_refresh)

    _spawn_background(_run())
    return {"job_id": job_id, "status": "pending", "started_at": None, "deduped": False}


@router.get("/ideas/hk50/jobs/{job_id}")
async def get_hk50_job(job_id: str):
    """Current state of an HK50 background job (shared job store)."""
    try:
        job = await asyncio.to_thread(job_store.get_job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return job
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("get_hk50_job(%s) failed: %s", job_id, exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


def _execute_hk50_ticker_qual_job(job_id: str, ticker: str, force_refresh: bool) -> None:
    """
    Manual Phase-2 LLM qualitative worker for ONE HK50 name. Mirrors the cohort
    batch worker (_execute_hk50_qual_job) but targets a single ticker the user
    drilled into, so they can deep-research a name on demand without re-running
    the whole top-N gate. Patches the row live; final-stamps when done.
    """
    import os as _os
    from src.research_ideas.hk50.hk50_qualitative import assess_hk50_qualitative

    latest = hk50_storage.get_latest_hk50_run()
    if not latest or not latest.get("results"):
        job_store.fail_job(
            job_id,
            "No HK50 cohort yet — run POST /ideas/hk50/refresh first so the "
            "quant card exists before deep research.",
        )
        return

    if not _os.environ.get("DEEP_RESEARCH_API_KEY"):
        job_store.fail_job(
            job_id,
            "DEEP_RESEARCH_API_KEY is not set on this deployment — cannot run "
            "manual qualitative research.",
        )
        return

    needle = (ticker or "").strip().upper()
    results = latest.get("results") or []
    row = None
    for r in results:
        if (r.get("ticker") or "").upper() == needle or (r.get("hk_ticker") or "").upper() == needle:
            row = r
            break
    if row is None:
        job_store.fail_job(
            job_id,
            f"{needle} is not in the latest HK50 cohort — only cohort names can "
            "be deep-researched.",
        )
        return

    hk_ticker = row.get("hk_ticker") or row.get("ticker")
    rep = row.get("ticker") or hk_ticker
    nm = row.get("name") or hk_ticker
    lead = max(
        float(row.get("growth_score") or 0.0),
        float(row.get("dividend_score") or 0.0),
    )

    def _push_live_row():
        """Surface the freshly-patched cohort row into the job result so the
        drawer can fill in even before the next cohort poll lands."""
        try:
            fresh = hk50_storage.get_latest_hk50_run()
            if not fresh:
                return
            for rr in fresh.get("results", []):
                if (rr.get("hk_ticker") or "").upper() == (hk_ticker or "").upper():
                    job_store.update_running_result(job_id, rr)
                    break
        except Exception:
            pass

    def _patch_partial(code, indicator_score, needle=hk_ticker):
        try:
            hk50_storage.update_ticker_in_latest_cohort_partial_qual(
                needle, code, indicator_score.model_dump(),
            )
        except Exception as exc:
            logger.warning("HK50 partial qual patch %s/%s: %s", needle, code, exc)
        job_store.update_progress(
            job_id, "running",
            f"manual qual · {nm} ({hk_ticker}) · {code}",
        )
        _push_live_row()

    job_store.update_progress(job_id, "running", f"manual qual · {nm} ({hk_ticker})")

    try:
        qual = assess_hk50_qualitative(
            hk_ticker,
            report_ticker=rep,
            name=nm,
            quant_lead_score=lead,
            use_llm=True,
            force_refresh=force_refresh,
            on_indicator_done=_patch_partial,
        )
    except Exception as exc:
        logger.exception("HK50 manual qual for %s threw: %s", hk_ticker, exc)
        job_store.fail_job(
            job_id,
            f"qualitative raised {type(exc).__name__}: {str(exc)[:200]} — "
            "curated/partial preserved",
        )
        return

    n_deep = 0
    if qual:
        qual.incomplete = qual.incomplete or False
        try:
            hk50_storage.set_ticker_qualitative_in_latest_cohort(hk_ticker, qual.model_dump())
        except Exception as exc:
            logger.warning("HK50 final qual stamp for %s failed: %s", hk_ticker, exc)
        for dim in (qual.policy, qual.moat):
            if not dim:
                continue
            for s in (dim.indicators or {}).values():
                if "deep" in (getattr(s, "model_used", "") or "").lower():
                    n_deep += 1

    # Final job result carries the freshly-patched row for the drawer.
    final_row = row
    try:
        fresh = hk50_storage.get_latest_hk50_run()
        if fresh:
            for rr in fresh.get("results", []):
                if (rr.get("hk_ticker") or "").upper() == (hk_ticker or "").upper():
                    final_row = rr
                    break
    except Exception:
        pass

    job_store.complete_job(job_id, {
        "run_id": latest.get("run_id"),
        "hk_ticker": hk_ticker,
        "ticker": rep,
        "conviction": (qual.conviction if qual else None),
        "deep_escalations": n_deep,
        "result": final_row,
    })
    logger.info(
        "HK50 manual qual job %s complete: %s · %d deep escalations",
        job_id, hk_ticker, n_deep,
    )


@router.post("/ideas/hk50/qual/{ticker}")
async def hk50_qual_one_ticker(ticker: str, force_refresh: bool = False):
    """
    Manual deep-research for ONE HK50 cohort name. Runs the LLM Policy + Moat
    overlay for a single ticker as a BACKGROUND job, patching the cohort row
    live. Returns {job_id, status} immediately; poll GET /ideas/hk50/jobs/{job_id}
    (shared job store with the batch deep-research).

    Dedupes per-ticker: a second click while one is in flight returns the
    existing job.
    """
    needle = (ticker or "").strip().upper()
    if not needle:
        raise HTTPException(status_code=400, detail="ticker required")

    in_flight = job_store.find_in_flight_job("hk50_qual_ticker", ticker=needle)
    if in_flight:
        return {
            "job_id": in_flight["job_id"],
            "status": in_flight["status"],
            "started_at": in_flight["started_at"],
            "deduped": True,
        }

    job_id = job_store.create_job("hk50_qual_ticker", ticker=needle)

    async def _run():
        await asyncio.to_thread(_execute_hk50_ticker_qual_job, job_id, needle, force_refresh)

    _spawn_background(_run())
    return {"job_id": job_id, "status": "pending", "started_at": None, "deduped": False}


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
    """
    Two-phase master refresh worker.

      PHASE 1 — ALL 50 tickers quant-only, parallel (~30-60s):
        Each ticker → score_one_ticker_quant. Assemble cohort.
        save_complacency_run → FULL COHORT PERSISTED HERE.
        Frontend table immediately shows new verdicts + composites.

      PHASE 2 — Gate-passers (Strong-Short / Watch) only, SEQUENTIAL:
        Run assess_qualitative per ticker, one at a time, to keep the
        Qwen token bucket happy. Per-indicator callback patches the
        cohort row live so users opening the drawer mid-refresh see
        indicators landing in real time.

        Sequential (not parallel) because each gate ticker fires up to
        3 concurrent Qwen calls; running 5 gate tickers in parallel
        would mean 15 concurrent Qwen calls → guaranteed 429 cascade.
    """
    from src.research_ideas.complacency.runner import (
        score_one_ticker_quant, _compute_aggregate,
    )
    from src.research_ideas.complacency.universe import list_tickers, get_ticker_metadata
    from src.research_ideas.complacency.schemas import (
        ComplacencyCohortResult, ComplacencyTickerResult,
    )
    from src.research_ideas.complacency.qualitative import assess_qualitative
    from datetime import datetime, timezone
    import uuid

    # ── PHASE 1: quant only for all tickers, parallel ────────────────
    job_store.update_progress(
        job_id, "running",
        f"phase 1: quant scoring all tickers (parallel)",
    )
    tickers = list_tickers()
    quant_results: dict = {}

    def _quant_one(ticker):
        meta = get_ticker_metadata(ticker)
        return score_one_ticker_quant(
            ticker,
            name=meta.get("name"),
            sector=meta.get("sector"),
            industry=meta.get("industry"),
        )

    # Phase 1 timeouts tuned for Railway FMP rate-limiting resilience:
    #  • per-future 240s: a single FMP 429 triggers a 60s sleep in
    #    _fmp_get (up to 3 attempts = 180s). 240s lets a ticker survive
    #    one or two 429 retries instead of being killed mid-sleep.
    #  • outer 1200s (20 min): 50 tickers ÷ 3 workers, with FMP
    #    rate-limit sleeps factored in. Generous because it's a
    #    background job — Phase 1 cohort saves as soon as the slowest
    #    ticker finishes or times out.
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_quant_one, t): t for t in tickers}
            done_n = 0
            for fut in as_completed(futures, timeout=1200):
                t = futures[fut]
                try:
                    out = fut.result(timeout=240)
                    quant_results[t] = out
                except Exception as exc:
                    logger.warning("Phase 1 quant for %s failed: %s", t, exc)
                    quant_results[t] = {"ticker": t, "reason": str(exc)[:200]}
                done_n += 1
                # Progress heartbeat every 5 tickers so the toast shows
                # Phase-1 advancement on Railway (long-running fetch).
                if done_n % 5 == 0 or done_n == len(tickers):
                    n_ok = sum(1 for r in quant_results.values()
                               if isinstance(r, ComplacencyTickerResult))
                    job_store.update_progress(
                        job_id, "running",
                        f"phase 1: quant {done_n}/{len(tickers)} fetched ({n_ok} ok)",
                    )
    except Exception as exc:
        logger.exception("Phase 1 (quant) outer timeout/error: %s", exc)

    # Assemble cohort
    results_list = []
    failed_tickers = []
    for t in tickers:
        r = quant_results.get(t)
        if isinstance(r, ComplacencyTickerResult):
            results_list.append(r)
        elif isinstance(r, dict) and r.get("reason"):
            failed_tickers.append({"ticker": t, "reason": r["reason"]})
        else:
            # Future never completed (outer timeout) — record so the
            # failure is visible rather than silently dropped.
            failed_tickers.append({"ticker": t, "reason": "did not complete (outer timeout)"})

    # ── EMPTY-COHORT GUARD ────────────────────────────────────────────
    # CRITICAL: never overwrite a good persisted cohort with an empty
    # one. If Phase 1 produced zero valid tickers (FMP outage, rate-limit
    # cascade, all timeouts), the previous run is far more valuable than
    # a blank table. Fail the job WITHOUT saving, preserving the prior
    # cohort. Surface a SAMPLE of failure reasons so the cause is
    # diagnosable from the error toast / job record.
    if not results_list:
        reason_sample = "; ".join(
            f"{f['ticker']}: {f['reason']}" for f in failed_tickers[:3]
        )
        logger.error(
            "Master refresh job %s: Phase 1 produced 0 valid tickers "
            "(%d failed). NOT overwriting existing cohort. Sample: %s",
            job_id, len(failed_tickers), reason_sample,
        )
        job_store.fail_job(
            job_id,
            f"Refresh produced 0 valid tickers ({len(failed_tickers)} failed). "
            f"Previous cohort preserved. Sample failures: {reason_sample[:300]}",
        )
        return

    results_list.sort(key=lambda r: (-(r.composite or 0.0), r.ticker))
    for i, r in enumerate(results_list, 1):
        r.rank = i
    gate_passers = sum(1 for r in results_list if r.passes_gate)
    cohort = ComplacencyCohortResult(
        run_id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        ticker_count=len(results_list),
        gate_passers=gate_passers,
        failed_tickers=failed_tickers,
        results=results_list,
    )
    complacency_storage.save_complacency_run(cohort)

    job_store.update_progress(
        job_id, "running",
        f"phase 1 done · {len(results_list)} tickers · "
        f"{gate_passers} gate passers · starting phase 2 qual...",
    )
    job_store.update_running_result(job_id, {
        "phase": "phase1_done",
        "run_id": cohort.run_id,
        "ticker_count": cohort.ticker_count,
        "gate_passers": cohort.gate_passers,
    })

    # ── PHASE 2: qualitative for gate passers, SEQUENTIAL ────────────
    import os as _os
    if not _os.environ.get("DEEP_RESEARCH_API_KEY"):
        logger.warning("Phase 2 skipped: DEEP_RESEARCH_API_KEY missing")
        job_store.complete_job(job_id, {
            "run_id": cohort.run_id,
            "created_at": cohort.created_at,
            "ticker_count": cohort.ticker_count,
            "gate_passers": cohort.gate_passers,
            "failed_tickers": cohort.failed_tickers,
            "phase2_skipped": "DEEP_RESEARCH_API_KEY missing",
        })
        return

    gate_tickers = [r for r in results_list if r.verdict in ("Strong-Short", "Watch")]
    # Verification accounting — surfaced in the completion result so the
    # user can see EXACTLY what Phase 2 did (was deep research run, or
    # cache reused, etc.) without guessing.
    phase2_summary = {
        "gate_tickers": [t.ticker for t in gate_tickers],
        "qual_scored": [],            # tickers that got a qual assessment
        "deep_research_by_ticker": {},  # ticker -> # indicators that used deep web search
        "total_deep_escalations": 0,
        "fully_cached": [],           # tickers where qual came entirely from cache (no deep)
    }
    for i, gate_result in enumerate(gate_tickers, 1):
        tkr = gate_result.ticker

        def _patch_partial(code, indicator_score, ticker=tkr):
            try:
                complacency_storage.update_ticker_in_latest_cohort_partial_qual(
                    ticker, code, indicator_score.model_dump(),
                )
            except Exception as exc:
                logger.warning("Partial qual patch %s/%s: %s", ticker, code, exc)

        job_store.update_progress(
            job_id, "running",
            f"phase 2: qual {i}/{len(gate_tickers)} · {tkr}",
        )

        try:
            qual = assess_qualitative(
                ticker=tkr,
                name=gate_result.name,
                sector=gate_result.sector,
                quant_passes_gate=gate_result.passes_gate,
                quant_composite=gate_result.composite,
                # force_refresh=False for master refresh: honor 7-day cache.
                # Deep research only RE-RUNS for tickers whose cache is
                # missing/stale. Use per-ticker "Re-score (force fresh)"
                # to force fresh deep research.
                force_refresh=False,
                on_indicator_done=_patch_partial,
            )
            # Final patch with full qual + recomputed aggregate
            final_payload = gate_result.model_dump()
            if qual:
                final_payload["qualitative"] = qual.model_dump()
                final_payload.update(_compute_aggregate(gate_result.composite, qual))
                # Count deep-research escalations for this ticker
                n_deep = sum(
                    1 for s in qual.indicators.values()
                    if "deep" in (s.model_used or "").lower()
                )
                phase2_summary["qual_scored"].append(tkr)
                phase2_summary["deep_research_by_ticker"][tkr] = n_deep
                phase2_summary["total_deep_escalations"] += n_deep
                if n_deep == 0:
                    phase2_summary["fully_cached"].append(tkr)
            try:
                complacency_storage.update_ticker_in_latest_cohort(final_payload)
            except Exception as exc:
                logger.warning("Final qual patch for %s: %s", tkr, exc)
        except Exception as exc:
            logger.warning(
                "Phase 2 qual for %s threw — keeping quant + partial qual: %s",
                tkr, exc,
            )

    job_store.complete_job(job_id, {
        "run_id": cohort.run_id,
        "created_at": cohort.created_at,
        "universe": cohort.universe,
        "ticker_count": cohort.ticker_count,
        "gate_passers": cohort.gate_passers,
        "failed_tickers": cohort.failed_tickers,
        "phase2_tickers_processed": len(gate_tickers),
        "phase2_summary": phase2_summary,
    })
    logger.info(
        "Master refresh job %s complete: %d tickers, %d gate passers, "
        "%d qual-scored, %d total deep-research escalations",
        job_id, cohort.ticker_count, gate_passers,
        len(phase2_summary["qual_scored"]),
        phase2_summary["total_deep_escalations"],
    )


@router.post("/ideas/complacency/refresh")
async def refresh_complacency(max_workers: int = 3):
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
    """
    Two-phase score job worker.

      PHASE 1 — Quant only (~5-15s):
        score_one_ticker_quant → cohort patch → publish running:quant_done
        Even if Phase 2 fails, the new quant verdict + pillars are saved.

      PHASE 2 — Qualitative (~5-12 min, if force_qual or gate verdict):
        assess_qualitative with on_indicator_done callback.
        Each indicator's completion → cohort patch + job result update.
        If we hit the 12-min outer timeout, partial qual is preserved.
    """
    from src.research_ideas.complacency.runner import score_one_ticker_quant

    # ── PHASE 1: quant only ──────────────────────────────────────────
    job_store.update_progress(
        job_id, "running",
        f"phase 1: quant scoring {ticker}",
    )
    try:
        quant_result = score_one_ticker_quant(ticker)
    except Exception as exc:
        logger.exception("Phase 1 (quant) for %s failed: %s", ticker, exc)
        job_store.fail_job(job_id, f"phase 1 failed: {type(exc).__name__}: {exc}")
        return

    if isinstance(quant_result, dict) and quant_result.get("reason"):
        job_store.fail_job(job_id, f"phase 1: {ticker}: {quant_result.get('reason')}")
        return

    quant_payload = (
        quant_result.model_dump() if hasattr(quant_result, "model_dump") else quant_result
    )

    # Patch cohort with quant result (if ticker is in cohort)
    try:
        persisted_quant = complacency_storage.update_ticker_in_latest_cohort(quant_payload)
    except Exception as exc:
        logger.warning("Cohort patch (quant) for %s failed: %s", ticker, exc)
        persisted_quant = False

    quant_payload["_persisted_to_cohort"] = persisted_quant
    job_store.update_running_result(job_id, quant_payload)
    job_store.update_progress(
        job_id, "running",
        f"phase 1 done · {ticker} {quant_result.verdict} · composite "
        f"{quant_result.composite:.1f}/8 · agg {quant_result.aggregate_score or 0}/100",
    )

    # ── PHASE 2: qualitative (conditional) ───────────────────────────
    should_qual = force_qual or quant_result.verdict in ("Strong-Short", "Watch")
    if not should_qual:
        # Pure ad-hoc on Borderline/Pass without force_qual — done after quant
        job_store.complete_job(job_id, quant_payload)
        return

    import os as _os
    if not _os.environ.get("DEEP_RESEARCH_API_KEY"):
        quant_payload["error"] = (
            "DEEP_RESEARCH_API_KEY is not set on this deployment. "
            "Quant saved; qualitative skipped."
        )
        try:
            complacency_storage.update_ticker_in_latest_cohort(quant_payload)
        except Exception:
            pass
        job_store.complete_job(job_id, quant_payload)
        return

    from src.research_ideas.complacency.qualitative import assess_qualitative

    # Callback fires per-indicator: patches cohort row + job result
    indicators_done_count = {"n": 0}
    base_qual_msg = f"phase 2: qual {ticker}"

    def _on_indicator_done(code, indicator_score):
        indicators_done_count["n"] += 1
        n = indicators_done_count["n"]
        # Patch cohort with this single indicator
        try:
            complacency_storage.update_ticker_in_latest_cohort_partial_qual(
                ticker, code, indicator_score.model_dump(),
            )
        except Exception as exc:
            logger.warning(
                "Partial-qual cohort patch for %s/%s failed: %s",
                ticker, code, exc,
            )
        # Update job state with progress
        deep = " ★DEEP" if "deep" in (indicator_score.model_used or "").lower() else ""
        job_store.update_progress(
            job_id, "running",
            f"{base_qual_msg} {n}/10 · {code} = {indicator_score.score}/5 "
            f"conf {indicator_score.confidence:.0%}{deep}",
        )
        # Also update running result so frontend's drawer can pick up the
        # incremental qualitative state without a cohort poll
        try:
            from app.backend.services.complacency_storage import get_latest_complacency_run
            latest = get_latest_complacency_run()
            if latest:
                for r in latest.get("results", []):
                    if (r.get("ticker") or "").upper() == ticker.upper():
                        job_store.update_running_result(job_id, r)
                        break
        except Exception:
            pass

    try:
        qual_assessment = _with_heartbeat(
            job_id, base_qual_msg,
            lambda: assess_qualitative(
                ticker=ticker,
                name=quant_result.name,
                sector=quant_result.sector,
                quant_passes_gate=quant_result.passes_gate,
                quant_composite=quant_result.composite,
                force_refresh=force_qual,
                on_indicator_done=_on_indicator_done,
            ),
        )
    except Exception as exc:
        logger.exception("Phase 2 (qual) for %s failed: %s", ticker, exc)
        # Quant + whatever qual streamed is already in cohort. Complete
        # the job with the latest cohort row state.
        quant_payload["error"] = (
            f"phase 2 raised {type(exc).__name__}: {str(exc)[:200]} — "
            f"quant + partial qual preserved"
        )
        job_store.complete_job(job_id, quant_payload)
        return

    # Phase 2 complete (possibly with some indicators incomplete due to
    # outer timeout). Final cohort row update with full assessment.
    final_payload = quant_payload
    if qual_assessment:
        final_payload["qualitative"] = qual_assessment.model_dump()
        # Recompute aggregate with full assessment
        from src.research_ideas.complacency.runner import _compute_aggregate
        agg = _compute_aggregate(quant_result.composite, qual_assessment)
        final_payload.update(agg)
        if qual_assessment.incomplete:
            final_payload["error"] = (
                f"qualitative completed {len(qual_assessment.indicators)}/10 indicators "
                "(some hit timeout or rate limit) — try Re-score again to fill the gaps "
                "(only failed indicators re-run, high-conf ones preserved)"
            )
    try:
        complacency_storage.update_ticker_in_latest_cohort(final_payload)
    except Exception as exc:
        logger.warning("Final cohort patch for %s failed: %s", ticker, exc)

    job_store.complete_job(job_id, final_payload)


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
