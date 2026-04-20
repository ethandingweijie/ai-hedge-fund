"""
app/backend/routes/analysis.py
================================
SSE-streaming endpoint for the 10-phase advanced pipeline,
plus read endpoints for run history and archive summary.
"""

import asyncio
import json
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.backend.database import get_db
from app.backend.services.api_key_service import ApiKeyService
from app.backend.services import analysis_service
from app.backend.services.auth_service import get_user_from_token

logger = logging.getLogger(__name__)

# ── Per-ticker deduplication: one pipeline at a time per ticker+agents ────────
_in_flight: dict[str, asyncio.Event] = {}
_in_flight_lock: asyncio.Lock = asyncio.Lock()

# ── Global pipeline cap: at most 5 concurrent pipelines ──────────────────────
_pipeline_semaphore: asyncio.Semaphore = asyncio.Semaphore(5)

# ── Live phase status per ticker (read-only endpoint for reconnecting clients) ─
# Stores ALL phases per ticker so reconnecting clients can rebuild the full
# progress bar, not just the current phase.
_live_phases: dict[str, dict] = {}       # ticker → latest progress event dict
_live_phase_maps: dict[str, dict] = {}   # ticker → {phase_name: event_dict, ...}

# ── Load .env.local once at import time so FMP_API_KEY and others are available
# for the standalone endpoints (news, financials, intelligence) that run outside
# the pipeline thread and don't benefit from analysis_service's loader.
def _load_env_local() -> None:
    """Load .env.local from the project root into os.environ (always overrides process env)."""
    project_root = Path(__file__).parent.parent.parent.parent
    env_file = project_root / ".env.local"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)   # .env.local wins over process env (e.g. Claude Code settings)
    except ImportError:
        # dotenv not installed — parse manually
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                if k:
                    os.environ[k] = v.strip().strip('"').strip("'")

_load_env_local()


def _get_fmp_key() -> str:
    """Return FMP_API_KEY, re-loading .env.local if needed."""
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        _load_env_local()
        key = os.environ.get("FMP_API_KEY", "")
    return key

router = APIRouter(prefix="/analysis")


