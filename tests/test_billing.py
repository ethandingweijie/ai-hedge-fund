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
    monkeypatch.setattr(b, "_is_covered", lambda t: True)               # covered (1 credit) unless a test says
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

def test_public_plans_carry_prices_and_limits_monthly_only():
    pub = b.public_plans()
    got = {p["id"]: p for p in pub["plans"]}
    assert [*got] == ["free", "starter", "professional"]
    assert (got["starter"]["price_monthly"], got["starter"]["credits"]) == (29, 15)
    assert (got["professional"]["price_monthly"], got["professional"]["credits"]) == (79, 50)
    for p in got.values():                                  # owner 2026-11-28: no annual plan, no discount
        assert "price_annual" not in p and "annual_saving_pct" not in p
    assert got["free"]["credits"] == 3 and got["free"]["credit_period"] == "month"
    assert pub["credit_costs"] == {"covered": 1, "new": 2, "premium": 4}
    for p in got.values():
        assert not {"agent_selection", "batch_tickers", "priority_queue", "api_access"} & set(p)


def test_every_add_on_credit_is_priced_above_its_worst_case_cost():
    worst_cost_per_credit = 3.00 / b.CREDIT_COSTS["premium"]             # an Anthropic full-research run
    for p in b.PLANS.values():
        if p["addon"]:
            assert p["addon"]["price"] / p["addon"]["credits"] > worst_cost_per_credit


# ── metering ────────────────────────────────────────────────────────────────

def test_run_type_prices_by_cost():
    assert b.run_type("AAPL", "qwen3.8-flash", covered=True) == "covered"
    assert b.run_type("AAPL", "qwen3.8-flash", covered=False) == "new"
    assert b.run_type("AAPL", None, covered=False) == "new"
    assert b.run_type("AAPL", "claude-sonnet-4-6", covered=True) == "premium"
    boom = lambda t: (_ for _ in ()).throw(RuntimeError("db down"))
    assert b.run_type("AAPL", "qwen3.8-flash", covered=boom) == "covered"    # a lookup error: the cheaper rate


def test_free_user_gets_three_credits_a_month_then_is_blocked_when_enforced(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW, covered=False)                    # new ticker: 2
    _complete(store, u.id, "AAPL", NOW + timedelta(minutes=8))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(u, "MSFT", now=NOW + timedelta(hours=2), covered=False)
    assert e.value.detail["credits_needed"] == 2 and "needs 2 credits and you have 1 left" in e.value.detail["message"]
    b.reserve_run(u, "MSFT", now=NOW + timedelta(hours=2))              # covered: 1
    _complete(store, u.id, "MSFT", NOW + timedelta(hours=2, minutes=5))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(u, "NVDA", now=NOW + timedelta(hours=3))
    assert e.value.detail["code"] == "credits_exhausted" and "3 free credits" in e.value.detail["message"]
    assert b.usage(u, now=datetime(2026, 12, 1, 1, tzinfo=timezone.utc))["left"] == 3   # renews monthly


