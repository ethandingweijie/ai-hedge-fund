"""Pricing actions 1+2 (2026-11-28): server-side plans, the usage ledger, entitlements, Stripe."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.backend.services import billing as b
from app.backend.services import stripe_billing as sb


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "t.db"))
    from src.data import db as _db
    monkeypatch.setattr(_db, "get_db_path", lambda: str(tmp_path / "t.db"))
    _db.close_all_connections()
    monkeypatch.setitem(b._READY, "v", False)
    _db.ensure_table("CREATE TABLE IF NOT EXISTS web_runs (run_id TEXT PRIMARY KEY, run_at TEXT, ticker TEXT, "
                     "user_id INTEGER, is_checkpoint INTEGER DEFAULT 0)")
    yield _db
    _db.close_all_connections()


def _user(uid=7, role="member"):
    return SimpleNamespace(id=uid, role=role, email="u@example.test")


NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def _complete(db, uid, ticker, at):
    db.execute("INSERT INTO web_runs (run_id, run_at, ticker, user_id, is_checkpoint) VALUES (?, ?, ?, ?, 0)",
               [f"{ticker}-{at.isoformat()}", b._iso(at), ticker, uid])


# ── plans: every page claim is a field ──────────────────────────────────────

def test_public_plans_carry_prices_limits_and_the_true_annual_saving():
    got = {p["id"]: p for p in b.public_plans()["plans"]}
    assert [*got] == ["free", "starter", "professional"]
    assert got["starter"]["annual_saving_pct"] == 17           # 290 vs 12 x 29; the page said 20%
    assert got["free"]["credits"] == 1 and got["free"]["credit_period"] == "lifetime"
    for p in got.values():
        assert not {"agent_selection", "batch_tickers", "priority_queue", "api_access"} & set(p)


# ── metering ────────────────────────────────────────────────────────────────

def test_free_user_has_one_lifetime_run_then_is_blocked_when_enforced(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW)
    _complete(store, u.id, "AAPL", NOW + timedelta(minutes=8))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(u, "MSFT", now=NOW + timedelta(days=40))
    assert e.value.detail["code"] == "credits_exhausted"


def test_shadow_mode_records_but_never_blocks(store, monkeypatch):
    monkeypatch.delenv("BILLING_ENFORCED", raising=False)
    u = _user()
    for i, t in enumerate(("AAPL", "MSFT", "NVDA")):
        b.reserve_run(u, t, now=NOW + timedelta(minutes=i))
    got = b.usage(u, now=NOW + timedelta(minutes=5))
    assert got["used"] == 3 and got["left"] == 0 and got["enforced"] is False


def test_a_failed_run_is_not_charged_after_the_hold(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW)                                   # no completed run follows
    assert b.usage(u, now=NOW + timedelta(minutes=10))["used"] == 1     # held while it could still finish
    assert b.usage(u, now=NOW + timedelta(minutes=90))["used"] == 0     # released
    b.reserve_run(u, "MSFT", now=NOW + timedelta(minutes=90))           # so the free run is still there


def test_the_same_ticker_inside_the_hold_is_one_charge(store):
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW)
    b.reserve_run(u, "AAPL", now=NOW + timedelta(minutes=3))
    assert b.usage(u, now=NOW + timedelta(minutes=4))["used"] == 1


def test_monthly_allowance_resets_and_addons_are_spent_after_it(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.upsert_account(u.id, plan="starter", status="active")
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    for i in range(5):
        at = t0 + timedelta(hours=i)
        b.reserve_run(u, f"T{i}", now=at)
        _complete(store, u.id, f"T{i}", at + timedelta(minutes=5))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(u, "T5", now=t0 + timedelta(days=1))
    assert "Add 5 more for $12" in e.value.detail["message"]
    assert b.grant_addon(u.id, 5, ref="stripe:cs_1") is True
    assert b.grant_addon(u.id, 5, ref="stripe:cs_1") is False           # a replayed webhook grants once
    b.reserve_run(u, "T5", now=t0 + timedelta(days=1))
    got = b.usage(u, now=t0 + timedelta(days=1, minutes=1))
    assert got["plan_left"] == 0 and got["addon_left"] == 4
    dec = b.usage(u, now=datetime(2026, 12, 1, 1, tzinfo=timezone.utc))
    assert dec["used"] == 0 and dec["plan_left"] == 5                   # new calendar month


def test_admins_are_never_metered(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    a = _user(role="admin")
    for i in range(5):
        assert b.reserve_run(a, f"T{i}", now=NOW) is None
    assert b.usage(a)["left"] == b.UNLIMITED


def test_enforced_metering_fails_closed(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    monkeypatch.setattr(b, "usage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(_user(), "AAPL", now=NOW)
    assert e.value.detail["code"] == "billing_unavailable"
    monkeypatch.delenv("BILLING_ENFORCED")
    assert b.reserve_run(_user(), "AAPL", now=NOW) is None              # shadow mode allows


def test_lapsed_subscription_is_free(store):
    b.upsert_account(9, plan="professional", status="canceled")
    assert b.get_account(9)["effective_plan"] == "free"


# ── feature gates ───────────────────────────────────────────────────────────

def test_exports_follow_the_plan(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    with pytest.raises(b.LimitReached) as e:
        b.check_feature(u, "pdf_export")
    assert e.value.detail["required_plan"] == "starter"
    b.upsert_account(u.id, plan="starter", status="active")
    b.check_feature(u, "pdf_export")
    with pytest.raises(b.LimitReached):
        b.check_feature(u, "excel_export")                              # Excel is Professional
    monkeypatch.delenv("BILLING_ENFORCED")
    b.check_feature(_user(8), "excel_export")                           # not enforced: open


def test_watchlist_cap(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    with pytest.raises(b.LimitReached):
        b.check_watchlist(_user(), 3)
    b.check_watchlist(_user(), 2)


# ── Stripe ──────────────────────────────────────────────────────────────────

def _sign(payload: bytes, secret="whsec_test", ts=None):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_signature():
    body = b'{"id":"evt_1"}'
    sb.verify(body, _sign(body), secret="whsec_test")
    with pytest.raises(sb.SignatureError):
        sb.verify(body + b" ", _sign(body), secret="whsec_test")
    with pytest.raises(sb.SignatureError):
        sb.verify(body, _sign(body, ts=int(time.time()) - 3600), secret="whsec_test")
    with pytest.raises(sb.StripeNotConfigured):
        sb.verify(body, _sign(body), secret="")


def test_checkout_subscription_sets_the_plan_from_the_price(store, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_PROFESSIONAL_ANNUAL", "price_pro_y")
    sub = {"id": "sub_1", "customer": "cus_1", "status": "active", "metadata": {"user_id": "7"},
           "items": {"data": [{"price": {"id": "price_pro_y", "recurring": {"interval": "year"}},
                               "current_period_start": 1764288000, "current_period_end": 1795824000}]}}
    ev = {"type": "checkout.session.completed",
          "data": {"object": {"id": "cs_1", "mode": "subscription", "client_reference_id": "7",
                              "customer": "cus_1", "subscription": "sub_1", "metadata": {"plan": "professional"}}}}
    assert sb.handle_event(ev, fetch_subscription=lambda sid: sub) == "subscription: applied"
    acct = b.get_account(7)
    assert acct["effective_plan"] == "professional" and acct["billing_interval"] == "annual"
    assert b.find_user_by_customer("cus_1") == 7
    sb.handle_event({"type": "customer.subscription.deleted", "data": {"object": dict(sub, metadata={})}})
    assert b.get_account(7)["effective_plan"] == "free"


def test_addon_checkout_grants_credits_once(store):
    ev = {"type": "checkout.session.completed",
          "data": {"object": {"id": "cs_2", "mode": "payment", "payment_status": "paid", "client_reference_id": "7",
                              "metadata": {"plan": "starter", "kind": "addon"}}}}
    assert "granted 5" in sb.handle_event(ev)
    assert "already granted" in sb.handle_event(ev)
    assert b._addon_granted(7) == 5


def test_routes_and_public_paths():
    from app.backend import auth_gate
    from app.backend.routes import api_router
    paths = {getattr(r, "path", "") for r in api_router.routes}
    assert {"/billing/plans", "/billing/me", "/billing/checkout", "/billing/portal", "/billing/webhook"} <= paths
    assert auth_gate._is_public("/billing/webhook") and auth_gate._is_public("/billing/plans")
    assert not auth_gate._is_public("/billing/me")
