"""
app/backend/routes/screener.py
================================
Stock screener endpoint — fetches FMP candidates + joins with internal VGPM data.
"""
import asyncio
import traceback
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

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
    limit: int = Query(100, ge=1, le=500),
    refresh: bool = Query(False, description="Force fresh FMP fetch, bypassing 24h cache"),
):
    try:
        # Run in thread so blocking FMP/SQLite calls don't stall the event loop.
        # Cache hits return in <10ms; misses can take 15–20s without threading.
        return await asyncio.to_thread(
            screener_service.get_screener_stocks,
            sector=sector,
            exchange=exchange,
            country=country,
            market_cap_more_than=marketCapMin,
            market_cap_lower_than=marketCapMax,
            limit=limit,
            force_refresh=refresh,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_screener_stocks failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/hk-stocks")
async def get_hk_screener_stocks(
    refresh: bool = Query(False, description="Force fresh AKShare fetch, bypassing 24h cache"),
):
    """Return ~118 well-known HKEX stocks with VGPM scores computed within the HK peer universe."""
    try:
        return await asyncio.to_thread(screener_service.get_hk_screener_stocks, force_refresh=refresh)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_hk_screener_stocks failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/sg-stocks")
async def get_sg_screener_stocks(
    refresh: bool = Query(False, description="Force fresh yfinance fetch, bypassing 24h cache"),
):
    """Return ~80 SGX stocks with VGPM scores computed within the SG peer universe."""
    try:
        return await asyncio.to_thread(screener_service.get_sg_screener_stocks, force_refresh=refresh)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("get_sg_screener_stocks failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{exc}\n\n{tb}")


@router.get("/lookup")
async def lookup_ticker(symbol: str = Query(..., description="Ticker symbol or company name to look up")):
    result = await asyncio.to_thread(screener_service.lookup_ticker, symbol)
    if result is None:
        raise HTTPException(status_code=404, detail=f"'{symbol}' not found")
    return result


@router.get("/prices")
async def get_live_prices(
    symbols: str = Query(..., description="Comma-separated ticker symbols e.g. AAPL,MSFT,NVDA"),
):
    """Lightweight live quote fetch — price, marketCap, volume, beta only. No VGPM.
    Also writes the fresh prices back into the screener SQLite cache."""
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols provided")
    # Run in thread — FMP calls for 200 tickers take ~20s and would block the event loop.
    quotes = await asyncio.to_thread(screener_service.get_live_quotes, tickers)
    logger.info("get_live_prices: requested %d tickers, got %d quotes back", len(tickers), len(quotes))
    if quotes:
        # Log a sample so we can confirm real prices are coming through
        sample = dict(list(quotes.items())[:3])
        logger.info("get_live_prices sample: %s", sample)
        try:
            await asyncio.to_thread(screener_service.update_cached_prices, quotes)
        except Exception:
            pass  # cache write failure is non-fatal
    else:
        logger.warning("get_live_prices: FMP returned empty quotes for %d tickers", len(tickers))
    return quotes


# ── Admin: VGPM backfill ──────────────────────────────────────────────────────

_backfill_running = False


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
    """
    global _backfill_running
    if _backfill_running:
        raise HTTPException(status_code=409, detail="Backfill already in progress")

    def _run() -> dict:
        global _backfill_running
        _backfill_running = True
        try:
            return screener_service.backfill_master_universe(
                batch_size=batch_size,
                passes=passes,
                delay=delay,
            )
        finally:
            _backfill_running = False

    return await asyncio.to_thread(_run)
