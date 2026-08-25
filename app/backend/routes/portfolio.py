"""
app/backend/routes/portfolio.py
================================
P1 — holdings CRUD + indicator dashboard, scoped per authenticated user.

Follows the watchlist auth pattern: works without a token (user_id NULL
scope) and scopes by user when logged in. All service calls are blocking
(SQLAlchemy + one FMP batch call + one archive query) → asyncio.to_thread
per the async-routes rule.
"""
import asyncio
import logging
import traceback
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.backend.database.connection import get_db
from app.backend.services import portfolio_service
from app.backend.services.auth_service import get_user_from_token

logger = logging.getLogger(__name__)
router = APIRouter()


class HoldingRequest(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)
    avg_cost: float = Field(gt=0)
    opened_at: Optional[datetime] = None
    notes: Optional[str] = None


class HoldingUpdate(BaseModel):
    quantity: Optional[float] = Field(default=None, gt=0)
    avg_cost: Optional[float] = Field(default=None, gt=0)
    opened_at: Optional[datetime] = None
    notes: Optional[str] = None


def _optional_user_id(authorization: Optional[str] = Header(default=None),
                      db: Session = Depends(get_db)) -> Optional[int]:
    """Same optional-auth dependency as watchlist.py — holdings work
    unauthenticated (NULL scope) and scope by user when logged in."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(token, db)
    return user.id if user else None


@router.get("")
async def get_portfolio_dashboard(user_id: Optional[int] = Depends(_optional_user_id),
                                  db: Session = Depends(get_db)):
    """Holdings enriched with live prices + the latest archived signals
    per ticker (decision, IV base/bear/bull, sector, VGPM) + portfolio
    weights, sector exposure and unrealized P&L."""
    try:
        return await asyncio.to_thread(portfolio_service.get_dashboard, db, user_id)
    except Exception as exc:
        logger.error("get_portfolio_dashboard failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/holdings")
async def add_holding(body: HoldingRequest,
                      user_id: Optional[int] = Depends(_optional_user_id),
                      db: Session = Depends(get_db)):
    """Add (or replace) a position for (user, ticker)."""
    try:
        row = await asyncio.to_thread(
            portfolio_service.upsert_holding, db, user_id, body.ticker,
            body.quantity, body.avg_cost, body.opened_at, body.notes)
        return portfolio_service._holding_dict(row)
    except Exception as exc:
        logger.error("add_holding failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/holdings/{holding_id}")
async def update_holding(holding_id: int, body: HoldingUpdate,
                         user_id: Optional[int] = Depends(_optional_user_id),
                         db: Session = Depends(get_db)):
    try:
        row = await asyncio.to_thread(
            portfolio_service.update_holding, db, user_id, holding_id,
            body.quantity, body.avg_cost, body.opened_at, body.notes)
        if row is None:
            raise HTTPException(status_code=404, detail=f"holding {holding_id} not found")
        return portfolio_service._holding_dict(row)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("update_holding failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/holdings/{holding_id}")
async def delete_holding(holding_id: int,
                         user_id: Optional[int] = Depends(_optional_user_id),
                         db: Session = Depends(get_db)):
    try:
        removed = await asyncio.to_thread(
            portfolio_service.delete_holding, db, user_id, holding_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"holding {holding_id} not found")
        return {"removed": holding_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_holding failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
