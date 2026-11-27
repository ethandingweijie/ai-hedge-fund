"""
app/backend/routes/screener.py
================================
Stock screener endpoint — fetches FMP candidates + joins with internal VGPM data.
"""
import asyncio
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.backend.request_guards import internal_error
from app.backend.services import screener_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stocks")
async def get_screener_stocks(
    sector: Optional[str] = Query(None, description="FMP sector name e.g. Technology"),
    exchange: Optional[str] = Query(None, description="Exchange e.g. NASDAQ, NYSE"),
    country: str = Query("US"),
    marketCapMin: Optional[int] = Query(None, description="Minimum market cap in USD"),
    marketCapMax: Optional[int] = Query(None, description="Maximum market cap in USD"),
    limit: int = Query(100, ge=1, le=500, description="Page size"),
    offset: Optional[int] = Query(None, ge=0, description="Rows to skip (paging)"),
    page: Optional[int] = Query(None, ge=1, description="1-based page; offset = (page-1)*limit"),
    sort: Optional[str] = Query(None, description="score (default) | marketCap | price | change | symbol"),
    order: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    refresh: bool = Query(False, description="Force fresh FMP fetch, bypassing 24h cache"),
    live: bool = Query(True, description="Overlay live quotes (price / day change) on the page"),
):
    """One page of the US screener. Every filter, the sort and the page apply
    to the merged universe: `total` counts all matches and `has_more` says
    whether another page exists. Without offset/page this is page 1, so old
    callers see the same `items` / `total` / `cached` keys as before."""
    try:
        screener_service.normalize_sort(sort)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if offset is None:
        offset = (page - 1) * limit if page else 0
    try:
        # Run in thread so blocking FMP/SQLite calls don't stall the event loop.
        return await asyncio.to_thread(
            screener_service.get_screener_stocks,
            sector=sector,
            exchange=exchange,
            country=country,
            market_cap_more_than=marketCapMin,
            market_cap_lower_than=marketCapMax,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
            live=live,
            force_refresh=refresh,
        )
    except Exception:
        raise internal_error(logger, "get_screener_stocks")


def _hk_result(refresh: bool) -> dict:
    result = screener_service.get_hk_screener_stocks(force_refresh=refresh)
    # Live price + day change on every row (one FMP call per exchange).
    result = screener_service.overlay_exchange_quotes(result, "HKSE")
    return screener_service.annotate_currency(result, "HK")


def _sg_result(refresh: bool) -> dict:
    result = screener_service.get_sg_screener_stocks(force_refresh=refresh)
    result = screener_service.overlay_exchange_quotes(result, "SES")
    return screener_service.annotate_currency(result, "SG")


@router.get("/hk-stocks")
async def get_hk_screener_stocks(
    refresh: bool = Query(False, description="Force fresh AKShare fetch, bypassing 24h cache"),
):
    """HKEX universe with VGPM scores computed within the HK peer universe.
    Rows carry `currency` (listing currency of price and marketCap) and
    `marketCapUsd` (None when no FX rate is available)."""
    try:
        return await asyncio.to_thread(_hk_result, refresh)
    except Exception:
        raise internal_error(logger, "get_hk_screener_stocks")


@router.get("/sg-stocks")
async def get_sg_screener_stocks(
    refresh: bool = Query(False, description="Force fresh yfinance fetch, bypassing 24h cache"),
):
    """SGX universe (depositary receipts excluded) with VGPM scores computed
    within the SG peer universe; currency fields as for /hk-stocks."""
    try:
        return await asyncio.to_thread(_sg_result, refresh)
    except Exception:
        raise internal_error(logger, "get_sg_screener_stocks")


@router.get("/lookup")
async def lookup_ticker(symbol: str = Query(..., description="Ticker symbol or company name to look up")):
    try:
        result = await asyncio.to_thread(screener_service.lookup_ticker, symbol)
    except screener_service.ProviderUnavailable as exc:
        logger.warning("lookup %r: provider unavailable: %s", symbol, exc)
        raise HTTPException(status_code=503,
                            detail="Market data provider unavailable — try again shortly",
                            headers={"Retry-After": "5"})
    if result is None:
        raise HTTPException(status_code=404, detail=f"'{symbol}' not found")
    return result


