"""
app/backend/routes/robo_strategy.py
=====================================
Robo Strategy — deterministic portfolio recommendation from a risk/horizon/
sector/geography questionnaire. Synchronous, not async-job: the work is
arithmetic over an already-cached/live-fetched universe (24h-cached ETF
metadata, already-cached Screener reads), nowhere near the multi-minute
LLM-backed jobs that justify research.py's job-store/poll ceremony. One
endpoint serves both the first Generate and every Regenerate.

Field naming is snake_case over the wire, matching this app's existing
research-ideas/contrarian feature (idea.conviction_score, idea.hypothesis,
etc.) rather than inventing a new camelCase-aliasing convention this
codebase has no other precedent for.
"""
import asyncio
import logging
import traceback
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backend.database.connection import get_db
from app.backend.services import etf_metadata_service, robo_strategy_service, robo_strategy_storage
from app.backend.services.auth_service import get_user_from_token

logger = logging.getLogger(__name__)
router = APIRouter()


def _optional_user_id(authorization: Optional[str] = Header(default=None),
                       db: Session = Depends(get_db)) -> Optional[int]:
    """Extract user_id from Bearer token if present, otherwise None. Mirrors
    routes/watchlist.py's dependency exactly. This route sits behind
    <RequireAuth> on the frontend, so user_id will in practice always be
    set — the optional pattern is kept for consistency with the rest of
    the backend, not because guest usage is expected."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(token, db)
    return user.id if user else None


class QuestionnaireAnswers(BaseModel):
    risk_tolerance: Literal["conservative", "moderate", "aggressive"]
    time_horizon: Literal["<3 years", "3-7 years", "7-15 years", "15+ years"]
    sector_preferences: dict[str, float]
    geography_preferences: dict[str, float]
    investment_amount: float


@router.get("/profile")
async def get_profile(user_id: Optional[int] = Depends(_optional_user_id)):
    if user_id is None:
        return {"answers": None, "portfolio": None}
    try:
        answers = await asyncio.to_thread(robo_strategy_storage.get_profile, user_id)
        portfolio = await asyncio.to_thread(robo_strategy_storage.get_portfolio, user_id)
        return {"answers": answers, "portfolio": portfolio}
    except Exception as exc:
        logger.error("robo_strategy get_profile failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate")
async def generate(body: QuestionnaireAnswers, user_id: Optional[int] = Depends(_optional_user_id)):
    try:
        answers = body.model_dump()
        portfolio = await asyncio.to_thread(robo_strategy_service.generate_portfolio, answers)
        if user_id is not None:
            await asyncio.to_thread(robo_strategy_storage.save_profile, user_id, answers)
            await asyncio.to_thread(robo_strategy_storage.save_portfolio, user_id, portfolio, "etf")
        return portfolio
    except Exception as exc:
        logger.error("robo_strategy generate failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/etf-universe")
async def get_etf_universe_debug(force_refresh: bool = False):
    """Admin/debug convenience — inspect the live-fetched ETF metadata directly."""
    try:
        return await asyncio.to_thread(etf_metadata_service.get_etf_universe, force_refresh)
    except Exception as exc:
        logger.error("robo_strategy etf-universe failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