def _get_user_id(request: Request, db: Session) -> Optional[int]:
    """Extract user_id from Bearer token if present. Returns None for unauthenticated requests."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user = get_user_from_token(token, db)
        return user.id if user else None
    return None


# ── POST /analysis/run ────────────────────────────────────────────────────────

@router.post("/run")
async def run_analysis(body: dict, request: Request, db: Session = Depends(get_db)):
    """
    Start a full 10-phase pipeline run for a ticker.
    Returns a text/event-stream SSE response with:
      event: start
      event: progress  (repeated)
      event: heartbeat (keepalive, every 60 s of silence)
      event: complete  {run_id, ticker}
      event: error     {error}
    """
    ticker = (body.get("ticker") or "").strip().upper()
    # Normalise HK tickers to canonical "NNNNN.HK" form so the routing cache
    # and web_runs DB always see a consistent key regardless of input format.
    from src.tools.hk.ticker import is_hk_ticker, to_canonical as _hk_canonical
    from src.tools.sg.ticker import is_sg_ticker, to_canonical as _sg_canonical
    if is_hk_ticker(ticker):
        ticker = _hk_canonical(ticker)
    elif is_sg_ticker(ticker):
        ticker = _sg_canonical(ticker)
    model_name = body.get("model", "claude-sonnet-4-6")
    # Use agents list only when explicitly provided and non-empty.
    # `body.get("agents") or None` would coerce [] → None (all 12 agents),
    # so check explicitly for a non-empty list.
    _raw_agents = body.get("agents")
    agents: list[str] | None = _raw_agents if isinstance(_raw_agents, list) and _raw_agents else None

    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    # ── Extract authenticated user (optional — unauthenticated runs stored with NULL user_id)
    user_id = _get_user_id(request, db)

    # ── Cache check (fast path, no lock) ─────────────────────────────────────
    cached = analysis_service.get_cached_run(ticker, within_minutes=30, agents=agents)
    if cached:
        async def cached_event_generator():
            yield f"event: start\ndata: {json.dumps({'ticker': ticker, 'model': model_name})}\n\n"
            yield (
                f"event: cached\ndata: "
                f"{json.dumps({'run_id': cached['run_id'], 'ticker': ticker, 'run_at': cached['run_at']})}\n\n"
            )
        return StreamingResponse(
            cached_event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Dedup lock: prevent duplicate pipelines for the same ticker+agents ────
    # Uses a per-(ticker, agents) asyncio.Event so that concurrent requests for
    # the same analysis wait for the first pipeline to finish, then reuse its
    # cached result instead of launching N identical pipelines.
    _agents_key = ",".join(sorted(agents or []))
    _dedup_key = f"{ticker}::{_agents_key}"
    _wait_event: asyncio.Event | None = None
    _run_event: asyncio.Event | None = None
    _cached_in_lock: dict | None = None

    async with _in_flight_lock:
        # Re-check inside the lock to close the TOCTOU gap (another request may
        # have finished the pipeline between our fast-path check and here).
        _cached_in_lock = analysis_service.get_cached_run(
            ticker, within_minutes=30, agents=agents
        )
        if not _cached_in_lock:
            if _dedup_key in _in_flight:
                # A pipeline for this exact ticker+agents is already running —
                # grab its completion event so we can wait for it.
                _wait_event = _in_flight[_dedup_key]
            else:
                # We are the first — claim the dedup slot.
                _run_event = asyncio.Event()
                _in_flight[_dedup_key] = _run_event

    if _cached_in_lock:
        async def cached_in_lock_generator():
            yield f"event: start\ndata: {json.dumps({'ticker': ticker, 'model': model_name})}\n\n"
            yield (
                f"event: cached\ndata: "
                f"{json.dumps({'run_id': _cached_in_lock['run_id'], 'ticker': ticker, 'run_at': _cached_in_lock['run_at']})}\n\n"
            )
        return StreamingResponse(
            cached_in_lock_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if _wait_event is not None:
        # Waiter path: another request for the same ticker+agents is already in-flight.
        # Wait up to 15 min for it to finish, polling every 30s.  If it finishes
        # successfully we reuse its cached result.  If it fails or times out we
        # tell the client to retry — this cleans up the stale dedup entry so the
        # NEXT request becomes the runner instead of also waiting on a dead event.
        async def waiting_generator():
            yield f"event: start\ndata: {json.dumps({'ticker': ticker, 'model': model_name, 'total_done_phases': 0})}\n\n"
            _wait_msg = "Analysis already in progress for " + ticker + " \u2014 awaiting result\u2026"
            yield f"event: progress\ndata: {json.dumps({'phase': 'pipeline_queued', 'status': 'running', 'summary': _wait_msg})}\n\n"
            _total_waited = 0
            _MAX_WAIT = 900  # 15 min — deep research + 12 agents can take 10-12 min
            _orphaned = False
            while _total_waited < _MAX_WAIT:
                try:
                    await asyncio.wait_for(_wait_event.wait(), timeout=30.0)
                    break
                except asyncio.TimeoutError:
                    _total_waited += 30
                    # Check if the dedup slot was orphaned (runner crashed/restarted
                    # or uvicorn reload wiped the in-memory dict)
                    async with _in_flight_lock:
                        if _dedup_key not in _in_flight:
                            _orphaned = True
                            break
                    yield "event: heartbeat\ndata: {}\n\n"
            else:
                # Timeout exhausted — force-clean the stale dedup entry so ALL
                # subsequent requests can become runners instead of also waiting.
                async with _in_flight_lock:
                    _in_flight.pop(_dedup_key, None)
                yield f"event: error\ndata: {json.dumps({'error': 'Timed out waiting for in-progress analysis (15 min). The next request will start a fresh run.'})}\n\n"
                return

            # Runner finished (event was set) or was orphaned — check for cached result
            _result = analysis_service.get_cached_run(ticker, within_minutes=5, agents=agents)
            if _result:
                yield (
                    f"event: cached\ndata: "
                    f"{json.dumps({'run_id': _result['run_id'], 'ticker': ticker, 'run_at': _result['run_at']})}\n\n"
                )
            else:
                # Runner failed or produced no result — tell client to retry.
                # The dedup slot is already cleaned up by the runner's finally block.
                _reason = "Previous analysis was orphaned" if _orphaned else "Previous analysis failed"
                yield f"event: error\ndata: {json.dumps({'error': _reason + ' — please retry to start a fresh run.'})}\n\n"
        return StreamingResponse(
            waiting_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Runner path: we hold the dedup slot, proceed with the pipeline ────────
    # Load API keys from DB
    api_key_svc = ApiKeyService(db)
    api_keys = api_key_svc.get_api_keys_dict()

    async def event_generator():
        result_container: dict = {}
        error_container: dict = {}
        phase_events: asyncio.Queue = asyncio.Queue()

        # System agents that ALWAYS run and ALWAYS emit "Done":
        # portfolio_manager, risk_manager, fundamentals, growth_agent,
        # news_sentiment, sentiment, technicals, valuation
        # Always-terminal phases = investor agents + fixed pipeline phases
        # Fixed breakdown (21):  (citation_auditor removed from pipeline)
        #   Pipeline "✓" (10): macro_regime_classifier, strategic_router, intelligence_agents,
        #                       deep_research_agent, industry_specialist, dcf_engine,
        #                       investor_agents (synthetic), phase7_complete,
        #                       advanced_risk_manager, portfolio_manager
        #   System "Done"  (7): fundamentals, growth_agent, news_sentiment, sentiment,
        #                       technicals, valuation, advanced_portfolio_manager
        #   Other terminal (4): edgar_hkex_resolver, power_law_agent, value_trap_agent, data_router
        _FIXED_DONE_COUNT = 21
        _investor_count = len(agents) if agents else 12
        _total_done = _investor_count + _FIXED_DONE_COUNT
        yield f"event: start\ndata: {json.dumps({'ticker': ticker, 'model': model_name, 'total_done_phases': _total_done})}\n\n"

        def _on_phase_sync(
            phase: str,
            status: str,
            summary: str,
            reasoning: str = "",
            ticker: str | None = None,
            timestamp: str | None = None,
            partial_data: dict | None = None,
        ):
            """Called from within run_analysis_pipeline (async context)."""
            try:
                event: dict = {"phase": phase, "status": status, "summary": summary}
                if reasoning:
                    event["reasoning"] = reasoning
                if ticker:
                    event["ticker"] = ticker
                if timestamp:
                    event["timestamp"] = timestamp
                if partial_data:
                    event["partial_data"] = partial_data
                phase_events.put_nowait(event)
                # Store latest phase for read-only status endpoint
                _tk = ticker.upper() if ticker else ""
                _phase_event = {
                    "phase": phase, "status": status, "summary": summary,
                    "timestamp": timestamp or "",
                }
                _live_phases[_tk] = _phase_event
                # Accumulate all phases so reconnecting clients can rebuild progress bar
                if _tk not in _live_phase_maps:
                    _live_phase_maps[_tk] = {}
                _live_phase_maps[_tk][phase] = _phase_event
            except Exception:
                pass

        async def _run():
            try:
                async with _pipeline_semaphore:
                    run_id, result = await analysis_service.run_analysis_pipeline(
                        ticker=ticker,
                        model_name=model_name,
                        api_keys=api_keys,
                        on_phase=_on_phase_sync,
                        selected_agents=agents,
                        user_id=user_id,
                    )
                result_container["run_id"] = run_id
                result_container["result"] = result
            except Exception as exc:
                error_container["error"] = str(exc)
            finally:
                # Release the dedup slot and wake any waiters BEFORE signalling
                # __pipeline_done__ so waiters can fetch the result while this
                # SSE stream is still closing.
                async with _in_flight_lock:
                    _in_flight.pop(_dedup_key, None)

                # Write a synthetic "pipeline_complete" phase marker so reconnecting
                # clients can detect completion even if the DB write hasn't landed yet.
                # This stays in _live_phases for 120s before normal cleanup.
                _tk_done = ticker.upper()
                _completion_marker = {
                    "phase": "pipeline_complete",
                    "status": "done",
                    "summary": f"Run completed: {result_container.get('run_id', '')}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "run_id": result_container.get("run_id", ""),
                    "completed": True,
                }
                _live_phases[_tk_done] = _completion_marker
                if _tk_done not in _live_phase_maps:
                    _live_phase_maps[_tk_done] = {}
                _live_phase_maps[_tk_done]["pipeline_complete"] = _completion_marker

                # Schedule delayed cleanup — give clients 2 min to poll and see completion
                async def _delayed_cleanup():
                    await asyncio.sleep(120)
                    _live_phases.pop(_tk_done, None)
                    _live_phase_maps.pop(_tk_done, None)
                asyncio.create_task(_delayed_cleanup())

                if _run_event is not None:
                    _run_event.set()
                await phase_events.put(
                    {"phase": "__pipeline_done__", "status": "done", "summary": ""}
                )

        pipeline_task = asyncio.create_task(_run())

        while True:
            try:
                event = await asyncio.wait_for(phase_events.get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
                continue

            if event["phase"] == "__pipeline_done__":
                break

            yield f"event: progress\ndata: {json.dumps(event)}\n\n"

        await pipeline_task

        if error_container:
            yield f"event: error\ndata: {json.dumps({'error': error_container['error']})}\n\n"
        else:
            yield (
                f"event: complete\ndata: "
                f"{json.dumps({'run_id': result_container['run_id'], 'ticker': ticker})}\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /analysis/runs ────────────────────────────────────────────────────────

@router.get("/runs")
async def get_runs(
    request: Request,
    ticker: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    regime: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    user_id = _get_user_id(request, db)
    try:
        import functools
        return await asyncio.to_thread(
            functools.partial(
                analysis_service.get_history,
                ticker=ticker,
                sector=sector,
                regime=regime,
                action=action,
                outcome=outcome,
                date_from=date_from,
                date_to=date_to,
                page=page,
                page_size=page_size,
                user_id=user_id,
            )
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_history failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# ── GET /analysis/runs/{run_id} ───────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    try:
        result = await asyncio.to_thread(analysis_service.get_run_result, run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_run_result(%s) failed: %s\n%s", run_id, exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# ── DELETE /analysis/runs/{run_id} ───────────────────────────────────────────

@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """Permanently delete a single run from the archive."""
    try:
        deleted = await asyncio.to_thread(analysis_service.delete_run, run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"deleted": run_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_run(%s) failed: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /analysis/status/{ticker} — read-only live phase for reconnecting clients
@router.get("/status/{ticker}")
async def get_pipeline_status(ticker: str):
    """Return the latest progress phase for an in-flight pipeline run.
    Safe to call repeatedly — read-only, never triggers a new run.
    Returns null fields if no run is in progress for this ticker."""
    t = ticker.strip().upper()
    phase_info = _live_phases.get(t)
    in_progress = False
    async with _in_flight_lock:
        in_progress = any(k.startswith(f"{t}::") for k in _in_flight)
    # Return all accumulated phases so reconnecting clients can rebuild progress bar
    all_phases = _live_phase_maps.get(t, {})
    return {
        "ticker": t,
        "in_progress": in_progress,
        "phase": phase_info.get("phase") if phase_info else None,
        "status": phase_info.get("status") if phase_info else None,
        "summary": phase_info.get("summary") if phase_info else None,
        "timestamp": phase_info.get("timestamp") if phase_info else None,
        "all_phases": all_phases,  # {phase_name: {phase, status, summary, timestamp}, ...}
    }


# ── GET /analysis/popular-tickers ────────────────────────────────────────────

@router.get("/popular-tickers")
async def get_popular_tickers(limit: int = Query(default=15, ge=1, le=50)):
    """
    Return the most frequently analysed tickers from the run archive,
    each enriched with today's price change vs the previous close (via yfinance).
    """
    from app.backend.services.analysis_service import _get_db_path
    import sqlite3, yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    db_path = _get_db_path()

    # ── 1. Fetch top tickers by run count in the last 3 days ──────────────────
    def _fetch_popular() -> list[str]:
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Only count runs from the last 3 days so the tape reflects
            # recent interest, not historical accumulation.
            rows = conn.execute(
                """
                SELECT ticker, COUNT(*) AS run_count
                FROM (
                    SELECT ticker FROM web_runs
                     WHERE run_at >= datetime('now', '-3 days')
                    UNION ALL
                    SELECT ts.ticker
                      FROM ticker_signals ts
                      JOIN runs r ON r.run_id = ts.run_id
                     WHERE r.run_at >= datetime('now', '-3 days')
                )
                GROUP BY ticker
                ORDER BY run_count DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            # Fallback: if no runs in last 3 days, widen to all-time so the
            # tape isn't empty for low-traffic deployments.
            if not rows:
                rows = conn.execute(
                    """
                    SELECT ticker, COUNT(*) AS run_count
                    FROM (
                        SELECT ticker FROM web_runs
                        UNION ALL
                        SELECT ticker FROM ticker_signals
                    )
                    GROUP BY ticker
                    ORDER BY run_count DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            conn.close()
            return [r["ticker"] for r in rows]
        except Exception:
            return []

    popular = await asyncio.to_thread(_fetch_popular)

    if not popular:
        return []

    # ── 2. Fetch price change for each ticker in parallel ────────────────────
    def _price_change(ticker: str) -> dict:
        try:
            from src.tools.hk.ticker import is_hk_ticker as _is_hk, to_yfinance_code as _to_yf
            yf_sym = _to_yf(ticker) if _is_hk(ticker) else ticker
            hist = yf.Ticker(yf_sym).history(period="5d")
            if len(hist) >= 2:
                prev  = float(hist["Close"].iloc[-2])
                curr  = float(hist["Close"].iloc[-1])
                chg   = curr - prev
                chg_pct = (chg / prev) * 100 if prev else 0.0
                return {
                    "ticker":   ticker,
                    "price":    round(curr, 2),
                    "change":   round(chg, 2),
                    "change_pct": round(chg_pct, 2),
                }
        except Exception:
            pass
        return {"ticker": ticker, "price": None, "change": None, "change_pct": None}

    def _fetch_prices() -> list[dict]:
        with ThreadPoolExecutor(max_workers=min(len(popular), 10)) as pool:
            return list(pool.map(_price_change, popular))

    return await asyncio.to_thread(_fetch_prices)


# ── GET /analysis/search?q=... ───────────────────────────────────────────────

@router.get("/search")
async def search_companies(q: str = Query(default="", min_length=1), limit: int = Query(default=8, ge=1, le=20)):
    """
    Search for companies by name OR ticker symbol.
    Strategy:
      1. yfinance Search (primary) — Yahoo Finance full-text search, works for
         both company names ("Alibaba") and symbols ("BABA"), no API key needed.
      2. FMP /stable/search (secondary) — supplements when yfinance misses edge cases.
    Returns [{ticker, name, exchange, type}] deduplicated, ordered by Yahoo relevance score.
    """
    import asyncio
    q = q.strip()
    if not q:
        return []

    seen: set[str] = set()
    results: list[dict] = []

    # ── 1. yfinance Search — primary, handles name + symbol ──────────────────
    def _yf_search() -> list[dict]:
        import yfinance as yf
        out = []
        try:
            sr = yf.Search(q, max_results=limit)
            for item in (sr.quotes or []):
                sym  = (item.get("symbol") or "").strip().upper()
                name = (item.get("longname") or item.get("shortname") or sym).strip()
                exch = (item.get("exchDisp") or item.get("exchange") or "").strip()
                qtype = item.get("quoteType", "EQUITY")
                # Filter to equities and ETFs only (skip CURRENCY, FUTURE, etc.)
                if sym and qtype in ("EQUITY", "ETF") and sym not in seen:
                    seen.add(sym)
                    out.append({"ticker": sym, "name": name, "exchange": exch, "type": qtype.lower()})
        except Exception:
            pass
        return out

    try:
        results = await asyncio.to_thread(_yf_search)
    except Exception:
        pass

    # ── 2. FMP /stable/search — secondary top-up if yfinance returned few hits ─
    fmp_key = _get_fmp_key()
    if fmp_key and len(results) < 4:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://financialmodelingprep.com/stable/search",
                    params={"query": q, "limit": limit, "apikey": fmp_key},
                )
            if resp.status_code == 200:
                for item in (resp.json() or []):
                    sym  = (item.get("symbol") or "").strip().upper()
                    name = (item.get("name") or item.get("companyName") or sym).strip()
                    exch = (item.get("exchangeShortName") or item.get("exchange") or "").strip()
                    if sym and sym not in seen:
                        seen.add(sym)
                        results.append({"ticker": sym, "name": name, "exchange": exch, "type": "stock"})
        except Exception:
            pass

    return results[:limit]


# ── GET /analysis/companies (batch) ──────────────────────────────────────────

@router.get("/companies")
async def get_company_names_batch(tickers: str):
    """Return company names/sector/industry for a comma-separated list of tickers.

    Uses company_name_cache (7-day TTL) → screener caches → yfinance fallback.
    Much faster than N individual /company/{ticker} calls.
    """
    import asyncio
    from app.backend.services.screener_service import get_company_names
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {}
    result = await asyncio.to_thread(get_company_names, ticker_list)
    return result


# ── GET /analysis/company/{ticker} ───────────────────────────────────────────

@router.get("/company/{ticker}")
async def get_company_info(ticker: str):
    """Return company profile for a ticker symbol (via yfinance): name, sector, industry."""
    from src.tools.hk.ticker import is_hk_ticker, to_yfinance_code, to_akshare_code

    sym = ticker.upper()
    # HK tickers need 4-digit yfinance format (e.g. "6862.HK"), not canonical "06862.HK"
    hk = is_hk_ticker(sym)
    yf_sym = to_yfinance_code(sym) if hk else sym

    # ── Static fallback names for common HKEX stocks ──────────────────────────
    # Populated when yfinance returns no name (e.g. rate-limited or delisted check)
    _HK_NAMES: dict[str, str] = {
        "00700": "Tencent Holdings Ltd",
        "09988": "Alibaba Group Holding",
        "03690": "Meituan",
        "09618": "JD.com Inc",
        "09999": "NetEase Inc",
        "00941": "China Mobile Ltd",
        "00762": "China Unicom Hong Kong",
        "00883": "CNOOC Ltd",
        "00857": "PetroChina Co Ltd",
        "00386": "Sinopec Corp",
        "00005": "HSBC Holdings",
        "01299": "AIA Group Ltd",
        "02318": "Ping An Insurance",
        "03988": "Bank of China Ltd",
        "01398": "ICBC",
        "00939": "China Construction Bank",
        "00001": "CK Hutchison Holdings",
        "00016": "Sun Hung Kai Properties",
        "00012": "Henderson Land Development",
        "00688": "China Overseas Land",
        "01113": "CK Asset Holdings",
        "01177": "Sino Biopharmaceutical",
        "02269": "Wuxi Biologics",
        "06862": "Haidilao International",
        "00669": "Techtronic Industries",
        "00322": "Tingyi (Cayman Islands) Holding",
        "00151": "Want Want China Holdings",
        "01024": "Kuaishou Technology",
        "03750": "CATL (HK)",
        "09888": "Baidu Inc",
        "09901": "New Oriental Education",
        "06690": "Haier Smart Home",
        "02015": "Li Auto Inc",
        "09868": "XPeng Inc",
        "09866": "NIO Inc",
    }

    name: str | None = None
    sector: str | None = None
    industry: str | None = None

    try:
        import yfinance as yf
        info = yf.Ticker(yf_sym).info
        name     = info.get("longName") or info.get("shortName") or None
        sector   = info.get("sector") or None
        industry = info.get("industry") or None
    except Exception:
        pass

    # ── AKShare fallback for HK tickers (name only) ───────────────────────────
    if not name and hk:
        ak_code = to_akshare_code(sym)   # e.g. "06862"
        # 1. Static lookup first (instant, no network)
        name = _HK_NAMES.get(ak_code)
        # 2. AKShare live lookup (catches tickers not in the static dict)
        if not name:
            try:
                import akshare as ak
                df = ak.stock_hk_spot_em()
                hit = df[df["代码"] == ak_code]
                if not hit.empty:
                    raw = str(hit.iloc[0].get("名称", "") or "").strip()
                    if raw:
                        name = raw
            except Exception:
                pass

    # Avoid showing the ticker code as the "company name"
    if name and name.upper() in (sym.upper(), yf_sym.upper()):
        name = None

    return {
        "ticker":   sym,
        "name":     name or sym,
        "sector":   sector,
        "industry": industry,
    }


# ── GET /analysis/stock/{ticker} ─────────────────────────────────────────────

@router.get("/stock/{ticker}")
async def get_stock_data(ticker: str, period: str = "1y"):
    """
    Return OHLC price history and 8 financial metrics for a ticker.
    Primary source: yfinance (works for US + HK).
    HK gap-fill: AKShare indicator / growth / valuation endpoints.
    """
    import math
    import yfinance as yf
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_yfinance_code
    except ImportError:
        def is_hk_ticker(t): return False
        def to_yfinance_code(t): return t

    def _safe(v):
        """Coerce to float, replacing NaN/Inf with None."""
        if v is None:
            return None
        try:
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return None

    sym    = ticker.upper()
    hk     = is_hk_ticker(sym)
    yf_sym = to_yfinance_code(sym) if hk else sym

    try:
        t    = yf.Ticker(yf_sym)
        hist = t.history(period=period)
        info = t.info
        history = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(c, 2)}
            for idx, row in hist.iterrows()
            if (c := _safe(row.get("Close"))) is not None
        ]
        total_cash = _safe(info.get("totalCash"))
        total_debt = _safe(info.get("totalDebt"))
        net_cash   = (total_cash - total_debt) if (total_cash is not None and total_debt is not None) else None
        metrics: dict = {
            "market_cap":                 _safe(info.get("marketCap")),
            "revenue":                    _safe(info.get("totalRevenue")),
            "free_cash_flow":             _safe(info.get("freeCashflow")),
            "net_margin":                 _safe(info.get("profitMargins")),
            "pe_ratio":                   _safe(info.get("trailingPE")),
            "price_to_sales":             None,  # FMP-filled below for US tickers
            "revenue_growth":             _safe(info.get("revenueGrowth")),
            "ev_to_ebitda":               _safe(info.get("enterpriseToEbitda")),
            "return_on_equity":           _safe(info.get("returnOnEquity")),
            "return_on_assets":           _safe(info.get("returnOnAssets")),
            "return_on_invested_capital": None,  # FMP-filled below for US tickers
            "free_cash_flow_yield":       None,  # FMP-filled below for US tickers
            "total_cash":                 total_cash,
            "total_debt":                 total_debt,
            "net_cash":                   net_cash,
            "fifty_two_week_high":        _safe(info.get("fiftyTwoWeekHigh")),
            "fifty_two_week_low":         _safe(info.get("fiftyTwoWeekLow")),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── US / FMP TTM gap-fill ────────────────────────────────────────────────
    # Pulls from /stable/key-metrics-ttm + /stable/ratios-ttm in one FMP round
    # trip each. FMP TTM values are generally more current than yfinance's info
    # dict (which can lag by a quarter on enterpriseToEbitda / profitMargins).
    # For every overlapping field we PREFER FMP when both return a value.
    # Fields exclusive to FMP: priceToSalesRatioTTM, returnOnInvestedCapitalTTM,
    # freeCashFlowYieldTTM. Fields only yfinance has: revenueGrowth,
    # fiftyTwoWeekHigh/Low, totalRevenue, freeCashflow absolute.
    if not hk:
        try:
            from src.tools.api import _fmp_get, _STABLE, _get_key
            api_key = _get_key(None)
            if api_key:
                km_raw = _fmp_get(f"{_STABLE}/key-metrics-ttm", {"symbol": sym}, api_key)
                rt_raw = _fmp_get(f"{_STABLE}/ratios-ttm",      {"symbol": sym}, api_key)
                km = (km_raw[0] if isinstance(km_raw, list) and km_raw
                      else km_raw if isinstance(km_raw, dict) else {}) or {}
                rt = (rt_raw[0] if isinstance(rt_raw, list) and rt_raw
                      else rt_raw if isinstance(rt_raw, dict) else {}) or {}

                # Prefer FMP when available — else keep yfinance value already set.
                def _prefer_fmp(key: str, fmp_value):
                    fv = _safe(fmp_value)
                    if fv is not None:
                        metrics[key] = fv

                _prefer_fmp("market_cap",                 km.get("marketCap"))
                _prefer_fmp("ev_to_ebitda",               km.get("evToEBITDATTM"))
                _prefer_fmp("return_on_equity",           km.get("returnOnEquityTTM"))
                _prefer_fmp("return_on_assets",           km.get("returnOnAssetsTTM"))
                _prefer_fmp("return_on_invested_capital", km.get("returnOnInvestedCapitalTTM"))
                _prefer_fmp("free_cash_flow_yield",       km.get("freeCashFlowYieldTTM"))

                _prefer_fmp("pe_ratio",                   rt.get("priceToEarningsRatioTTM"))
                _prefer_fmp("price_to_sales",             rt.get("priceToSalesRatioTTM"))
                _prefer_fmp("net_margin",                 rt.get("netProfitMarginTTM"))
        except Exception:
            pass  # Best-effort; yfinance values remain as the baseline
    # Final fallback — ROA as a proxy for ROIC when neither FMP nor HK path filled it
    if metrics["return_on_invested_capital"] is None and metrics.get("return_on_assets") is not None:
        metrics["return_on_invested_capital"] = metrics["return_on_assets"]

    # ── HK gap-fill: AKShare fills any None values that yfinance missed ───────
    if hk:
        try:
            from src.tools.hk.ticker import to_akshare_code
            from src.tools.hk.financial_metrics import (
                _fetch_indicator, _fetch_growth, _fetch_valuation, _extract_market_cap,
            )
            from src.tools.hk._utils import _parse_float
            import akshare as ak

            ak_code   = to_akshare_code(sym)
            indicator = _fetch_indicator(ak, ak_code)
            growth    = _fetch_growth(ak, ak_code)
            valuation = _fetch_valuation(ak, ak_code)

            # Market cap (亿HKD → HKD; _extract_market_cap handles unit detection)
            if metrics["market_cap"] is None:
                metrics["market_cap"] = _extract_market_cap(indicator)
            # Revenue (raw value from AKShare is already in full units)
            if metrics["revenue"] is None:
                metrics["revenue"] = _safe(_parse_float(indicator.get("revenue")))
            # Net margin (AKShare gives %, divide by 100)
            if metrics["net_margin"] is None:
                v = _parse_float(indicator.get("net_margin"))
                metrics["net_margin"] = v / 100 if v is not None else None
            # ROE (AKShare gives %, divide by 100)
            if metrics["return_on_equity"] is None:
                v = _parse_float(indicator.get("return_on_equity"))
                metrics["return_on_equity"] = v / 100 if v is not None else None
            # P/E — valuation TTM preferred; indicator as fallback
            if metrics["pe_ratio"] is None:
                metrics["pe_ratio"] = _safe(
                    _parse_float(valuation.get("price_to_earnings_ratio"))
                    or _parse_float(indicator.get("price_to_earnings_ratio"))
                )
            # Revenue growth YoY (AKShare gives %, divide by 100)
            if metrics["revenue_growth"] is None:
                v = _parse_float(growth.get("revenue_growth"))
                metrics["revenue_growth"] = v / 100 if v is not None else None
            # EV/EBITDA not available from AKShare snapshot;
            # use P/CF TTM as the closest proxy
            if metrics["ev_to_ebitda"] is None:
                metrics["ev_to_ebitda"] = _safe(
                    _parse_float(valuation.get("price_to_cash_flow_ratio"))
                )
        except Exception:
            pass   # HK gap-fill is best-effort; silently degrade

    return {"ticker": ticker.upper(), "history": history, "metrics": metrics}


# ── GET /analysis/intelligence/{ticker} ──────────────────────────────────────

@router.get("/intelligence/{ticker}")
async def get_intelligence(ticker: str):
    """
    Compute all five intelligence-agent metrics live from FMP + yfinance.
    Returns the same field schema the pipeline agents produce so IntelligenceGrid
    renders rich data for every run (archived or fresh).

    FMP endpoints used:
      /v4/insider-trading          → Insider Activity
      /v3/earnings-surprises       → Analyst Revisions surprise streak
      /v3/analyst-estimates        → Analyst count + estimate dispersion
      /v3/stock_news               → News Sentiment (keyword scoring)
      /v3/income-statement         → Earnings Quality (accrual, cash conversion)
      /v3/cash-flow-statement      → Earnings Quality (OCF)
      /v3/balance-sheet-statement  → Earnings Quality (total assets)
    yfinance:
      ticker.info                  → Short Interest (shortPercentOfFloat, shortRatio)
    """
    import asyncio as _aio
    from datetime import datetime, timedelta, timezone

    try:
        import requests as _req
    except ImportError:
        raise HTTPException(status_code=503, detail="requests not installed")

    fmp_key = _get_fmp_key()
    if not fmp_key:
        raise HTTPException(status_code=503, detail="FMP_API_KEY not configured — add it to .env.local")

    from src.tools.hk.ticker import is_hk_ticker as _is_hk, to_yfinance_code as _to_yf

    sym = ticker.strip().upper()
    # yfinance needs 4-digit format for HK tickers (e.g. "0700.HK" not "00700.HK")
    yf_sym = _to_yf(sym) if _is_hk(sym) else sym

    # ── helper: fire FMP call in thread ──────────────────────────────────────
    def _fmp(path: str, **params) -> list | dict:
        # FMP has no data for HK-listed stocks — skip all FMP calls silently
        if _is_hk(sym):
            return []
        params["apikey"] = fmp_key
        url = f"https://financialmodelingprep.com{path}"
        try:
            r = _req.get(url, params=params, timeout=12)
            r.raise_for_status()
            return r.json()
        except Exception:
            return []

    # ── helper: yfinance short interest + earnings history in one thread ─────
    def _yf_all() -> dict:
        try:
            import yfinance as yf
            import pandas as pd
            t = yf.Ticker(yf_sym)
            info = t.info
            sf = info.get("shortPercentOfFloat")
            sr = info.get("shortRatio")           # days-to-cover
            # Earnings history — used as fallback when FMP has no EPS data (e.g. foreign ADRs)
            earnings_hist: list = []
            try:
                df = t.earnings_dates
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        try:
                            est = row.get("EPS Estimate")
                            act = row.get("Reported EPS")
                            if est is None or act is None:
                                continue
                            if pd.isna(est) or pd.isna(act):
                                continue
                            earnings_hist.append({"epsActual": float(act), "epsEstimated": float(est)})
                        except Exception:
                            continue
            except Exception:
                pass
            return {
                "short_float_pct":  round(float(sf) * 100, 2) if sf else None,
                "days_to_cover":    round(float(sr), 1) if sr else None,
                "borrow_rate_pct":  None,          # not in yfinance .info
                "earnings_hist":    earnings_hist[:8],
            }
        except Exception:
            return {"short_float_pct": None, "days_to_cover": None,
                    "borrow_rate_pct": None, "earnings_hist": []}

    # ── fire all calls concurrently (8 FMP stable + 1 yfinance) ─────────────
    (
        insider_raw,
        surprises_raw,
        estimates_raw,
        analyst_est_raw,
        news_raw,
        income_raw,
        cf_raw,
        bs_raw,
        yf_raw,
    ) = await _aio.gather(
        _aio.to_thread(_fmp, "/stable/insider-trading/search", symbol=sym, limit=40),
        _aio.to_thread(_fmp, "/stable/earnings",               symbol=sym, limit=8),
        _aio.to_thread(_fmp, "/stable/price-target-consensus", symbol=sym),
        _aio.to_thread(_fmp, "/stable/analyst-estimates",      symbol=sym, limit=4),
        _aio.to_thread(_fmp, "/stable/news/stock",             tickers=sym, limit=30),
        _aio.to_thread(_fmp, "/stable/income-statement",       symbol=sym, period="annual", limit=4),
        _aio.to_thread(_fmp, "/stable/cash-flow-statement",    symbol=sym, period="annual", limit=4),
        _aio.to_thread(_fmp, "/stable/balance-sheet-statement",symbol=sym, period="annual", limit=4),
        _aio.to_thread(_yf_all),
    )

    now = datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. INSIDER ACTIVITY
    # ─────────────────────────────────────────────────────────────────────────
    BUY_TYPES  = {"P", "P-Purchase", "Buy", "Purchase"}
    SELL_TYPES = {"S", "S-Sale", "Sell", "Sale"}

    net_30d = net_90d = net_12m = 0.0
    buy_val_12m = sell_val_12m = 0.0
    buyers_30d: set = set()
    key_txns: list = []

    for t in (insider_raw if isinstance(insider_raw, list) else []):
        raw_date = t.get("transactionDate") or t.get("filingDate") or ""
        try:
            txn_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if txn_dt.tzinfo is None:
                txn_dt = txn_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        t_type = str(t.get("transactionType", ""))
        shares = float(t.get("securitiesTransacted") or 0)
        price  = float(t.get("price") or 0)
        value  = shares * price
        is_buy  = t_type in BUY_TYPES
        is_sell = t_type in SELL_TYPES

        days_ago = (now - txn_dt).days
        if days_ago <= 365:
            if is_buy:  buy_val_12m  += value
            if is_sell: sell_val_12m += value
        if days_ago <= 90:
            net_90d += value if is_buy else -value if is_sell else 0
        if days_ago <= 30:
            net_30d += value if is_buy else -value if is_sell else 0
            if is_buy:
                buyers_30d.add(t.get("reportingName", ""))
        if value > 0:
            key_txns.append({
                "name": t.get("reportingName", ""),
                "type": "BUY" if is_buy else "SELL" if is_sell else t_type,
                "value": round(value),
                "date":  raw_date[:10],
                "price": price,
            })

    net_12m = buy_val_12m - sell_val_12m
    bsr = round(buy_val_12m / sell_val_12m, 2) if sell_val_12m > 0 else (None if buy_val_12m == 0 else 99.0)
    cluster_buy = len(buyers_30d) >= 2
    conviction_sell = any(
        t.get("securitiesTransacted", 0) * t.get("price", 0) >= 5_000_000
        and str(t.get("transactionType", "")) in SELL_TYPES
        for t in (insider_raw if isinstance(insider_raw, list) else [])
    )
    if net_30d > 0 and cluster_buy:
        ia_signal = "BULLISH"
    elif net_30d < -200_000 or conviction_sell:
        ia_signal = "BEARISH"
    elif net_12m > 0:
        ia_signal = "BULLISH"
    elif net_12m < 0:
        ia_signal = "BEARISH"
    else:
        ia_signal = "NEUTRAL"

    key_txns_sorted = sorted(key_txns, key=lambda x: -x["value"])[:5]

    insider_activity = {
        "signal":               ia_signal,
        "net_buying_30d_usd":   round(net_30d),
        "net_buying_90d_usd":   round(net_90d),
        "net_buying_12m_usd":   round(net_12m),
        "buy_sell_ratio_12m":   bsr,
        "cluster_buy":          cluster_buy,
        "conviction_sell_flag": conviction_sell,
        "key_transactions":     key_txns_sorted,
        "data_source":          "FMP",
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. ANALYST REVISIONS
    # ─────────────────────────────────────────────────────────────────────────
    # /stable/earnings field names: epsActual / epsEstimated
    # Legacy /v3/earnings-surprises used: actualEarningResult / estimatedEarning
    # yfinance earnings_dates used as fallback for foreign ADRs with no FMP EPS data
    fmp_surp = surprises_raw if isinstance(surprises_raw, list) else []
    yf_surp  = yf_raw.get("earnings_hist", []) if isinstance(yf_raw, dict) else []
    # prefer FMP data; fall back to yfinance when FMP records lack epsActual
    fmp_has_eps = any(
        s.get("epsActual") is not None or s.get("actualEarningResult") is not None
        for s in fmp_surp
    )
    surp_list = fmp_surp if fmp_has_eps else (yf_surp or fmp_surp)
    streak = 0
    beats = misses = 0
    for s in surp_list[:8]:
        actual = s.get("epsActual") or s.get("actualEarningResult")
        est    = s.get("epsEstimated") or s.get("estimatedEarning")
        if actual is None or est is None:
            break
        is_beat = float(actual) >= float(est)
        if is_beat:
            beats += 1
        else:
            misses += 1
        if streak == 0:
            streak = 1 if is_beat else -1
        elif (streak > 0 and is_beat) or (streak < 0 and not is_beat):
            streak += (1 if streak > 0 else -1)
        else:
            break  # streak broken

    if beats > misses:
        surp_dir = "BEAT"
    elif misses > beats:
        surp_dir = "MISS"
    elif beats == 0 and misses == 0:
        surp_dir = "UNKNOWN"
    else:
        surp_dir = "MIXED"

    # Analyst estimates — primary: /stable/analyst-estimates (has EPS + revenue ranges)
    # Fallback dispersion: /stable/price-target-consensus (targetHigh/Low/Consensus)
    ae_list  = analyst_est_raw if isinstance(analyst_est_raw, list) else []
    pt_list  = estimates_raw   if isinstance(estimates_raw,   list) else []
    analyst_count  = 0
    eps_dispersion = None
    rev_dispersion = None

    if ae_list and isinstance(ae_list[0], dict):
        ae = ae_list[0]
        # Analyst count
        analyst_count = int(
            ae.get("numberAnalystsEstimatedEps") or
            ae.get("numberAnalystsEstimatedRevenue") or 0
        )
        # EPS dispersion from analyst-estimates
        eps_avg = float(ae.get("estimatedEpsAvg")  or 0)
        eps_hi  = float(ae.get("estimatedEpsHigh") or 0)
        eps_lo  = float(ae.get("estimatedEpsLow")  or 0)
        if eps_avg != 0 and eps_hi and eps_lo:
            eps_dispersion = round((eps_hi - eps_lo) / abs(eps_avg) * 100, 1)
        # Revenue dispersion
        rev_avg = float(ae.get("estimatedRevenueAvg")  or 0)
        rev_hi  = float(ae.get("estimatedRevenueHigh") or 0)
        rev_lo  = float(ae.get("estimatedRevenueLow")  or 0)
        if rev_avg != 0 and rev_hi and rev_lo:
            rev_dispersion = round((rev_hi - rev_lo) / abs(rev_avg) * 100, 1)

    # If analyst-estimates had no EPS range, fall back to price-target-consensus spread
    if eps_dispersion is None and pt_list and isinstance(pt_list[0], dict):
        pt = pt_list[0]
        pt_hi  = float(pt.get("targetHigh")      or 0)
        pt_lo  = float(pt.get("targetLow")        or 0)
        pt_avg = float(pt.get("targetConsensus")  or 0)
        if pt_avg != 0 and pt_hi and pt_lo:
            eps_dispersion = round((pt_hi - pt_lo) / abs(pt_avg) * 100, 1)

    if eps_dispersion is None:
        est_disp_str = "UNKNOWN"
    elif eps_dispersion < 10:
        est_disp_str = "LOW"
    elif eps_dispersion < 25:
        est_disp_str = "MEDIUM"
    else:
        est_disp_str = "HIGH"

    if streak >= 3:
        rev_dir = "ACCELERATING_UP"
    elif streak == 2:
        rev_dir = "STABLE"
    elif streak == 1:
        rev_dir = "STABLE"
    elif streak <= -2:
        rev_dir = "ACCELERATING_DOWN"
    else:
        rev_dir = "UNKNOWN"

    analyst_revisions = {
        "revision_direction":      rev_dir,
        "surprise_streak":         streak,
        "surprise_direction":      surp_dir,
        "estimate_dispersion":     est_disp_str,
        "eps_dispersion_pct":      eps_dispersion,
        "revenue_dispersion_pct":  rev_dispersion,
        "analyst_count":           analyst_count,
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 3. NEWS SENTIMENT  (keyword scoring on titles + text)
    # ─────────────────────────────────────────────────────────────────────────
    BULLISH_KW = {"beat", "surge", "record", "strong", "upgrade", "outperform",
                  "growth", "profit", "expand", "raise", "buy", "positive", "gain",
                  "rise", "exceed", "accelerat"}
    BEARISH_KW = {"miss", "decline", "fall", "concern", "risk", "downgrade",
                  "underperform", "weak", "loss", "cut", "disappoint", "warning",
                  "drop", "slump", "below", "slow"}

    articles = news_raw if isinstance(news_raw, list) else []
    bullish_n = bearish_n = neutral_n = pr_n = 0
    scores: list[float] = []
    headlines: list[str] = []

    for a in articles:
        text = f"{a.get('title','')} {a.get('text','')[:200]}".lower()
        b = sum(1 for kw in BULLISH_KW if kw in text)
        be = sum(1 for kw in BEARISH_KW if kw in text)
        score = (b - be) / max(b + be, 1)
        scores.append(score)
        if b > be:
            bullish_n += 1
        elif be > b:
            bearish_n += 1
        else:
            neutral_n += 1
        site = (a.get("site") or "").lower()
        if any(x in site for x in ["prnewswire", "businesswire", "globenewswire", "ir.", "investor"]):
            pr_n += 1
        if a.get("title"):
            headlines.append(a["title"])

    composite = round(sum(scores) / len(scores), 3) if scores else 0.0
    if composite > 0.1:
        ns_signal = "BULLISH"
    elif composite < -0.1:
        ns_signal = "BEARISH"
    else:
        ns_signal = "NEUTRAL"

    news_sentiment = {
        "signal":               ns_signal,
        "composite_score":      composite,
        "article_count":        len(articles),
        "bullish_count":        bullish_n,
        "bearish_count":        bearish_n,
        "neutral_count":        neutral_n,
        "press_release_count":  pr_n,
        "volume_spike":         len(articles) > 20,
        "top_headlines":        headlines[:5],
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 4. EARNINGS QUALITY
    # ─────────────────────────────────────────────────────────────────────────
    is_list = income_raw if isinstance(income_raw, list) else []
    cf_list = cf_raw if isinstance(cf_raw, list) else []
    bs_list = bs_raw if isinstance(bs_raw, list) else []

    accrual_ratios: list[float] = []
    ccr_list: list[float] = []
    fcf_ni_list: list[float] = []

    n = min(len(is_list), len(cf_list), len(bs_list), 3)
    for i in range(n):
        ni   = float(is_list[i].get("netIncome") or 0)
        ocf  = float(cf_list[i].get("operatingCashFlow") or 0)
        fcf  = float(cf_list[i].get("freeCashFlow") or 0)
        ta   = float(bs_list[i].get("totalAssets") or 1)
        if ta:
            accrual_ratios.append((ni - ocf) / ta)
        if ni and ni != 0:
            ccr_list.append(ocf / ni)
            fcf_ni_list.append(fcf / ni)

    avg_accrual  = sum(accrual_ratios) / len(accrual_ratios) if accrual_ratios else None
    avg_ccr      = sum(ccr_list) / len(ccr_list) if ccr_list else None
    avg_fcf_ni   = sum(fcf_ni_list) / len(fcf_ni_list) if fcf_ni_list else None

    # Accrual flag: lower (more negative) is better (OCF > NI)
    if avg_accrual is None:
        accrual_flag = "UNKNOWN"
    elif avg_accrual < -0.03:
        accrual_flag = "GREEN"    # OCF consistently exceeds NI
    elif avg_accrual < 0.05:
        accrual_flag = "AMBER"
    else:
        accrual_flag = "RED"

    # Cash conversion ratio: OCF / NI — want ≥ 0.9
    if avg_ccr is None:
        ccr_flag = "UNKNOWN"
    elif avg_ccr >= 0.9:
        ccr_flag = "GREEN"
    elif avg_ccr >= 0.6:
        ccr_flag = "AMBER"
    else:
        ccr_flag = "RED"

    # FCF / NI alignment
    if avg_fcf_ni is None:
        fcf_ni_flag = "UNKNOWN"
    elif avg_fcf_ni >= 0.7:
        fcf_ni_flag = "GREEN"
    elif avg_fcf_ni >= 0.4:
        fcf_ni_flag = "AMBER"
    else:
        fcf_ni_flag = "RED"

    # SBC drag from latest income statement
    sbc_drag = None
    sbc_flag = "UNKNOWN"
    if is_list and cf_list:
        rev = float(is_list[0].get("revenue") or 0)
        sbc = float(cf_list[0].get("stockBasedCompensation") or 0)
        if rev > 0:
            sbc_drag = round(sbc / rev * 100, 1)
            sbc_flag = "HIGH" if sbc_drag > 10 else "MEDIUM" if sbc_drag > 5 else "LOW"

    # Composite quality score
    score_map = {"GREEN": 10, "AMBER": 5, "RED": 0, "UNKNOWN": 5}
    flags_vals = [score_map[accrual_flag], score_map[ccr_flag], score_map[fcf_ni_flag]]
    eq_score = round(sum(flags_vals) / len(flags_vals) / 10 * 10, 1) if flags_vals else 5.0

    if eq_score >= 8:
        eq_verdict = "HIGH"
    elif eq_score >= 5:
        eq_verdict = "MEDIUM"
    else:
        eq_verdict = "LOW"

    earnings_quality = {
        "quality_verdict":       eq_verdict,
        "overall_quality_score": eq_score,
        "accrual_flag":          accrual_flag,
        "accrual_trend":         "IMPROVING" if len(accrual_ratios) >= 2 and accrual_ratios[-1] < accrual_ratios[0] else "STABLE",
        "cash_conversion_flag":  ccr_flag,
        "cash_conversion_ratio": round(avg_ccr, 2) if avg_ccr is not None else None,
        "fcf_ni_divergence":     fcf_ni_flag,
        "sbc_drag_pct":          sbc_drag,
        "sbc_drag_flag":         sbc_flag,
        "pre_earnings_risk":     "LOW" if accrual_flag == "GREEN" and ccr_flag == "GREEN" else "HIGH" if accrual_flag == "RED" else "MEDIUM",
        "data_quality":          "FULL" if n >= 3 else "PARTIAL" if n >= 1 else "INSUFFICIENT",
        "flags":                 ([f"Accrual ratio elevated ({avg_accrual:.3f})"] if avg_accrual and avg_accrual > 0.05 else []) +
                                 ([f"Cash conversion weak ({avg_ccr:.2f}x)"] if avg_ccr and avg_ccr < 0.6 else []),
    }

    # ─────────────────────────────────────────────────────────────────────────
    # 5. SHORT INTEREST  (yfinance — already fetched concurrently above)
    # ─────────────────────────────────────────────────────────────────────────
    yf_si  = yf_raw if isinstance(yf_raw, dict) else {}
    sf_pct = yf_si.get("short_float_pct")
    dtc    = yf_si.get("days_to_cover")

    if sf_pct is None:
        si_signal = "UNKNOWN"
    elif sf_pct > 20:
        si_signal = "HEAVILY_SHORTED"
    elif sf_pct > 10:
        si_signal = "MODERATELY_SHORTED"
    else:
        si_signal = "LOW_SHORT_INTEREST"

    squeeze_risk = (sf_pct or 0) > 20 and (dtc or 0) > 7
    crowded      = (sf_pct or 0) > 15

    short_interest = {
        "signal":               si_signal,
        "short_float_pct":      sf_pct,
        "days_to_cover":        dtc,
        "borrow_rate_pct":      yf_si.get("borrow_rate_pct"),
        "short_interest_trend": None,         # historical SI trend requires premium data
        "squeeze_risk":         squeeze_risk,
        "crowded_trade":        crowded,
        "short_float_flag":     "HIGH" if (sf_pct or 0) > 20 else "MEDIUM" if (sf_pct or 0) > 10 else "LOW",
    }

    return {
        "ticker":            sym,
        "insider_activity":  insider_activity,
        "analyst_revisions": analyst_revisions,
        "news_sentiment":    news_sentiment,
        "earnings_quality":  earnings_quality,
        "short_interest":    short_interest,
    }


# ── GET /analysis/news/{ticker} ──────────────────────────────────────────────

@router.get("/news/{ticker}")
async def get_news(ticker: str, limit: int = 8):
    """
    Fetch latest news articles for a ticker from FMP /stable/news/stock.
    Filters to authoritative financial sources (Bloomberg, FT, Reuters, WSJ, etc.).
    Fetches 3× the requested limit so filtering still returns enough results.
    Returns list of {title, text, url, publishedDate, site, image, symbol}.
    """
    try:
        import requests as _req
    except ImportError:
        raise HTTPException(status_code=503, detail="requests package not installed")

    fmp_key = _get_fmp_key()
    if not fmp_key:
        raise HTTPException(status_code=503, detail="FMP_API_KEY not configured — add it to .env.local")

    # Authoritative financial sources — exact domain fragments (lowercase)
    AUTHORITATIVE = {
        "bloomberg.com", "ft.com", "reuters.com", "wsj.com", "barrons.com",
        "cnbc.com", "marketwatch.com", "seekingalpha.com", "thestreet.com",
        "investopedia.com", "morningstar.com",
        "businessinsider.com", "forbes.com", "nytimes.com", "economist.com",
        "financialtimes.com", "ap.org", "apnews.com",
        "nasdaq.com", "nyse.com",
        "prnewswire.com", "businesswire.com", "globenewswire.com",  # official IR wires
        "sec.gov", "ir.", "investor.",                               # company IR pages
    }
    # Noise sources to always exclude
    EXCLUDED = {
        "fool.com", "motleyfool.com", "zacks.com", "benzinga.com",
        "stockanalysis.com", "stockcharts.com", "barchart.com",
        "tipranks.com", "finviz.com",
    }

    def _is_authoritative(site: str) -> bool:
        s = (site or "").lower()
        if any(ex in s for ex in EXCLUDED):
            return False
        return any(auth in s for auth in AUTHORITATIVE if auth)

    import re as _re
    sym = ticker.strip().upper()

    # Ticker word-boundary pattern  e.g. r'\bBABA\b'
    _sym_pat = _re.compile(r'\b' + _re.escape(sym) + r'\b', _re.IGNORECASE)

    # ADR / foreign-listed stocks: ticker ≠ company name in headlines.
    # Map ticker → lowercase name fragments that unambiguously identify the company.
    _ALIASES: dict[str, list[str]] = {
        "BABA":  ["alibaba"],
        "CHA":   ["china telecom"],
        "CHU":   ["china unicom"],
        "CHL":   ["china mobile"],
        "BIDU":  ["baidu"],
        "JD":    ["jd.com", "jingdong"],
        "NIO":   ["nio inc"],
        "TCEHY": ["tencent"],
        "XPEV":  ["xpeng"],
        "LI":    ["li auto"],
        "PDD":   ["pinduoduo", "temu"],
        "NTES":  ["netease"],
        "WB":    ["weibo"],
        "VIPS":  ["vipshop"],
        "TME":   ["tencent music"],
        "FUTU":  ["futu holdings"],
        "TIGR":  ["up fintech"],
        "SAP":   ["sap se"],
        "TSM":   ["tsmc", "taiwan semiconductor"],
        "ASML":  ["asml"],
        "SHOP":  ["shopify"],
        "SE":    ["sea limited", "sea group"],
    }
    _aliases = _ALIASES.get(sym, [])

    # Use FMP's dedicated Search Stock News endpoint:
    # GET /stable/news/stock?symbols={sym}  (symbols is required)
    # Supports: symbols, from, to, page, limit (max 250, page max 100)
    # The response `symbol` field per article lets us verify relevance.
    # Only fetch articles from the last 7 days; start with a 2-day window and
    # widen to 7 days so we always have enough articles after source filtering.
    from datetime import datetime, timedelta
    _from_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    _to_date   = datetime.now().strftime("%Y-%m-%d")

    def _fetch_stock_news(page: int) -> list:
        url = (
            f"https://financialmodelingprep.com/stable/news/stock"
            f"?symbols={sym}&page={page}&limit=50"
            f"&from={_from_date}&to={_to_date}&apikey={fmp_key}"
        )
        try:
            r = _req.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # Fetch pages 0 and 1 concurrently — 100 articles, all pre-filtered by FMP to this ticker
    pages = await asyncio.gather(
        asyncio.to_thread(_fetch_stock_news, 0),
        asyncio.to_thread(_fetch_stock_news, 1),
    )
    raw: list = [article for page_data in pages for article in page_data]

    # Tiered fallback: widen date window progressively so low-coverage tickers
    # still surface something. 2d → 7d → 30d; empty list is valid, not an error.
    for _fallback_days in (7, 30):
        if raw:
            break
        _from_wide = (datetime.now() - timedelta(days=_fallback_days)).strftime("%Y-%m-%d")

        def _fetch_wide(page: int, _from=_from_wide) -> list:
            url = (
                f"https://financialmodelingprep.com/stable/news/stock"
                f"?symbols={sym}&page={page}&limit=50"
                f"&from={_from}&to={_to_date}&apikey={fmp_key}"
            )
            try:
                r = _req.get(url, timeout=15)
                r.raise_for_status()
                data = r.json()
                return data if isinstance(data, list) else []
            except Exception:
                return []

        pages_wide = await asyncio.gather(
            asyncio.to_thread(_fetch_wide, 0),
            asyncio.to_thread(_fetch_wide, 1),
        )
        raw = [article for page_data in pages_wide for article in page_data]

    # No news at all — return empty list gracefully (not a server error)
    if not raw:
        return {"ticker": sym, "articles": []}

    # Verify relevance using the symbol field + alias match (removes any FMP false-positives)
    def _is_ticker_relevant(a: dict) -> bool:
        article_sym = str(a.get("symbol") or "").upper()
        tagged = {s.strip() for s in article_sym.split(",")}
        if sym in tagged:
            return True
        title = a.get("title") or ""
        if _sym_pat.search(title):
            return True
        title_lower = title.lower()
        return any(alias in title_lower for alias in _aliases)

    relevant = [a for a in raw if _is_ticker_relevant(a)]
    # If FMP returned articles but none matched our strict check, trust FMP's pre-filter
    if not relevant:
        relevant = raw

    # Authoritative source filter; fall back to all relevant if none qualify
    filtered = [a for a in relevant if _is_authoritative(a.get("site", ""))]
    if not filtered:
        filtered = relevant   # keep ticker-specific articles even from minor sources

    articles = [
        {
            "title":         a.get("title", ""),
            "text":          (a.get("text") or "")[:300],
            "url":           a.get("url", ""),
            "publishedDate": a.get("publishedDate", ""),
            "site":          a.get("site", ""),
            "image":         a.get("image", ""),
            "symbol":        a.get("symbol", sym),
        }
        for a in filtered[:limit]
    ]

    return {"ticker": sym, "articles": articles}


# ── GET /analysis/financials/{ticker} ────────────────────────────────────────

@router.get("/financials/{ticker}")
async def get_financials(ticker: str, period: str = "annual"):
    """
    Fetch income-statement time-series.
    HK tickers (numeric, e.g. "06862" or "06862.HK") → AKShare via search_hk_line_items.
    All others → FMP API.
    period: "annual"  → last 5 fiscal years
            "quarter" → last 20 quarters (~5 years)
    Returns items sorted oldest→newest with:
      date, period_label, revenue, net_income, operating_income
    """
    from datetime import datetime as _dt

    sym = ticker.strip().upper()

    # ── HK ticker path ────────────────────────────────────────────────────────
    try:
        from src.tools.hk.ticker import is_hk_ticker, to_canonical
        _hk = is_hk_ticker(sym)
    except Exception:
        _hk = False

    if _hk:
        try:
            from src.tools.hk.line_items import search_hk_line_items
            canonical   = to_canonical(sym)
            period_arg  = "quarterly" if period == "quarter" else "annual"
            limit       = 20 if period == "quarter" else 5
            rows = await asyncio.to_thread(
                search_hk_line_items,
                canonical,
                ["revenue", "operating_income", "net_income"],
                end_date=_dt.now().strftime("%Y-%m-%d"),
                period=period_arg,
                limit=limit,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"AKShare financials error: {exc}")

        items = []
        for li in rows:
            report_period = getattr(li, "report_period", None) or ""
            # Build a readable label: "FY2024" or "Q3 2024"
            if period == "quarter":
                # report_period is typically "2024-09-30"
                try:
                    d = _dt.strptime(report_period[:10], "%Y-%m-%d")
                    q = (d.month - 1) // 3 + 1
                    label = f"Q{q} {d.year}"
                except Exception:
                    label = report_period[:10]
            else:
                year = report_period[:4] if report_period else "—"
                label = f"FY{year}"

            items.append({
                "date":             report_period[:10],
                "period_label":     label,
                "revenue":          getattr(li, "revenue", None),
                "net_income":       getattr(li, "net_income", None),
                "operating_income": getattr(li, "operating_income", None),
            })

        # Sort oldest → newest
        items.sort(key=lambda x: x["date"])
        return {"ticker": sym, "period_type": period, "items": items}

    # ── US / non-HK path (FMP) ────────────────────────────────────────────────
    try:
        import requests as _req
    except ImportError:
        raise HTTPException(status_code=503, detail="requests package not installed")

    fmp_key = _get_fmp_key()
    if not fmp_key:
        raise HTTPException(status_code=503, detail="FMP_API_KEY not configured — add it to .env.local")

    base = "https://financialmodelingprep.com/stable/income-statement"
    if period == "quarter":
        url = f"{base}?symbol={sym}&period=quarter&limit=20&apikey={fmp_key}"
    else:
        url = f"{base}?symbol={sym}&limit=5&apikey={fmp_key}"

    import time as _time

    # Retry with backoff on 429 (FMP rate limit) — backfill may be consuming quota
    raw = None
    last_exc = None
    for attempt in range(3):
        try:
            resp = await asyncio.to_thread(lambda: _req.get(url, timeout=15))
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning("FMP 429 on income-statement for %s, retry %d in %ds", sym, attempt + 1, wait)
                await asyncio.to_thread(lambda: _time.sleep(wait))
                continue
            resp.raise_for_status()
            raw = resp.json()
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                await asyncio.to_thread(lambda: _time.sleep(3))
    if raw is None:
        raise HTTPException(status_code=500, detail=str(last_exc or "FMP rate limited after retries"))

    if not isinstance(raw, list):
        raise HTTPException(status_code=502, detail="Unexpected FMP response")

    # Sort oldest → newest
    raw_sorted = sorted(raw, key=lambda x: x.get("date", ""))

    items = []
    for row in raw_sorted:
        cal_year = row.get("fiscalYear", "") or row.get("calendarYear", "")
        per      = row.get("period", "FY")
        if per == "FY":
            label = f"FY{cal_year}"
        else:
            label = f"{per} {cal_year}"

        items.append({
            "date":             row.get("date", ""),
            "period_label":     label,
            "revenue":          row.get("revenue"),
            "net_income":       row.get("netIncome"),
            "operating_income": row.get("operatingIncome"),
        })

    return {"ticker": sym, "period_type": period, "items": items}


# ── GET /analysis/summary ─────────────────────────────────────────────────────

@router.get("/summary")
async def get_summary():
    try:
        return await asyncio.to_thread(analysis_service.get_archive_summary)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_archive_summary failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


# ── POST /analysis/research-summary ──────────────────────────────────────────

class ResearchSummaryRequest(BaseModel if False else object):
    pass

from pydantic import BaseModel as _BaseModel

class _ResearchSummaryReq(_BaseModel):
    run_id: str
    ticker: str
    industry_brief: Optional[str] = None
    deep_research: Optional[str] = None

_RESEARCH_SUMMARY_PROMPT = """You are a senior equity analyst covering U.S. financials. Summarize the research section from deep research and industry intelligence brief into 200 words across four dimensions.

Output format rules (strictly follow):
- Each dimension must start with its label on its own line (e.g. "Industry Structure"), followed by 1-3 bullet points starting with "•"
- Do NOT use markdown bold (**), asterisks, or any special characters for headers
- Write in complete, professional sentences
- Separate each section with a blank line
- Total output must not exceed 200 words

Dimensions to cover:
1. Industry Structure — moat durability, competitive dynamics, 3-year tailwinds/headwinds
2. Corporate Developments — board changes, capital accretion, DU/AI platform progress
3. Growth Potential — guaranteed book expansion, g-fee revenue growth, origination cycle recovery; quantify where possible
4. Key Risks — top 2-3 KPI thresholds that would invalidate the thesis

Tone: precise, evidence-based, investment-grade. Written for a portfolio manager forming a view in under 5 minutes.

---
INDUSTRY INTELLIGENCE BRIEF:
{industry_brief}

---
DEEP RESEARCH:
{deep_research}
"""

@router.post("/research-summary")
async def post_research_summary(body: _ResearchSummaryReq, db: Session = Depends(get_db)):
    """Generate a 200-word analyst summary from industry brief + deep research via Qwen.
    Result is cached in DB by run_id — subsequent calls for the same run return instantly."""
    from app.backend.database.models import ResearchSummaryCache

    # ── Reject empty run_id — prevents caching a stale summary that would be
    # served to every subsequent streaming session (all share run_id='').
    if not body.run_id or not body.run_id.strip():
        raise HTTPException(status_code=400, detail="run_id is required")

    # ── Cache hit ──────────────────────────────────────────────────────────────
    cached = db.query(ResearchSummaryCache).filter(
        ResearchSummaryCache.run_id == body.run_id
    ).first()
    if cached:
        return {"summary": cached.summary, "cached": True}

    # ── Generate via Qwen ──────────────────────────────────────────────────────
    industry_brief = (body.industry_brief or "").strip()
    deep_research   = (body.deep_research  or "").strip()

    if not industry_brief and not deep_research:
        raise HTTPException(status_code=400, detail="No source content provided")

    # ── Cross-contamination guard ─────────────────────────────────────────────
    # Prevent caching another ticker's research under the wrong run_id.
    # The frontend may send stale props during navigation transitions.
    _ticker_upper = body.ticker.upper() if body.ticker else ""
    _content_sample = (industry_brief + " " + deep_research)[:2000].upper()
    if _ticker_upper and _ticker_upper not in _content_sample:
        # Content doesn't mention the ticker — likely stale props from previous report
        raise HTTPException(
            status_code=400,
            detail=f"Content does not reference {_ticker_upper} — possible cross-contamination"
        )

    prompt = _RESEARCH_SUMMARY_PROMPT.format(
        industry_brief=industry_brief[:8000] or "(not available)",
        deep_research=deep_research[:8000]   or "(not available)",
    )

    api_key  = os.environ.get("DEEP_RESEARCH_API_KEY", "")
    base_url = os.environ.get("DEEP_RESEARCH_SEARCH_BASE_URL",
                              "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    model    = os.environ.get("QWEN_MODEL", "qwen-plus")

    if not api_key:
        raise HTTPException(status_code=503, detail="DEEP_RESEARCH_API_KEY not configured")

    import httpx as _httpx

    async def _call_qwen() -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0.3,
        }
        # Qwen 3.x thinking models can spend a long time on reasoning_content
        # that we don't need for a short summary — disable it via the
        # DashScope OpenAI-compatible API parameter.
        if "qwen3" in model.lower():
            payload["enable_thinking"] = False

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                async with _httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(f"{base_url}/chat/completions",
                                          headers=headers, json=payload)
                    if r.status_code >= 400:
                        body_text = r.text[:500]
                        logger.error(
                            "research-summary Qwen HTTP %s (attempt %d): %s",
                            r.status_code, attempt + 1, body_text,
                        )
                        r.raise_for_status()
                    resp = r.json()
                    content = resp["choices"][0]["message"]["content"]
                    if content is None:
                        # Thinking models may return content=null; fall back to reasoning
                        content = resp["choices"][0]["message"].get("reasoning_content", "")
                    return (content or "").strip()
            except _httpx.TimeoutException as exc:
                last_err = exc
                logger.warning("research-summary Qwen timeout (attempt %d)", attempt + 1)
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2 * (attempt + 1))
            except _httpx.HTTPStatusError as exc:
                last_err = exc
                if attempt < 2 and exc.response.status_code in (429, 500, 502, 503):
                    import asyncio
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    raise
        raise last_err or RuntimeError("Qwen call failed after retries")

    try:
        summary = await _call_qwen()
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        logger.error("research-summary Qwen call failed: %s", detail)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {detail}")

    # ── Persist to DB ──────────────────────────────────────────────────────────
    try:
        row = ResearchSummaryCache(
            run_id=body.run_id,
            ticker=body.ticker,
            summary=summary,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        logger.warning("Failed to cache research summary: %s", exc)
        db.rollback()

    return {"summary": summary, "cached": False}