def test_anthropic_runs_cost_four_and_free_cannot_afford_one(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(_user(), "AAPL", now=NOW, model_name="claude-sonnet-4-6")
    assert e.value.detail["run_type"] == "premium" and "A Qwen run costs less" in e.value.detail["message"]
    pro = _user(8)
    b.upsert_account(pro.id, plan="professional", status="active")
    assert b.reserve_run(pro, "AAPL", now=NOW, model_name="claude-sonnet-4-6")["used"] == 4


def test_shadow_mode_records_but_never_blocks(store, monkeypatch):
    monkeypatch.delenv("BILLING_ENFORCED", raising=False)
    u = _user()
    for i, t in enumerate(("AAPL", "MSFT", "NVDA", "TSLA")):
        b.reserve_run(u, t, now=NOW + timedelta(minutes=i))
    got = b.usage(u, now=NOW + timedelta(minutes=5))
    assert got["used"] == 4 and got["left"] == 0 and got["enforced"] is False


def test_a_failed_run_is_not_charged_after_the_hold(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW, covered=False)                    # no completed run follows
    assert b.usage(u, now=NOW + timedelta(minutes=10))["used"] == 2     # held while it could still finish
    assert b.usage(u, now=NOW + timedelta(minutes=90))["used"] == 0     # released
    b.reserve_run(u, "MSFT", now=NOW + timedelta(minutes=90), covered=False)


def test_the_same_ticker_inside_the_hold_is_one_charge(store):
    u = _user()
    b.reserve_run(u, "AAPL", now=NOW, covered=False)
    b.reserve_run(u, "AAPL", now=NOW + timedelta(minutes=3), covered=False)
    assert b.usage(u, now=NOW + timedelta(minutes=4))["used"] == 2


def test_monthly_allowance_resets_and_a_run_straddles_plan_and_addon_credits(store, monkeypatch):
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    u = _user()
    b.upsert_account(u.id, plan="starter", status="active")
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    for i in range(7):                                                  # 7 new tickers = 14 of 15
        at = t0 + timedelta(hours=i)
        b.reserve_run(u, f"T{i}", now=at, covered=False)
        _complete(store, u.id, f"T{i}", at + timedelta(minutes=5))
    with pytest.raises(b.LimitReached) as e:
        b.reserve_run(u, "T7", now=t0 + timedelta(days=1), covered=False)
    assert "Add 10 credits for $12" in e.value.detail["message"]
    assert b.grant_addon(u.id, 10, ref="stripe:cs_1") is True
    assert b.grant_addon(u.id, 10, ref="stripe:cs_1") is False          # a replayed webhook grants once
    b.reserve_run(u, "T7", now=t0 + timedelta(days=1), covered=False)   # 1 from the plan + 1 add-on
    _complete(store, u.id, "T7", t0 + timedelta(days=1, minutes=5))
    got = b.usage(u, now=t0 + timedelta(days=1, minutes=1))
    assert got["plan_left"] == 0 and got["addon_left"] == 9
    dec = b.usage(u, now=datetime(2026, 12, 1, 1, tzinfo=timezone.utc))
    assert dec["used"] == 0 and dec["plan_left"] == 15 and dec["addon_left"] == 9   # packs never expire


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
        b.check_watchlist(_user(), 5)
    b.check_watchlist(_user(), 4)


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
    assert "granted 10" in sb.handle_event(ev)
    assert "already granted" in sb.handle_event(ev)
    assert b._addon_granted(7) == 10


def test_routes_and_public_paths():
    from app.backend import auth_gate
    from app.backend.routes import api_router
    paths = {getattr(r, "path", "") for r in api_router.routes}
    assert {"/billing/plans", "/billing/me", "/billing/checkout", "/billing/portal", "/billing/webhook"} <= paths
    assert auth_gate._is_public("/billing/webhook") and auth_gate._is_public("/billing/plans")
    assert not auth_gate._is_public("/billing/me")


# ── plan gates on every path (2026-11-28) ───────────────────────────────────

def _web_run(db, uid, run_id, at):
    db.execute("INSERT INTO web_runs (run_id, run_at, ticker, user_id, is_checkpoint) VALUES (?, ?, ?, ?, 0)",
               [run_id, b._iso(at), "AAPL", uid])


def test_a_run_older_than_the_plan_window_cannot_be_opened(store, monkeypatch):
    now = datetime.now(timezone.utc)
    _web_run(store, 7, "old", now - timedelta(days=70))
    _web_run(store, 7, "new", now - timedelta(days=3))
    monkeypatch.delenv("BILLING_ENFORCED", raising=False)
    b.check_run_history(_user(), "old")                                 # shadow mode: open
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    b.check_run_history(_user(), "new")
    with pytest.raises(b.LimitReached) as e:
        b.check_run_history(_user(), "old")                             # Free keeps 1 month
    assert e.value.detail["code"] == "history_window" and e.value.detail["required_plan"] == "starter"
    b.upsert_account(7, plan="starter", status="active")
    b.check_run_history(_user(), "old")                                 # Starter keeps 12
    b.check_run_history(_user(role="admin"), "old")


def test_operator_only_jobs(monkeypatch):
    monkeypatch.delenv("BILLING_ENFORCED", raising=False)
    b.check_operator(_user())                                           # shadow mode: unchanged
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    with pytest.raises(b.LimitReached) as e:
        b.check_operator(_user())
    assert e.value.detail["code"] == "operator_only"
    b.check_operator(_user(role="admin"))
    b.check_operator(None, service=True)


def test_operator_gate_dependency(monkeypatch):
    from fastapi import HTTPException
    from app.backend.routes import deps
    monkeypatch.setenv("DB_UPLOAD_SECRET", "s3cret")
    monkeypatch.setattr(deps, "get_user_from_token", lambda tok, db: _user(role="admin" if tok == "adm" else "member"))
    monkeypatch.delenv("BILLING_ENFORCED", raising=False)
    deps.operator_gate("Bearer mem", None, None)
    monkeypatch.setenv("BILLING_ENFORCED", "true")
    with pytest.raises(HTTPException) as e:
        deps.operator_gate("Bearer mem", None, None)
    assert e.value.status_code == 403 and e.value.detail["code"] == "operator_only"
    with pytest.raises(HTTPException):
        deps.operator_gate(None, None, None)                            # anonymous
    deps.operator_gate("Bearer adm", None, None)
    deps.operator_gate(None, "s3cret", None)                            # the scheduler / service secret


OPERATOR_ROUTES = {
    "/research/ideas/idea-of-the-day/generate", "/research/ideas/sw46/refresh", "/research/ideas/hk50/refresh",
    "/research/ideas/hk50/qual-deep-research", "/research/ideas/complacency/refresh",
    "/research/ideas/complacency/qual-sweep", "/research/ideas/analyst-docs/sync",
    "/research/ideas/analyst-docs/steward", "/research/ideas/analyst-docs/refresh/{ticker}",
    "/research/ideas/complacency/refresh-sectors", "/research/ideas/momentum/refresh",
    "/research/ideas/fundflow/refresh", "/research/ideas/fundflow/weekly-run", "/research/ideas/iv15-alerts/run",
    "/research/ideas/hundred-q/refresh", "/hedge-fund/run", "/hedge-fund/backtest",
    "/flows/{flow_id}/runs/",
}


def test_every_owner_job_route_carries_the_operator_gate():
    from app.backend.routes import api_router
    from app.backend.routes.deps import operator_gate
    gated = {r.path for r in api_router.routes
             if "POST" in (getattr(r, "methods", None) or set())
             and any(getattr(d, "call", None) is operator_gate for d in r.dependant.dependencies)}
    assert OPERATOR_ROUTES <= gated, OPERATOR_ROUTES - gated
    # what a plan includes stays open: running an analysis, ad-hoc screen scoring, overrides
    assert not {"/analysis/run", "/research/ideas/complacency/score/{ticker}",
                "/research/ideas/hundred-q/{ticker}/rescore"} & gated


def test_every_single_run_path_checks_the_history_window():
    import inspect
    from app.backend.routes import analysis as a
    for fn in (a.get_run, a.get_run_workbook, a.get_run_report_pdf, a._owned_run_or_404):
        assert "_run_in_plan_history" in inspect.getsource(fn), fn.__name__


def test_checkout_offers_monthly_and_addon_only():
    import asyncio
    from fastapi import HTTPException
    from app.backend.routes import billing as rb
    with pytest.raises(HTTPException) as e:
        asyncio.run(rb.checkout({"plan": "starter", "interval": "annual"}, user=_user()))
    assert e.value.status_code == 422
