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


# ── P2: crisis replay ────────────────────────────────────────────────────────

@router.get("/replay/events")
async def replay_events():
    """The curated event library (metadata only — cheap, unauthenticated)."""
    return {"events": portfolio_service.list_events()}


@router.post("/replay")
async def start_replay(user_id: Optional[int] = Depends(_optional_user_id),
                       db: Session = Depends(get_db)):
    """Replay the current holdings through every curated crisis event.

    Cache-first on the holdings snapshot hash: unchanged portfolio →
    instant cached result ({cached: true}). Otherwise a tracked job is
    started and the client polls GET /portfolio/replay/jobs/{job_id}.
    """
    if not portfolio_service.replay_enabled():
        raise HTTPException(status_code=503, detail="portfolio replay disabled")
    try:
        out = await asyncio.to_thread(portfolio_service.start_replay, db, user_id)
    except Exception as exc:
        logger.error("start_replay failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
    if out.get("error") == "no_holdings":
        raise HTTPException(status_code=400, detail="add holdings before replaying")
    return out


@router.get("/replay/jobs/{job_id}")
async def replay_job(job_id: str,
                     user_id: Optional[int] = Depends(_optional_user_id)):
    if not portfolio_service.replay_enabled():
        raise HTTPException(status_code=503, detail="portfolio replay disabled")
    job = await asyncio.to_thread(portfolio_service.get_replay_job, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"replay job {job_id} not found")
    return job


# ── P5: what-if crisis simulator ─────────────────────────────────────────────

class WhatIfRequest(BaseModel):
    category: str
    concerns: str
    reference_key: Optional[str] = None
    search_override: str = "auto"          # auto | always | never
    horizon_days: int = Field(default=90, ge=5, le=365)
    share: bool = True                     # publish to joint scenario memory
    parent_id: Optional[str] = None        # forked-from library scenario


@router.get("/what-if/meta")
async def what_if_meta():
    """Scenario categories + reference-crisis choices + short-product
    knowledge base (confirmed / assumed / unknown) for the form UI."""
    return {
        "categories": portfolio_service.list_what_if_categories(),
        "reference_events": [
            {"key": e["key"], "name": e["name"], "window": e["window"],
             "spy_return_pct": e["benchmarks"]["spy_return_pct"],
             "qqq_return_pct": e["benchmarks"]["qqq_return_pct"]}
            for e in portfolio_service.list_events()
        ],
        "product_map": portfolio_service.product_knowledge(),
    }


@router.post("/what-if")
async def start_what_if(body: WhatIfRequest,
                        user_id: Optional[int] = Depends(_optional_user_id),
                        db: Session = Depends(get_db)):
    """Simulate a crisis that has not happened yet.

    Cache-first on the scenario hash (same inputs + holdings → instant
    cached result). Otherwise a tracked job runs the deterministic
    skeleton + one DeepSeek scenario call; poll
    GET /portfolio/what-if/jobs/{job_id}.
    """
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    try:
        out = await asyncio.to_thread(
            portfolio_service.start_what_if, db, user_id, body.category,
            body.concerns, body.reference_key, body.search_override,
            body.horizon_days, body.share, body.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("start_what_if failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
    if out.get("error") == "no_holdings":
        raise HTTPException(status_code=400, detail="add holdings before simulating")
    return out


@router.get("/what-if/jobs/{job_id}")
async def what_if_job(job_id: str,
                      user_id: Optional[int] = Depends(_optional_user_id)):
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    job = await asyncio.to_thread(portfolio_service.get_what_if_job, job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"what-if job {job_id} not found")
    return job


# ── P6: joint scenario memory + assumption tracking ─────────────────────────

class NoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class CheckRequest(BaseModel):
    method: str   # deep_research | market_data


@router.get("/what-if/library")
async def what_if_library(limit: int = 50):
    """Newest-first shared scenario memory (capped 50). Read-only list —
    unauthenticated reads are fine; writing (notes) requires auth."""
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    limit = min(max(int(limit), 1), 50)
    return await asyncio.to_thread(portfolio_service.list_what_if_library, limit)


@router.get("/what-if/library/{scenario_id}")
async def what_if_library_scenario(scenario_id: str):
    """Full scenario detail: result, assumptions (with sensitivity +
    latest check), notes and fork lineage."""
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    out = await asyncio.to_thread(portfolio_service.get_what_if_scenario,
                                  scenario_id)
    if out is None:
        raise HTTPException(status_code=404,
                            detail=f"scenario {scenario_id} not found")
    return out


@router.post("/what-if/library/{scenario_id}/notes")
async def add_what_if_note(scenario_id: str, body: NoteRequest,
                           user_id: Optional[int] = Depends(_optional_user_id),
                           db: Session = Depends(get_db)):
    """Append a dated community note to a shared scenario (auth required —
    notes are attributed)."""
    if user_id is None:
        raise HTTPException(status_code=401, detail="sign in to add notes")
    user_name = await asyncio.to_thread(
        portfolio_service._user_display_name, db, user_id)
    try:
        note = await asyncio.to_thread(
            portfolio_service.add_what_if_note, scenario_id, user_id,
            user_name, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("add_what_if_note failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
    if note is None:
        raise HTTPException(status_code=404,
                            detail=f"scenario {scenario_id} not found")
    return note


@router.post("/what-if/library/{scenario_id}/compare")
async def compare_what_if(scenario_id: str,
                          user_id: Optional[int] = Depends(_optional_user_id),
                          db: Session = Depends(get_db)):
    """Deterministic skeleton of the VIEWER's holdings under this shared
    scenario's reference/horizon + per-assumption sensitivity on the
    viewer's portfolio. Zero LLM calls — pure math."""
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    try:
        out = await asyncio.to_thread(
            portfolio_service.compare_what_if_to_holdings, db, user_id,
            scenario_id)
    except Exception as exc:
        logger.error("compare_what_if failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
    if out is None:
        raise HTTPException(status_code=404,
                            detail=f"scenario {scenario_id} not found")
    if out.get("error") == "no_holdings":
        raise HTTPException(status_code=400,
                            detail="add holdings before comparing")
    return out


@router.post("/what-if/assumptions/{assumption_id}/check")
async def check_assumption(assumption_id: str, body: CheckRequest,
                           user_id: Optional[int] = Depends(_optional_user_id),
                           db: Session = Depends(get_db)):
    """Kick a tracked assumption-verification job. market_data = FMP
    treasury/inflation/sector-ETF reading + one verdict call;
    deep_research = one qwen web search. Poll
    GET /portfolio/what-if/jobs/{job_id}."""
    if not portfolio_service.what_if_enabled():
        raise HTTPException(status_code=503, detail="what-if simulator disabled")
    user_name = await asyncio.to_thread(
        portfolio_service._user_display_name, db, user_id)
    try:
        return await asyncio.to_thread(
            portfolio_service.start_assumption_check, user_id, user_name,
            assumption_id, body.method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("check_assumption failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))
