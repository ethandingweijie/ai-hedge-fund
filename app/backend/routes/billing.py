"""
app/backend/routes/billing.py
==============================
GET  /billing/plans     public: the plans the pricing page renders (prices, credits, limits, features)
GET  /billing/me        the caller's plan and usage this period
POST /billing/checkout  {plan, interval: monthly|annual|addon} -> {url} (Stripe Checkout)
POST /billing/portal    -> {url} (Stripe customer portal: change plan, card, cancel)
POST /billing/webhook   public, signature-verified: the only writer of a paid plan
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.backend.routes.deps import require_user
from app.backend.services import billing, stripe_billing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
async def get_plans():
    return billing.public_plans()


@router.get("/me")
async def get_me(user=Depends(require_user)):
    def _me():
        acct = billing.get_account(user.id)
        return {"plan": billing.plan_for(user), "status": acct.get("status"),
                "interval": acct.get("billing_interval"), "period_end": acct.get("period_end"),
                "has_billing_account": bool(acct.get("stripe_customer_id")),
                "usage": billing.usage(user), "limits": billing.PLANS[billing.plan_for(user)]}
    return await asyncio.to_thread(_me)


@router.post("/checkout")
async def checkout(body: dict, user=Depends(require_user)):
    plan = str((body or {}).get("plan") or "")
    kind = str((body or {}).get("interval") or "monthly")
    if kind not in ("monthly", "annual", "addon") or plan not in billing.PLANS or plan == "free":
        raise HTTPException(status_code=422, detail="plan must be a paid plan; interval monthly | annual | addon")
    if not billing.payments_enabled():
        raise HTTPException(status_code=503, detail="Payments are not enabled yet.")
    try:
        url = await asyncio.to_thread(stripe_billing.checkout_url, user, plan, kind)
    except stripe_billing.StripeNotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"Payments are not enabled yet ({exc}).")
    except Exception:
        logger.exception("checkout failed for user %s", user.id)
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return {"url": url}


@router.post("/portal")
async def portal(user=Depends(require_user)):
    if not billing.payments_enabled():
        raise HTTPException(status_code=503, detail="Payments are not enabled yet.")
    try:
        return {"url": await asyncio.to_thread(stripe_billing.portal_url, user)}
    except ValueError:
        raise HTTPException(status_code=404, detail="No billing account yet: choose a plan first.")
    except Exception:
        logger.exception("portal failed for user %s", user.id)
        raise HTTPException(status_code=502, detail="Could not open billing. Please try again.")


@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        stripe_billing.verify(payload, request.headers.get("Stripe-Signature", ""))
    except stripe_billing.StripeNotConfigured:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    except stripe_billing.SignatureError as exc:
        logger.warning("stripe webhook rejected: %s", exc)
        raise HTTPException(status_code=400, detail="invalid signature")
    event = json.loads(payload or b"{}")
    try:
        did = await asyncio.to_thread(stripe_billing.handle_event, event)
    except Exception:
        # 500 makes Stripe retry; every handler is idempotent.
        logger.exception("stripe webhook %s (%s) failed", event.get("id"), event.get("type"))
        raise HTTPException(status_code=500, detail="handler failed")
    logger.info("stripe webhook %s %s: %s", event.get("id"), event.get("type"), did)
    return {"received": True, "result": did}
