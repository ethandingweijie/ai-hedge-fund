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
from src.research_ideas.sw46.runner import run_sw46
from src.research_ideas.sw46.universe import list_tickers as sw46_list_tickers
from src.research_ideas.complacency.runner import run_complacency, score_one_ticker
from src.research_ideas.complacency.universe import list_tickers as complacency_list_tickers


logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Idea catalogue ────────────────────────────────────────────────────────


@router.get("/ideas")
async def list_research_ideas():
    """Catalogue of research ideas. v1: SW46 + Complacency."""
    latest_sw46 = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
    latest_compl = await asyncio.to_thread(complacency_storage.get_latest_complacency_run)

    sw46_meta = {
        "id": "sw46",
        "name": "SW46 — Software Cohort Valuation",
        "blurb": (
            "Cassandra Unchained / Scion methodology: Tragic Algebra owner "
            "earnings, fully-adjusted ROIC, AI Competitive Threat tiering, "
            "and IV15 (15-year / 15% required-return hybrid intrinsic value)."
        ),
        "ticker_count": len(sw46_list_tickers()),
        "last_run_at": latest_sw46.get("created_at") if latest_sw46 else None,
        "last_pooled_delta_e": latest_sw46.get("cohort_pooled_delta_e") if latest_sw46 else None,
        "headline_metric_label": "Pooled ΔE",
    }

    complacency_meta = {
        "id": "complacency",
        "name": "Complacency Detector — Ackman 4-Pillar",
        "blurb": (
            "Bill Ackman-style equity screener: detects when price has "
            "decoupled from fundamentals across Valuation, Behavioral, "
            "Technical, and Quality pillars. Composite ≥6/8 + all pillars ≥1 "
            "flags structural complacency."
        ),
        "ticker_count": len(complacency_list_tickers()),
        "last_run_at": latest_compl.get("created_at") if latest_compl else None,
        "last_gate_passers": latest_compl.get("gate_passers") if latest_compl else None,
        "headline_metric_label": "Gate passers",
    }

    return {"ideas": [sw46_meta, complacency_meta]}


# ─── SW46 endpoints ────────────────────────────────────────────────────────


@router.get("/ideas/sw46")
async def get_sw46_cohort():
    """Most-recent persisted SW46 cohort snapshot."""
    try:
        latest = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
        if not latest:
            # Empty shape (UI shows "No runs yet — click Refresh").
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
    """Historical SW46 cohort runs (header-only — no results JSON)."""
    try:
        runs = await asyncio.to_thread(sw46_storage.list_sw46_runs, limit)
        return {"runs": runs}
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("list_sw46_runs failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/ideas/sw46/{ticker}")
async def get_sw46_ticker(ticker: str):
    """Per-ticker detail — pulls from the latest cohort run."""
    ticker = ticker.upper()
    try:
        latest = await asyncio.to_thread(sw46_storage.get_latest_sw46_run)
        if not latest:
            raise HTTPException(status_code=404, detail="No SW46 cohort run yet — refresh first")
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
    Trigger a fresh cohort run. Synchronous (returns once persisted) —
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


# ─── Complacency endpoints ────────────────────────────────────────────────


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
            raise HTTPException(status_code=404, detail="No Complacency cohort run yet — refresh first")
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


@router.post("/ideas/complacency/refresh")
async def refresh_complacency(max_workers: int = 4):
    try:
        cohort = await asyncio.to_thread(
            run_complacency,
            max_workers=max_workers,
            save=True,
        )
        return {
            "run_id": cohort.run_id,
            "created_at": cohort.created_at,
            "universe": cohort.universe,
            "ticker_count": cohort.ticker_count,
            "gate_passers": cohort.gate_passers,
            "failed_tickers": cohort.failed_tickers,
        }
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("refresh_complacency failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.post("/ideas/complacency/score/{ticker}")
async def score_complacency_adhoc(ticker: str, force_qual: bool = False):
    """
    Ad-hoc: score ONE ticker. Used for two flows:

      1. Brand-new ticker not in the curated universe — full fresh score.
         Sector / industry auto-looked-up via FMP /profile. NOT persisted
         to the cohort table.

      2. Existing-cohort ticker with `force_qual=true` — the user clicked
         "Generate Qualitative" on a row whose verdict is Borderline / Pass
         (auto-qual normally fires only for Strong-Short / Watch). The
         qualitative LLM runs regardless of verdict, the aggregate is
         recomputed, and the updated row is patched back into the latest
         cohort run so a page-refresh shows the new score.

    Takes ~10-15 sec quant-only; ~4-5 min with qual.
    """
    ticker = ticker.strip().upper()
    if not ticker or not ticker.isalnum() or len(ticker) > 6:
        raise HTTPException(status_code=400, detail=f"Invalid ticker: {ticker!r}")
    try:
        result = await asyncio.to_thread(score_one_ticker, ticker, None, None, None, force_qual)
        if isinstance(result, dict) and result.get("reason"):
            raise HTTPException(
                status_code=422,
                detail=f"{ticker}: {result.get('reason')}"
            )
        payload = result.model_dump() if hasattr(result, "model_dump") else result

        # If force_qual was requested AND this ticker is in the latest cohort,
        # patch the cohort storage so the updated aggregate persists.
        persisted = False
        if force_qual:
            try:
                persisted = await asyncio.to_thread(
                    complacency_storage.update_ticker_in_latest_cohort,
                    payload,
                )
            except Exception as exc:
                logger.warning(
                    "Cohort patch for %s after force_qual failed: %s", ticker, exc
                )
        if isinstance(payload, dict):
            payload["_persisted_to_cohort"] = persisted
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("score_complacency_adhoc(%s) failed: %s", ticker, exc)
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
