"""
app/backend/services/billing.py
================================
Plans, the usage ledger and entitlements (pricing actions 1+2, 2026-11-28).

Before this, the plan lived in the browser's localStorage and "Activate" set it for free; nothing on the server
read it. This module is the single source of truth for what each plan includes, what a user is on, and what they
have used. The frontend renders the pricing page from `GET /billing/plans` and the caller's state from
`GET /billing/me`.

Storage: two NEW tables (CREATE IF NOT EXISTS; no ALTER on `users`, so no schema-guard risk):
  billing_accounts  one row per user who has ever had a paid plan: plan, status, interval, Stripe ids, period
  usage_ledger      one row per metered event: a run reservation, an add-on credit grant

Metering (`reserve_run`): a pipeline start reserves 1 credit (CREDITS_PER_RUN; credit pricing by run type is a
config change, not a schema change). A reservation SETTLES lazily: it counts while it is younger than
RESERVATION_HOLD_MIN, and after that only if the user has a completed (non-checkpoint) run of that ticker started
since -- so a failed or cancelled run is never charged, without hooking every pipeline exit path. A second start of
the same ticker inside the hold window reuses the first reservation (the dedup path serves it the same run).

Enforcement is behind BILLING_ENFORCED (default off). Off: usage is recorded and reported but nothing is blocked,
so this can deploy before Stripe is configured. On: a plan limit FAILS CLOSED (a ledger read error blocks the
start) -- unlike the Redis capacity guards, which fail open. Admins are always exempt.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

UNLIMITED = -1

#: The plans. Prices in USD; the Stripe price ids are read from env (STRIPE_PRICE_<PLAN>_<MONTHLY|ANNUAL>,
#: STRIPE_PRICE_<PLAN>_ADDON). Every claim the pricing page makes is a field here, and every field is enforced
#: (or is true of every run) -- nothing on the page is decoration.
PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free", "price_monthly": 0, "price_annual": 0,
        "credits": 1, "credit_period": "lifetime",
        "watchlist_limit": 3, "history_months": 1,
        "pdf_export": False, "excel_export": False, "addon": None,
    },
    "starter": {
        "name": "Starter", "price_monthly": 29, "price_annual": 290,
        "credits": 5, "credit_period": "month",
        "watchlist_limit": 15, "history_months": 6,
        "pdf_export": True, "excel_export": False, "addon": {"credits": 5, "price": 12},
    },
    "professional": {
        "name": "Professional", "price_monthly": 79, "price_annual": 790,
        "credits": 20, "credit_period": "month",
        "watchlist_limit": UNLIMITED, "history_months": 24,
        "pdf_export": True, "excel_export": True, "addon": {"credits": 10, "price": 18},
    },
}
PLAN_ORDER = ("free", "starter", "professional")

#: Included in every plan, every run (the page lists them once, not as a tier differentiator).
EVERY_RUN = (
    "Full analysis: DCF + peer multiples, bull / base / bear scenarios, 12-month price target",
    "Web-search-backed research on the company",
    "US, Hong Kong, Singapore and four more markets, priced on regional peers",
)

CREDITS_PER_RUN = 1
RESERVATION_HOLD_MIN = 60
#: a completed run is matched to a reservation started up to this long before the run's own run_at.
_SETTLE_SLACK_MIN = 5

ACTIVE_STATUSES = ("active", "trialing", "past_due")


def enforced() -> bool:
    return os.environ.get("BILLING_ENFORCED", "").strip().lower() in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _parse(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _get(row, key, idx):
    try:
        return row[key]
    except Exception:  # noqa: BLE001
        return row[idx]


# ── tables ─────────────────────────────────────────────────────────────────

def _ddl():
    from src.data import db
    pg = db.is_postgres()
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return (
        "CREATE TABLE IF NOT EXISTS billing_accounts (user_id INTEGER PRIMARY KEY, plan TEXT NOT NULL, "
        "status TEXT NOT NULL, billing_interval TEXT, period_start TEXT, period_end TEXT, "
        "stripe_customer_id TEXT, stripe_subscription_id TEXT, updated_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS usage_ledger (id {pk}, user_id INTEGER NOT NULL, created_at TEXT NOT NULL, "
        "kind TEXT NOT NULL, credits INTEGER NOT NULL, ticker TEXT, source TEXT, ref TEXT UNIQUE, note TEXT)",
    )


_READY = {"v": False}


def ensure_tables() -> None:
    if _READY["v"]:
        return
    from src.data import db
    for ddl in _ddl():
        db.ensure_table(ddl)
    db.execute("CREATE INDEX IF NOT EXISTS idx_usage_ledger_user ON usage_ledger (user_id, created_at)")
    _READY["v"] = True


# ── account ────────────────────────────────────────────────────────────────

def get_account(user_id: int) -> dict:
    """The user's plan. No row, or a lapsed subscription, is Free."""
    from src.data import db
    ensure_tables()
    row = db.query_one("SELECT plan, status, billing_interval, period_start, period_end, stripe_customer_id, "
                       "stripe_subscription_id FROM billing_accounts WHERE user_id = ?", [user_id])
    if not row:
        return {"plan": "free", "status": "none", "effective_plan": "free"}
    keys = ("plan", "status", "billing_interval", "period_start", "period_end", "stripe_customer_id",
            "stripe_subscription_id")
    acct = {k: _get(row, k, i) for i, k in enumerate(keys)}
    if acct["plan"] not in PLANS or acct["status"] not in ACTIVE_STATUSES:
        acct["effective_plan"] = "free"
    else:
        acct["effective_plan"] = acct["plan"]
    return acct