# Write-back of fresh prices into the screener caches runs behind the
# response, one at a time: it rewrites every cached universe row, and doing
# it inline added its latency to every tick.
_writeback_lock = threading.Lock()


def _write_back(quotes: dict) -> None:
    if not _writeback_lock.acquire(blocking=False):
        return                       # one already running; the next tick catches up
    try:
        screener_service.update_cached_prices(quotes)
    except Exception:
        logger.debug("price write-back failed", exc_info=True)
    finally:
        _writeback_lock.release()


@router.get("/prices")
async def get_live_prices(
    symbols: str = Query(..., description="Comma-separated ticker symbols e.g. AAPL,MSFT,NVDA"),
):
    """Live quotes: {quotes: {symbol: {price, change_pct, volume, marketCap,
    as_of, stale?}}, missing: [...], stale: [...], as_of}.

    Symbols are deduped and capped at MAX_PRICE_SYMBOLS. Quotes come from
    FMP batch-quote behind a ~10 s single-flight cache; `stale` lists
    symbols whose price is not live (provider failed and a recent cached
    value was served, or the provider's own quote is days old — delisted or
    suspended). A price <= 0 is never returned."""
    tickers = screener_service._dedupe_symbols(symbols.split(","))
    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols provided")
    if len(tickers) > screener_service.MAX_PRICE_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many symbols ({len(tickers)}); at most "
                   f"{screener_service.MAX_PRICE_SYMBOLS} per request")
    detail = await asyncio.to_thread(screener_service.get_live_quotes_detail, tickers)
    fresh = {s: q for s, q in detail["quotes"].items() if not q.get("stale")}
    logger.info("get_live_prices: %d requested, %d live, %d stale, %d missing",
                len(tickers), len(fresh), len(detail["stale"]), len(detail["missing"]))
    if fresh:
        asyncio.get_running_loop().run_in_executor(None, _write_back, fresh)
    return detail


# ── Admin: VGPM backfill ──────────────────────────────────────────────────────

# Phase 5 (multi-replica web): the in-process `_backfill_running` flag only
# protected ONE replica. The guard is now a Redis lock shared with the arq
# worker's scheduled run_vgpm_backfill_task, so the daily job and an admin
# trigger can never run concurrently on ANY replica. When Redis is
# unavailable we degrade to the old per-process flag (single-replica
# semantics, same as pre-Phase 5).
_backfill_running_local = False


@router.post("/admin/backfill-universe")
async def backfill_universe(
    passes: int = Query(5, ge=1, le=15, description="Retry passes for rate-limited tickers"),
    delay: int = Query(30, ge=5, le=120, description="Seconds between batches for FMP rate-limit reset"),
    batch_size: int = Query(50, ge=10, le=100, description="Tickers per VGPM scoring batch"),
):
    """Admin endpoint: fetch all US stocks ≥$2B, score VGPM in batches, and
    pre-compute cache entries for every frontend market-cap filter.

    First run takes ~20-25 min (1,400 tickers × 8 FMP calls each).
    Subsequent runs with warm raw_metrics_cache complete in seconds.

    Returns 409 when a backfill is already running — on any web replica or
    in the arq worker (daily scheduled run).
    """
    global _backfill_running_local

    from app.backend.services.redis_locks import (
        VGPM_BACKFILL_LOCK_NAME,
        VGPM_BACKFILL_LOCK_TTL_S,
        try_lock,
        unlock,
    )

    acquired, token = await try_lock(VGPM_BACKFILL_LOCK_NAME, VGPM_BACKFILL_LOCK_TTL_S)
    if not acquired:
        raise HTTPException(status_code=409, detail="Backfill already in progress")
    if token is None:
        # Redis unavailable — fail-open degraded path: per-process guard only.
        if _backfill_running_local:
            raise HTTPException(status_code=409, detail="Backfill already in progress")
        _backfill_running_local = True

    def _run() -> dict:
        return screener_service.backfill_master_universe(
            batch_size=batch_size,
            passes=passes,
            delay=delay,
        )

    try:
        return await asyncio.to_thread(_run)
    finally:
        if token is not None:
            await unlock(VGPM_BACKFILL_LOCK_NAME, token)
        else:
            _backfill_running_local = False
