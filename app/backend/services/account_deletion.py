"""Self-service account deletion (owner, 2026-11-28; PDPA withdrawal of consent + the Privacy Policy's promise).

DELETE /auth/me erases the signed-in user's personal data and the account:

  ERASED      holdings, watchlist, chat posts and reactions, saved what-if runs and replays, robo-strategy profile
              and portfolio, estimate overrides, the user's own reports (web_runs), their personal job rows, their
              flows and API keys, and the user row itself.
  DETACHED    (user_id set to NULL; the row no longer identifies anyone) records shared across users or needed to keep
              the service honest: published what-if scenarios, market questions (the once-a-day update cap per ticker
              counts them), run cost records, override-outcome learning rows.
  KEPT        billing records (billing_accounts, usage_ledger): five years, as Singapore accounting and tax rules
              require -- the Privacy Policy says so.

Refused (409) while a paid subscription is active: cancel it first, so nobody is billed for a deleted account.
Admins cannot delete themselves here (the owner account runs the service).
Every step is soft: a missing table is skipped, and the result lists what happened to each.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: raw-SQL tables (src.data.db) holding user rows -- (table, action)
RAW_TABLES: tuple[tuple[str, str], ...] = (
    ("watchlist", "delete"),
    ("portfolio_replays", "delete"),
    ("portfolio_what_ifs", "delete"),
    ("robo_strategy_portfolio", "delete"),
    ("robo_strategy_profile", "delete"),
    ("estimate_overrides", "delete"),
    ("complacency_jobs", "delete"),
    ("web_runs", "delete"),
    ("what_if_scenario_library", "detach"),
    ("market_asks", "detach"),
    ("run_costs", "detach"),
    ("override_outcomes", "detach"),
)


class DeletionRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _paid_subscription_active(user_id: int) -> bool:
    try:
        from app.backend.services import billing
        acct = billing.get_account(user_id)
        return acct.get("effective_plan") not in (None, "free") and bool(acct.get("stripe_subscription_id"))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("account deletion: billing lookup failed for %s: %s", user_id, exc)
        return False


def _raw(user_id: int) -> dict:
    from src.data import db
    out = {}
    for table, action in RAW_TABLES:
        try:
            if not db.table_exists(table) or not db.column_exists(table, "user_id"):
                out[table] = "absent"
                continue
            if action == "delete":
                n = db.execute(f"DELETE FROM {table} WHERE user_id = ?", [user_id])
            else:
                n = db.execute(f"UPDATE {table} SET user_id = NULL WHERE user_id = ?", [user_id])
            out[table] = f"{action}d {n or 0}"
        except Exception as exc:                            # noqa: BLE001
            out[table] = f"error: {type(exc).__name__}"
            logger.warning("account deletion: %s %s failed for %s: %s", action, table, user_id, exc)
    return out


def _orm(session, user_id: int) -> dict:
    from app.backend.database.models import (ApiKey, ChatMessage, ChatReaction, HedgeFundFlow, HedgeFundFlowRun,
                                             HedgeFundFlowRunCycle, User, UserHolding)
    out = {}
    q = session.query
    mine = [m.id for m in q(ChatMessage.id).filter(ChatMessage.user_id == user_id).all()]
    # Reactions by the user, and anyone's reactions on the user's posts; replies to the user's posts stay, detached.
    out["chat_reactions"] = q(ChatReaction).filter(
        (ChatReaction.user_id == user_id) | (ChatReaction.message_id.in_(mine or [-1]))).delete(synchronize_session=False)
    if mine:
        q(ChatMessage).filter(ChatMessage.parent_message_id.in_(mine)).update(
            {ChatMessage.parent_message_id: None}, synchronize_session=False)
    out["chat_messages"] = q(ChatMessage).filter(ChatMessage.user_id == user_id).delete(synchronize_session=False)
    out["user_holdings"] = q(UserHolding).filter(UserHolding.user_id == user_id).delete(synchronize_session=False)
    out["api_keys"] = q(ApiKey).filter(ApiKey.user_id == user_id).delete(synchronize_session=False)
    runs = [r.id for r in q(HedgeFundFlowRun.id).filter(HedgeFundFlowRun.user_id == user_id).all()]
    flows = [f.id for f in q(HedgeFundFlow.id).filter(HedgeFundFlow.user_id == user_id).all()]
    if flows:
        runs += [r.id for r in q(HedgeFundFlowRun.id).filter(HedgeFundFlowRun.flow_id.in_(flows)).all()]
    if runs:
        q(HedgeFundFlowRunCycle).filter(HedgeFundFlowRunCycle.flow_run_id.in_(runs)).delete(synchronize_session=False)
        out["flow_runs"] = q(HedgeFundFlowRun).filter(HedgeFundFlowRun.id.in_(runs)).delete(synchronize_session=False)
    out["flows"] = q(HedgeFundFlow).filter(HedgeFundFlow.user_id == user_id).delete(synchronize_session=False)
    out["users"] = q(User).filter(User.id == user_id).delete(synchronize_session=False)
    session.commit()
    return out


def delete_account(user, session) -> dict:
    """Erase the user's personal data and the account. Returns what happened to each table."""
    uid = getattr(user, "id", None)
    if uid is None:
        raise DeletionRefused(401, "Sign in to delete your account.")
    if str(getattr(user, "role", "") or "").lower() == "admin":
        raise DeletionRefused(403, "An admin account cannot be deleted from the app.")
    if _paid_subscription_active(uid):
        raise DeletionRefused(409, "Cancel your subscription in the billing portal first; you can delete your account "
                                   "once it has ended.")
    raw = _raw(uid)
    try:
        orm = _orm(session, uid)
    except Exception:
        session.rollback()
        raise
    logger.info("account deletion: user %s erased (%s / %s)", uid, raw, orm)
    return {"deleted": True, "raw": raw, "orm": orm}
