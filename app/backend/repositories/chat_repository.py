from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.backend.database.models import ChatMessage, ChatReaction, User
from app.backend.services.user_display import display_name


class ChatRepository:
    """Repository for per-ticker chat message + reaction CRUD.

    Read methods return plain dicts already shaped for ChatMessageResponse
    (author_name/reply_count/like_count/liked_by_me computed here) rather
    than bare ORM objects — those fields aren't native columns, so the
    aggregation belongs in one place instead of being re-derived by every
    caller.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Writes ───────────────────────────────────────────────────────────────

    def create_message(
        self,
        ticker: str,
        user_id: int,
        content: str,
        parent_message_id: Optional[int] = None,
    ) -> ChatMessage:
        if parent_message_id is not None:
            parent = self.db.query(ChatMessage).filter(ChatMessage.id == parent_message_id).first()
            if parent is None:
                raise ValueError("Parent message not found")
            if parent.parent_message_id is not None:
                raise ValueError("Cannot reply to a reply — one level of nesting only")
            if parent.ticker != ticker:
                raise ValueError("Parent message belongs to a different ticker")

        message = ChatMessage(
            ticker=ticker,
            user_id=user_id,
            parent_message_id=parent_message_id,
            content=content,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def soft_delete_message(self, message_id: int, user_id: int) -> ChatMessage:
        message = self.db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
        if message is None:
            raise LookupError("Message not found")
        if message.user_id != user_id:
            raise PermissionError("Only the author can delete this message")
        message.deleted_at = func.now()
        self.db.commit()
        self.db.refresh(message)
        return message

    def toggle_reaction(self, message_id: int, user_id: int, reaction_type: str = "like") -> dict:
        message = self.db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
        if message is None:
            raise LookupError("Message not found")

        existing = self.db.query(ChatReaction).filter(
            ChatReaction.message_id == message_id,
            ChatReaction.user_id == user_id,
            ChatReaction.reaction_type == reaction_type,
        ).first()

        if existing is not None:
            self.db.delete(existing)
            self.db.commit()
            liked = False
        else:
            try:
                self.db.add(ChatReaction(message_id=message_id, user_id=user_id, reaction_type=reaction_type))
                self.db.commit()
                liked = True
            except IntegrityError:
                # Lost a race against a concurrent identical insert — the
                # reaction exists either way, treat this call as a no-op "like".
                self.db.rollback()
                liked = True

        like_count = self.db.query(func.count(ChatReaction.id)).filter(
            ChatReaction.message_id == message_id,
            ChatReaction.reaction_type == reaction_type,
        ).scalar()
        return {"liked": liked, "like_count": like_count}

    # ── Reads ────────────────────────────────────────────────────────────────

    def _attach_aggregates(self, messages: List[ChatMessage], current_user_id: int) -> List[dict]:
        """Batch-compute reply_count/like_count/liked_by_me/author_name for a
        list of messages — 3 follow-up queries total regardless of list size,
        not N+1."""
        if not messages:
            return []

        ids = [m.id for m in messages]

        reply_rows = (
            self.db.query(ChatMessage.parent_message_id, func.count(ChatMessage.id))
            .filter(ChatMessage.parent_message_id.in_(ids), ChatMessage.deleted_at.is_(None))
            .group_by(ChatMessage.parent_message_id)
            .all()
        )
        reply_counts = {parent_id: count for parent_id, count in reply_rows}

        like_rows = (
            self.db.query(ChatReaction.message_id, func.count(ChatReaction.id))
            .filter(ChatReaction.message_id.in_(ids), ChatReaction.reaction_type == "like")
            .group_by(ChatReaction.message_id)
            .all()
        )
        like_counts = {message_id: count for message_id, count in like_rows}

        liked_rows = (
            self.db.query(ChatReaction.message_id)
            .filter(
                ChatReaction.message_id.in_(ids),
                ChatReaction.reaction_type == "like",
                ChatReaction.user_id == current_user_id,
            )
            .all()
        )
        liked_by_me_ids = {row[0] for row in liked_rows}

        author_ids = {m.user_id for m in messages}
        authors = {u.id: u for u in self.db.query(User).filter(User.id.in_(author_ids)).all()}

        result = []
        for m in messages:
            is_deleted = m.deleted_at is not None
            author = authors.get(m.user_id)
            result.append({
                "id": m.id,
                "ticker": m.ticker,
                "user_id": m.user_id,
                "author_name": display_name(author) if author else "Unknown",
                "content": "[deleted]" if is_deleted else m.content,
                "created_at": m.created_at,
                "edited_at": m.edited_at,
                "is_deleted": is_deleted,
                "parent_message_id": m.parent_message_id,
                "reply_count": reply_counts.get(m.id, 0),
                "like_count": like_counts.get(m.id, 0),
                "liked_by_me": m.id in liked_by_me_ids,
            })
        return result

    def get_messages_for_ticker(
        self,
        ticker: str,
        current_user_id: int,
        limit: int = 30,
        before_id: Optional[int] = None,
        since_id: Optional[int] = None,
    ) -> tuple[List[dict], bool]:
        """Top-level messages only (parent_message_id IS NULL). Soft-deleted
        rows are still returned (as tombstones) so their replies don't look
        orphaned — only their content is masked, by _attach_aggregates.

        since_id: polling path — ignores limit/before_id, returns everything
        newer than since_id, ascending (oldest-first, ready to append).
        Otherwise: newest-first page of `limit` (+1 to detect has_more),
        optionally older than before_id, then reversed to ascending for display.
        """
        base = self.db.query(ChatMessage).filter(
            ChatMessage.ticker == ticker,
            ChatMessage.parent_message_id.is_(None),
        )

        if since_id is not None:
            rows = base.filter(ChatMessage.id > since_id).order_by(ChatMessage.id.asc()).all()
            return self._attach_aggregates(rows, current_user_id), False

        if before_id is not None:
            base = base.filter(ChatMessage.id < before_id)

        rows = base.order_by(ChatMessage.id.desc()).limit(limit + 1).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()  # oldest-first for display
        return self._attach_aggregates(rows, current_user_id), has_more

    def get_message_by_id(self, message_id: int, current_user_id: int) -> Optional[dict]:
        message = self.db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
        if message is None:
            return None
        return self._attach_aggregates([message], current_user_id)[0]

    def get_replies_for_message(self, message_id: int, current_user_id: int) -> List[dict]:
        rows = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.parent_message_id == message_id)
            .order_by(ChatMessage.id.asc())
            .all()
        )
        return self._attach_aggregates(rows, current_user_id)

    def get_active_tickers(self, limit: int = 12) -> List[dict]:
        """Tickers with at least one message (top-level or reply, excluding
        soft-deletes), ranked by most recent activity. `message_count`
        covers the whole thread (not just top-level) so it reads as
        "how much discussion", not just "how many topics started"."""
        rows = (
            self.db.query(
                ChatMessage.ticker,
                func.count(ChatMessage.id).label("message_count"),
                func.max(ChatMessage.created_at).label("last_activity_at"),
            )
            .filter(ChatMessage.deleted_at.is_(None))
            .group_by(ChatMessage.ticker)
            .order_by(func.max(ChatMessage.created_at).desc())
            .limit(limit)
            .all()
        )
        return [
            {"ticker": ticker, "message_count": count, "last_activity_at": last_activity}
            for ticker, count, last_activity in rows
        ]
