"""Model Accuracy -- the admin page's API (B6).

Every route requires an admin: a signed-in user with role='admin', or the
service secret. This is the only way a calibration reaches the engine, so it
is not reachable by members at all.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.backend.routes.deps import require_admin

router = APIRouter(prefix="/model-accuracy", tags=["model-accuracy"])


def _actor(admin) -> Optional[str]:
    return getattr(admin, "email", None) or "service"


@router.get("/overview")
async def overview(admin=Depends(require_admin)):
    """Recommendations, proposals with their backtest and shadow record, the
    live calibration, and diagnostics that need a code change."""
    from src.memory import calibration_review as review
    return await asyncio.to_thread(review.overview)


@router.get("/badge")
async def badge(admin=Depends(require_admin)):
    """How many proposals are eligible for promotion right now."""
    from src.memory import calibration_review as review
    return {"eligible": await asyncio.to_thread(review.eligible_count)}


@router.get("/calibration/{version_id}")
async def calibration_detail(version_id: str, admin=Depends(require_admin)):
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
async def promote(version_id: str, admin=Depends(require_admin)):
    """Make a proposal live. Refused (409) unless it is in shadow and its
    shadow report says eligible."""
    from src.memory import calibration_review as review
    return await _change(review.promote, version_id, actor=_actor(admin))


@router.post("/calibration/{version_id}/rollback")
async def rollback(version_id: str, reason: str = "", admin=Depends(require_admin)):
    from src.memory import calibration_review as review
    return await _change(review.rollback, version_id, actor=_actor(admin), reason=reason)


@router.post("/calibration/{version_id}/dismiss")
async def dismiss(version_id: str, reason: str = "", admin=Depends(require_admin)):
    from src.memory import calibration_review as review
    return await _change(review.dismiss, version_id, actor=_actor(admin), reason=reason)
