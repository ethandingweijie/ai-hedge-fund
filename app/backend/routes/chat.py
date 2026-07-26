"""
app/backend/routes/chat.py
============================
Per-ticker discussion endpoints — the first multi-user social feature in
this app. All endpoints require auth (require_user). Messages are checked
against a profanity filter before being persisted (see PROFANITY constant
below to extend the blocklist).

GET    /chat/{ticker}/messages                          — paginated / polling list
GET    /chat/{ticker}/messages/{message_id}/replies      — one level of replies
POST   /chat/{ticker}/messages                           — new top-level message
POST   /chat/{ticker}/messages/{message_id}/replies      — reply (one level only)
DELETE /chat/{ticker}/messages/{message_id}              — soft-delete, author-only
POST   /chat/{ticker}/messages/{message_id}/like         — toggle like
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from better_profanity import profanity

from app.backend.database import get_db
from app.backend.database.models import User
from app.backend.repositories.chat_repository import ChatRepository
from app.backend.routes.deps import require_user
from app.backend.models.schemas import (
    ChatMessageCreateRequest,
    ChatMessageResponse,
    ChatMessageListResponse,
    ChatReactionToggleResponse,
    ChatActiveTickerResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

# Loaded once at import time, not per-request.
profanity.load_censor_words()
# Extend the default blocklist here as finance-forum-specific harassment
# terms are identified — kept separate from route logic so it's easy to edit.
PROFANITY_EXTRA_WORDS: list[str] = []
if PROFANITY_EXTRA_WORDS:
    profanity.add_censor_words(PROFANITY_EXTRA_WORDS)


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _check_profanity(content: str) -> None:
    if profanity.contains_profanity(content):
        raise HTTPException(status_code=400, detail="Please remove inappropriate language and try again.")


@router.get("/active-tickers", response_model=list[ChatActiveTickerResponse])
async def list_active_tickers(
    limit: int = 12,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        repo = ChatRepository(db)
        return repo.get_active_tickers(limit=min(max(limit, 1), 50))
    except Exception as exc:
        logger.error("list_active_tickers failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load active tickers")


@router.get("/{ticker}/messages", response_model=ChatMessageListResponse)
async def list_messages(
    ticker: str,
    limit: int = 30,
    before_id: Optional[int] = None,
    since_id: Optional[int] = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        repo = ChatRepository(db)
        messages, has_more = repo.get_messages_for_ticker(
            ticker=_normalize_ticker(ticker),
            current_user_id=user.id,
            limit=min(max(limit, 1), 100),
            before_id=before_id,
            since_id=since_id,
        )
        return ChatMessageListResponse(messages=messages, has_more=has_more)
    except Exception as exc:
        logger.error("list_messages failed for %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail="Failed to load messages")


@router.get("/{ticker}/messages/{message_id}/replies", response_model=list[ChatMessageResponse])
async def list_replies(
    ticker: str,
    message_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        repo = ChatRepository(db)
        return repo.get_replies_for_message(message_id=message_id, current_user_id=user.id)
    except Exception as exc:
        logger.error("list_replies failed for message %s: %s", message_id, exc)
        raise HTTPException(status_code=500, detail="Failed to load replies")


@router.post("/{ticker}/messages", response_model=ChatMessageResponse)
async def create_message(
    ticker: str,
    body: ChatMessageCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_profanity(body.content)
    try:
        repo = ChatRepository(db)
        message = repo.create_message(ticker=_normalize_ticker(ticker), user_id=user.id, content=body.content)
        return repo.get_message_by_id(message.id, user.id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("create_message failed for %s: %s", ticker, exc)
        raise HTTPException(status_code=500, detail="Failed to post message")


@router.post("/{ticker}/messages/{message_id}/replies", response_model=ChatMessageResponse)
async def create_reply(
    ticker: str,
    message_id: int,
    body: ChatMessageCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_profanity(body.content)
    norm_ticker = _normalize_ticker(ticker)
    try:
        repo = ChatRepository(db)
        reply = repo.create_message(
            ticker=norm_ticker, user_id=user.id, content=body.content, parent_message_id=message_id,
        )
        return repo.get_message_by_id(reply.id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("create_reply failed for message %s: %s", message_id, exc)
        raise HTTPException(status_code=500, detail="Failed to post reply")


@router.delete("/{ticker}/messages/{message_id}")
async def delete_message(
    ticker: str,
    message_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        repo = ChatRepository(db)
        repo.soft_delete_message(message_id=message_id, user_id=user.id)
        return {"deleted": True}
    except LookupError:
        raise HTTPException(status_code=404, detail="Message not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Only the author can delete this message")
    except Exception as exc:
        logger.error("delete_message failed for message %s: %s", message_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete message")


@router.post("/{ticker}/messages/{message_id}/like", response_model=ChatReactionToggleResponse)
async def toggle_like(
    ticker: str,
    message_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        repo = ChatRepository(db)
        result = repo.toggle_reaction(message_id=message_id, user_id=user.id, reaction_type="like")
        return ChatReactionToggleResponse(**result)
    except LookupError:
        raise HTTPException(status_code=404, detail="Message not found")
    except Exception as exc:
        logger.error("toggle_like failed for message %s: %s", message_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update like")
