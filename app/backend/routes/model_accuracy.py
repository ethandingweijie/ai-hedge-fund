"""Model Accuracy -- the page's API (B6).

Every route requires a signed-in user whose email is on MODEL_ACCURACY_EMAILS.
No role and no service secret grants access: this is the only way a
calibration reaches the engine, so only the named sign-ins can see or act on it.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.backend.routes.deps import require_model_accuracy_owner

router = APIRouter(prefix="/model-accuracy", tags=["model-accuracy"])


def _actor(admin) -> Optional[str]:
    return getattr(admin, "email", None) or "service"


@router.get("/overview")
async def overview(admin=Depends(require_model_accuracy_owner)):
    """Recommendations, proposals with their backtest and shadow record, the
    live calibration, and diagnostics that need a code change."""
    from src.memory import calibration_review as review
    return await asyncio.to_thread(review.overview)


@router.get("/badge")
async def badge(admin=Depends(require_model_accuracy_owner)):
    """How many proposals are eligible for promotion right now."""
    from src.memory import calibration_review as review
    return {"eligible": await asyncio.to_thread(review.eligible_count)}


@router.get("/segment-memory")
async def segment_memory(admin=Depends(require_model_accuracy_owner)):
    """Reported segment revenue and profit for SOTP-valued names, with citations
    and the per-year reconciliation to FMP revenue (pending review)."""
    from src.data import segment_memory as sm
    return await asyncio.to_thread(sm.ui_summary)


async def _review(ticker: str, status: str, admin):
    from src.data import segment_memory as sm
    try:
        return await asyncio.to_thread(sm.set_review, ticker, status, _actor(admin))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no usable segment memory for {ticker!r}")


@router.post("/segment-memory/{ticker}/accept")
async def accept_segment_memory(ticker: str, admin=Depends(require_model_accuracy_owner)):
    """Accept these exact figures for live valuations (both listings of a
    dual-listed company). A rebuilt entry with different figures needs a new
    acceptance."""
    return await _review(ticker, "accepted", admin)


@router.post("/segment-memory/{ticker}/revoke")
async def revoke_segment_memory(ticker: str, admin=Depends(require_model_accuracy_owner)):
    return await _review(ticker, "revoked", admin)


@router.get("/calibration/{version_id}")
async def calibration_detail(version_id: str, admin=Depends(require_model_accuracy_owner)):
    from src.memory import calibration_review as review
    try:
        return await asyncio.to_thread(review.detail, version_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no calibration {version_id!r}")


async def _change(fn, version_id: str, **kwargs):
    from src.memory import calibration_review as review
    try:
        return await asyncio.to_thread(fn, version_id, **kwargs)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no calibration {version_id!r}")
    except review.PromotionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/calibration/{version_id}/promote")
async def promote(version_id: str, admin=Depends(require_model_accuracy_owner)):
    """Make a proposal live. Refused (409) unless it is in shadow and its
    shadow report says eligible."""
    from src.memory import calibration_review as review
    return await _change(review.promote, version_id, actor=_actor(admin))


@router.post("/calibration/{version_id}/rollback")
async def rollback(version_id: str, reason: str = "", admin=Depends(require_model_accuracy_owner)):
    from src.memory import calibration_review as review
    return await _change(review.rollback, version_id, actor=_actor(admin), reason=reason)


@router.post("/calibration/{version_id}/dismiss")
async def dismiss(version_id: str, reason: str = "", admin=Depends(require_model_accuracy_owner)):
    from src.memory import calibration_review as review
    return await _change(review.dismiss, version_id, actor=_actor(admin), reason=reason)
