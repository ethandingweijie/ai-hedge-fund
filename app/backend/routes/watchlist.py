"""
app/backend/routes/watchlist.py
================================
Watchlist CRUD endpoints.
"""
import traceback
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.backend.services import watchlist_service

logger = logging.getLogger(__name__)
router = APIRouter()


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("")
async def get_watchlist():
    try:
        return watchlist_service.get_watchlist()
    except Exception as exc:
        logger.error("get_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("")
async def add_to_watchlist(body: AddTickerRequest):
    try:
        return watchlist_service.add_ticker(body.ticker)
    except Exception as exc:
        logger.error("add_to_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{ticker}")
async def remove_from_watchlist(ticker: str):
    try:
        removed = watchlist_service.remove_ticker(ticker)
        if not removed:
            raise HTTPException(status_code=404, detail=f"'{ticker}' not in watchlist")
        return {"removed": ticker}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("remove_from_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
