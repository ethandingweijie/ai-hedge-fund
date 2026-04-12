"""
app/backend/routes/watchlist.py
================================
Watchlist CRUD endpoints — scoped per authenticated user.
"""
import traceback
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backend.services import watchlist_service
from app.backend.database.connection import get_db
from app.backend.services.auth_service import get_user_from_token

logger = logging.getLogger(__name__)
router = APIRouter()


class AddTickerRequest(BaseModel):
    ticker: str


def _optional_user_id(authorization: Optional[str] = Header(default=None),
                      db: Session = Depends(get_db)) -> Optional[int]:
    """Extract user_id from Bearer token if present, otherwise None.
    Watchlist works without auth (backward compat) but scopes by user when logged in."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(token, db)
    return user.id if user else None


@router.get("")
async def get_watchlist(user_id: Optional[int] = Depends(_optional_user_id)):
    try:
        return watchlist_service.get_watchlist(user_id=user_id)
    except Exception as exc:
        logger.error("get_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("")
async def add_to_watchlist(body: AddTickerRequest,
                           user_id: Optional[int] = Depends(_optional_user_id)):
    try:
        return watchlist_service.add_ticker(body.ticker, user_id=user_id)
    except Exception as exc:
        logger.error("add_to_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{ticker}")
async def remove_from_watchlist(ticker: str,
                                user_id: Optional[int] = Depends(_optional_user_id)):
    try:
        removed = watchlist_service.remove_ticker(ticker, user_id=user_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"'{ticker}' not in watchlist")
        return {"removed": ticker}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("remove_from_watchlist failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
