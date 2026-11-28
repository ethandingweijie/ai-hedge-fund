"""
app/backend/services/stripe_billing.py
=======================================
Stripe Checkout, the customer portal and the webhook (pricing action 2, 2026-11-28).

Plain REST over `requests` -- no `stripe` package, so poetry.lock is untouched (any lock change is a full
dependency reinstall on Railway; see the 2026-08-15 incident). The webhook signature is verified the way Stripe's
own libraries do it: HMAC-SHA256 over "<timestamp>.<raw body>" with the endpoint secret, compared in constant
time, timestamp within WEBHOOK_TOLERANCE_S.

Env:
  STRIPE_SECRET_KEY        sk_test_... / sk_live_...
  STRIPE_WEBHOOK_SECRET    whsec_... (the endpoint's signing secret)
  STRIPE_PRICE_STARTER_MONTHLY / _ANNUAL / _ADDON, STRIPE_PRICE_PROFESSIONAL_MONTHLY / _ANNUAL / _ADDON
  APP_BASE_URL             the frontend origin, for the success / cancel / portal return URLs

The webhook is the ONLY writer of a paid plan: a Checkout redirect back to the app proves nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from app.backend.services import billing

logger = logging.getLogger(__name__)

API = "https://api.stripe.com/v1"
WEBHOOK_TOLERANCE_S = 300


class StripeNotConfigured(Exception):
    pass


class SignatureError(Exception):
    pass


def _key() -> str:
    k = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not k:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set")
    return k


def _post(path: str, data: dict) -> dict:
    import requests
    r = requests.post(f"{API}{path}", data=data, auth=(_key(), ""), timeout=20)
    if r.status_code >= 300:
        logger.warning("stripe %s -> %s: %s", path, r.status_code, r.text[:300])
        raise RuntimeError(f"Stripe {path} failed ({r.status_code})")
    return r.json()


def _get(path: str) -> dict:
    import requests
    r = requests.get(f"{API}{path}", auth=(_key(), ""), timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Stripe {path} failed ({r.status_code})")
    return r.json()


def _app_url(fragment: str) -> str:
    base = (os.environ.get("APP_BASE_URL") or "").rstrip("/")
    return f"{base}/#{fragment}" if base else f"/#{fragment}"


# ── Checkout + portal ──────────────────────────────────────────────────────

def checkout_url(user, plan: str, kind: str) -> str:
    """kind: monthly (a subscription) | addon (a one-off credit pack for the user's current plan)."""
    if plan not in billing.PLANS or plan == "free":
        raise ValueError("unknown plan")
    pid = billing.price_id(plan, kind)
    if not pid:
        raise StripeNotConfigured(f"no Stripe price for {plan} {kind}")
    acct = billing.get_account(user.id)
    data = {
        "mode": "payment" if kind == "addon" else "subscription",
        "line_items[0][price]": pid,
        "line_items[0][quantity]": "1",
        "client_reference_id": str(user.id),
        "metadata[user_id]": str(user.id),
        "metadata[plan]": plan,
        "metadata[kind]": kind,
        "success_url": _app_url("/pricing?checkout=success"),
        "cancel_url": _app_url("/pricing?checkout=cancelled"),
        "allow_promotion_codes": "true",
    }
    if kind != "addon":
        data["subscription_data[metadata][user_id]"] = str(user.id)
        data["subscription_data[metadata][plan]"] = plan
    if acct.get("stripe_customer_id"):
        data["customer"] = acct["stripe_customer_id"]
    elif getattr(user, "email", None):
        data["customer_email"] = user.email
    return _post("/checkout/sessions", data)["url"]


def portal_url(user) -> str:
    cid = billing.get_account(user.id).get("stripe_customer_id")
    if not cid:
        raise ValueError("no billing account yet")
    return _post("/billing_portal/sessions", {"customer": cid, "return_url": _app_url("/pricing")})["url"]


# ── webhook ────────────────────────────────────────────────────────────────

def verify(payload: bytes, sig_header: str, secret: Optional[str] = None, now: Optional[float] = None) -> None:
    secret = secret if secret is not None else (os.environ.get("STRIPE_WEBHOOK_SECRET") or "")
    if not secret:
        raise StripeNotConfigured("STRIPE_WEBHOOK_SECRET is not set")
    parts: dict[str, list[str]] = {}
    for item in (sig_header or "").split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int((parts.get("t") or [""])[0])
    except ValueError:
        raise SignatureError("no timestamp")
    if abs((now if now is not None else time.time()) - ts) > WEBHOOK_TOLERANCE_S:
        raise SignatureError("timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in parts.get("v1") or []):
        raise SignatureError("signature mismatch")


def _ts(v) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _apply_subscription(sub: dict, user_id: Optional[int] = None) -> Optional[int]:
    meta = sub.get("metadata") or {}
    uid = user_id or (int(meta["user_id"]) if str(meta.get("user_id") or "").isdigit() else None) \
        or billing.find_user_by_customer(sub.get("customer") or "")
    if uid is None:
        logger.warning("stripe: subscription %s has no user", sub.get("id"))
        return None
    item = ((sub.get("items") or {}).get("data") or [{}])[0]
    price = (item.get("price") or {})
    mapped = billing.plan_for_price(price.get("id") or "")
    plan = mapped[0] if mapped and mapped[1] != "addon" else (meta.get("plan") or "free")
    interval = (price.get("recurring") or {}).get("interval")
    # API 2025+: the period moved from the subscription onto each item.
    start = sub.get("current_period_start") or item.get("current_period_start")
    end = sub.get("current_period_end") or item.get("current_period_end")
    billing.upsert_account(uid, plan=plan, status=str(sub.get("status") or "none"),
                           billing_interval={"month": "monthly", "year": "annual"}.get(interval, interval),
                           period_start=_ts(start), period_end=_ts(end),
                           stripe_customer_id=sub.get("customer"), stripe_subscription_id=sub.get("id"))
    return uid


def handle_event(event: dict, fetch_subscription=None) -> str:
    """Apply one verified event. Returns what it did (for the log / response)."""
    et = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}
    fetch_subscription = fetch_subscription or (lambda sid: _get(f"/subscriptions/{sid}"))
    if et == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        ref = obj.get("client_reference_id") or meta.get("user_id")
        uid = int(ref) if str(ref or "").isdigit() else None
        if uid is None:
            return "ignored: no user on the session"
        if obj.get("customer"):
            billing.upsert_account(uid, stripe_customer_id=obj["customer"])
        if obj.get("mode") == "payment" and meta.get("kind") == "addon":
            if obj.get("payment_status") != "paid":
                return "addon: not paid yet"
            pack = (billing.PLANS.get(meta.get("plan") or "") or {}).get("addon") or {}
            granted = billing.grant_addon(uid, int(pack.get("credits") or 0), ref=f"stripe:{obj.get('id')}",
                                          note=f"{meta.get('plan')} add-on")
            return f"addon: {'granted' if granted else 'already granted'} {pack.get('credits')} credits"
        if obj.get("subscription"):
            _apply_subscription(fetch_subscription(obj["subscription"]), uid)
            return "subscription: applied"
        return "checkout: nothing to apply"
    if et in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        if et.endswith("deleted"):
            obj = dict(obj, status="canceled")
        uid = _apply_subscription(obj)
        return f"subscription {obj.get('status')}: user {uid}"
    return f"ignored: {et}"