def upsert_account(user_id: int, **fields) -> None:
    from src.data import db
    ensure_tables()
    cur = db.query_one("SELECT user_id FROM billing_accounts WHERE user_id = ?", [user_id])
    fields["updated_at"] = _iso(_now())
    if cur:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE billing_accounts SET {sets} WHERE user_id = ?", [*fields.values(), user_id])
    else:
        fields.setdefault("plan", "free")
        fields.setdefault("status", "none")
        cols = ["user_id", *fields]
        db.execute(f"INSERT INTO billing_accounts ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                   [user_id, *fields.values()])


def find_user_by_customer(customer_id: str) -> Optional[int]:
    from src.data import db
    ensure_tables()
    row = db.query_one("SELECT user_id FROM billing_accounts WHERE stripe_customer_id = ?", [customer_id])
    return int(_get(row, "user_id", 0)) if row else None


def _is_admin(user) -> bool:
    return str(getattr(user, "role", "") or "").lower() == "admin"


def plan_for(user) -> str:
    if user is None:
        return "free"
    if _is_admin(user):
        return "professional"
    return get_account(user.id)["effective_plan"]


# ── usage ──────────────────────────────────────────────────────────────────

def period_start(acct: dict, plan: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Start of the allowance window: None = lifetime (Free); the subscription period when Stripe gave one;
    else the calendar month (UTC)."""
    now = now or _now()
    if PLANS[plan]["credit_period"] == "lifetime":
        return None
    ps = _parse(acct.get("period_start"))
    if ps and ps <= now:
        return ps
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _completed_run_since(user_id: int, ticker: str, since: datetime) -> bool:
    from src.data import db
    lo = _iso(since - timedelta(minutes=_SETTLE_SLACK_MIN))
    row = db.query_one("SELECT run_id FROM web_runs WHERE user_id = ? AND ticker = ? AND is_checkpoint = 0 "
                       "AND run_at >= ? LIMIT 1", [user_id, ticker, lo])
    return bool(row)


def _settled_runs(user_id: int, since: Optional[datetime], now: datetime, source: str,
                  completed=None) -> int:
    """Credits of run reservations that count, drawn from `source` (plan | addon): young ones, and old ones a
    completed run settled. A failed run's reservation stops counting when its hold expires."""
    from src.data import db
    completed = completed or _completed_run_since
    params: list = [user_id, source]
    where = "user_id = ? AND kind = 'run' AND source = ?"
    if since is not None:
        where += " AND created_at >= ?"
        params.append(_iso(since))
    rows = db.query(f"SELECT created_at, credits, ticker FROM usage_ledger WHERE {where}", params) or []
    total = 0
    for r in rows:
        at = _parse(_get(r, "created_at", 0))
        if at is None:
            continue
        if now - at < timedelta(minutes=RESERVATION_HOLD_MIN) or completed(user_id, _get(r, "ticker", 2), at):
            total += int(_get(r, "credits", 1) or 0)
    return total


def _addon_granted(user_id: int) -> int:
    from src.data import db
    row = db.query_one("SELECT COALESCE(SUM(credits), 0) AS s FROM usage_ledger WHERE user_id = ? AND kind = 'addon'",
                       [user_id])
    return int(_get(row, "s", 0) or 0) if row else 0


def usage(user, now: Optional[datetime] = None, completed=None) -> dict:
    """Credits included, used and left this period, plus add-on credits (bought packs never expire and are spent
    only once the period's allowance is gone)."""
    now = now or _now()
    plan = plan_for(user)
    p = PLANS[plan]
    exempt = user is not None and _is_admin(user)
    if user is None:
        used = addon_left = 0
        ps = None
    else:
        acct = get_account(user.id)
        ps = period_start(acct, plan, now)
        used = _settled_runs(user.id, ps, now, "plan", completed)
        addon_left = max(_addon_granted(user.id) - _settled_runs(user.id, None, now, "addon", completed), 0)
    plan_left = max(p["credits"] - used, 0)
    return {"plan": plan, "period": p["credit_period"], "period_start": ps.isoformat() if ps else None,
            "included": p["credits"], "used": used, "plan_left": plan_left, "addon_left": addon_left,
            "left": UNLIMITED if exempt else plan_left + addon_left, "enforced": enforced(), "exempt": exempt}


class LimitReached(Exception):
    def __init__(self, detail: dict):
        super().__init__(detail.get("message", "limit reached"))
        self.detail = detail


def reserve_run(user, ticker: str, now: Optional[datetime] = None) -> Optional[dict]:
    """Record a pipeline start. Raises LimitReached when enforced and no credit is left. Returns the usage after
    the reservation (None for anonymous/admin calls, which are not metered)."""
    if user is None or _is_admin(user):
        return None
    from src.data import db
    now = now or _now()
    try:
        ensure_tables()
        recent = db.query_one("SELECT id FROM usage_ledger WHERE user_id = ? AND kind = 'run' AND ticker = ? "
                              "AND created_at >= ? LIMIT 1",
                              [user.id, ticker, _iso(now - timedelta(minutes=RESERVATION_HOLD_MIN))])
        if recent:
            return usage(user, now)                       # the dedup path serves the same run: one charge
        u = usage(user, now)
        if enforced() and u["left"] != UNLIMITED and u["left"] < CREDITS_PER_RUN:
            raise LimitReached(_limit_detail(u))
        # Shadow mode (not enforced) records past the allowance as plan usage, so the report shows true demand.
        source = "addon" if (u["plan_left"] < CREDITS_PER_RUN and u["addon_left"] >= CREDITS_PER_RUN) else "plan"
        db.execute("INSERT INTO usage_ledger (user_id, created_at, kind, credits, ticker, source, ref) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   [user.id, _iso(now), "run", CREDITS_PER_RUN, ticker, source, f"run:{user.id}:{ticker}:{_iso(now)}"])
        return usage(user, now)
    except LimitReached:
        raise
    except Exception as exc:  # noqa: BLE001
        if enforced():
            logger.error("billing: reserve_run failed for user %s (%s) -- blocking (fail closed)", user.id, exc)
            raise LimitReached({"code": "billing_unavailable",
                                "message": "Usage could not be checked right now. Please try again shortly."})
        logger.warning("billing: reserve_run failed for user %s (%s) -- not enforced, allowing", user.id, exc)
        return None


def _limit_detail(u: dict) -> dict:
    p = PLANS[u["plan"]]
    if u["plan"] == "free":
        msg = "Your free analysis has been used. Upgrade to run more."
    elif p.get("addon"):
        msg = (f"You've used all {u['included']} analyses this month. Add {p['addon']['credits']} more for "
               f"${p['addon']['price']}, or upgrade.")
    else:
        msg = f"You've used all {u['included']} analyses this month."
    return {"code": "credits_exhausted", "message": msg, "usage": u}


def grant_addon(user_id: int, credits: int, ref: str, note: str = "") -> bool:
    """Idempotent on ref (the Stripe event / session id): a replayed webhook grants once."""
    from src.data import db
    ensure_tables()
    if db.query_one("SELECT id FROM usage_ledger WHERE ref = ?", [ref]):
        return False
    db.execute("INSERT INTO usage_ledger (user_id, created_at, kind, credits, ref, note) VALUES (?, ?, ?, ?, ?, ?)",
               [user_id, _iso(_now()), "addon", int(credits), ref, note[:200]])
    return True


# ── feature gates ──────────────────────────────────────────────────────────

FEATURE_LABELS = {"pdf_export": "PDF export", "excel_export": "the Excel valuation workbook"}


def min_plan_for(feature: str) -> Optional[str]:
    return next((p for p in PLAN_ORDER if PLANS[p].get(feature)), None)


def check_feature(user, feature: str) -> None:
    """Raises LimitReached when enforced and the plan lacks the feature."""
    if not enforced() or (user is not None and _is_admin(user)):
        return
    try:
        plan = plan_for(user)
    except Exception as exc:  # noqa: BLE001
        logger.error("billing: plan lookup failed (%s) -- blocking (fail closed)", exc)
        raise LimitReached({"code": "billing_unavailable", "message": "Your plan could not be checked right now."})
    if not PLANS[plan].get(feature):
        need = min_plan_for(feature)
        raise LimitReached({"code": "plan_required", "feature": feature, "required_plan": need,
                            "message": f"{FEATURE_LABELS.get(feature, feature)} is included from the "
                                       f"{PLANS[need]['name'] if need else 'paid'} plan."})


def check_watchlist(user, current_count: int) -> None:
    if not enforced() or (user is not None and _is_admin(user)):
        return
    plan = plan_for(user)
    cap = PLANS[plan]["watchlist_limit"]
    if cap != UNLIMITED and current_count >= cap:
        raise LimitReached({"code": "watchlist_full", "limit": cap,
                            "message": f"The {PLANS[plan]['name']} plan tracks up to {cap} tickers. Upgrade for more."})


def check_watchlist_for(user_id: int, ticker: str) -> None:
    """The route has only the user id: load the user, count their list (re-adding a listed name is free)."""
    from app.backend.database.connection import SessionLocal
    from app.backend.database.models import User
    from src.data import db
    s = SessionLocal()
    try:
        user = s.get(User, user_id)
    finally:
        s.close()
    rows = db.query("SELECT ticker FROM watchlist WHERE user_id = ?", [user_id]) or []
    have = {str(_get(r, "ticker", 0)).upper() for r in rows}
    if str(ticker or "").strip().upper() in have:
        return
    check_watchlist(user, len(have))


def history_cutoff(user) -> Optional[str]:
    """Earliest run_at the plan's history window shows (None = no limit, or not enforced)."""
    if not enforced() or user is None or _is_admin(user):
        return None
    months = PLANS[plan_for(user)]["history_months"]
    if months == UNLIMITED:
        return None
    return (_now() - timedelta(days=31 * months)).strftime("%Y-%m-%d")


def public_plans() -> dict:
    """GET /billing/plans: everything the pricing page renders."""
    out = []
    for pid in PLAN_ORDER:
        p = dict(PLANS[pid])
        p["id"] = pid
        p["annual_saving_pct"] = (round((1 - p["price_annual"] / (12 * p["price_monthly"])) * 100)
                                  if p["price_monthly"] else 0)
        p["purchasable"] = bool(pid != "free" and price_id(pid, "monthly"))
        out.append(p)
    return {"plans": out, "every_run": list(EVERY_RUN), "unlimited": UNLIMITED,
            "payments_enabled": payments_enabled(), "enforced": enforced()}


# ── Stripe config ──────────────────────────────────────────────────────────

def price_id(plan: str, kind: str) -> Optional[str]:
    """kind: monthly | annual | addon."""
    return (os.environ.get(f"STRIPE_PRICE_{plan.upper()}_{kind.upper()}") or "").strip() or None


def plan_for_price(pid: str) -> Optional[tuple[str, str]]:
    for plan in PLAN_ORDER[1:]:
        for kind in ("monthly", "annual", "addon"):
            if pid and price_id(plan, kind) == pid:
                return plan, kind
    return None


def payments_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_WEBHOOK_SECRET"))


def to_json(v) -> str:
    return json.dumps(v, default=str)
