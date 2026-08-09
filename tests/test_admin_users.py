"""Phase 3d — admin user-management endpoints + require_admin role check.

Covered:
1. deps.require_admin — secret → None, JWT role='admin' → User,
   member JWT → 403, nothing → 403, secret beats member JWT.
2. GET /admin/users — full field projection.
3. PATCH /admin/users/{id} — partial updates, validation, 404, and the
   self-lockout guard (an admin acting via JWT cannot demote/disable
   themselves; the secret path can — that's the recovery path).
4. GET /admin/usage — per-user counts across web_runs,
   complacency_jobs and flow runs, with NULL user_id aggregated as
   'unowned'.

Routes are driven directly (asyncio.run) with explicit _admin/db args —
FastAPI's dependency graph isn't exercised here, the deps are tested
separately above.
"""

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.database.models import (
    User, HedgeFundFlow, HedgeFundFlowRun)
from app.backend.routes import deps as D
from app.backend.routes import admin as A
from src.data import db


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def orm_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "run_archive.db"))
    db.close_all_connections()
    yield
    db.close_all_connections()


class _FakeUser:
    def __init__(self, uid, role="member"):
        self.id = uid
        self.role = role


def _make_user(session, n, **kwargs):
    u = User(email=f"u{n}@example.com", provider="google",
             provider_sub=f"sub-{n}", **kwargs)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


# ---------------------------------------------------------------------------
# 1. require_admin semantics
# ---------------------------------------------------------------------------

def test_secret_grants_admin_returns_none(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sekret")
    assert D.require_admin(authorization=None, x_admin_secret="sekret",
                           db=None) is None


def test_admin_role_jwt_grants_admin(monkeypatch):
    monkeypatch.delenv("DB_UPLOAD_SECRET", raising=False)
    sentinel = _FakeUser(1, role="admin")
    monkeypatch.setattr(D, "get_user_from_token", lambda t, db: sentinel)
    assert D.require_admin(authorization="Bearer abc", x_admin_secret=None,
                           db=None) is sentinel


def test_member_jwt_denied(monkeypatch):
    monkeypatch.delenv("DB_UPLOAD_SECRET", raising=False)
    monkeypatch.setattr(D, "get_user_from_token",
                        lambda t, db: _FakeUser(1, role="member"))
    with pytest.raises(HTTPException) as exc:
        D.require_admin(authorization="Bearer abc", x_admin_secret=None,
                        db=None)
    assert exc.value.status_code == 403


def test_no_credentials_denied(monkeypatch):
    monkeypatch.delenv("DB_UPLOAD_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        D.require_admin(authorization=None, x_admin_secret=None, db=None)
    assert exc.value.status_code == 403


def test_secret_beats_member_jwt(monkeypatch):
    """Ordering regression: a matching secret short-circuits before the
    JWT path even when the JWT belongs to a mere member."""
    monkeypatch.setenv("DB_UPLOAD_SECRET", "sekret")

    def _boom(token, db):
        raise AssertionError("JWT path should not run when secret matches")

    monkeypatch.setattr(D, "get_user_from_token", _boom)
    assert D.require_admin(authorization="Bearer abc",
                           x_admin_secret="sekret", db=None) is None


# ---------------------------------------------------------------------------
# 2. GET /admin/users
# ---------------------------------------------------------------------------

def test_list_users_projects_all_fields(orm_db):
    _make_user(orm_db, 1)
    _make_user(orm_db, 2, role="admin", is_active=False,
               daily_pipeline_limit=5, concurrent_pipeline_limit=1)

    out = asyncio.run(A.admin_list_users(_admin=None, db=orm_db))
    assert len(out["users"]) == 2
    first, second = out["users"]
    assert first["email"] == "u1@example.com"
    assert first["role"] == "member"
    assert first["is_active"] is True
    assert first["daily_pipeline_limit"] == 20
    assert first["concurrent_pipeline_limit"] == 3
    assert second["role"] == "admin"
    assert second["is_active"] is False
    assert second["daily_pipeline_limit"] == 5
    assert second["concurrent_pipeline_limit"] == 1
    assert {"id", "email", "name", "provider", "created_at"} <= set(first)


# ---------------------------------------------------------------------------
# 3. PATCH /admin/users/{id}
# ---------------------------------------------------------------------------

def test_patch_updates_fields(orm_db):
    target = _make_user(orm_db, 1)
    out = asyncio.run(A.admin_update_user(
        target.id, A._UserPatch(role="admin", is_active=False,
                                daily_pipeline_limit=50,
                                concurrent_pipeline_limit=5),
        admin=None, db=orm_db))
    assert out["role"] == "admin"
    assert out["is_active"] is False
    assert out["daily_pipeline_limit"] == 50
    assert out["concurrent_pipeline_limit"] == 5


def test_patch_partial_update_leaves_rest(orm_db):
    target = _make_user(orm_db, 1)
    out = asyncio.run(A.admin_update_user(
        target.id, A._UserPatch(daily_pipeline_limit=7),
        admin=None, db=orm_db))
    assert out["daily_pipeline_limit"] == 7
    assert out["role"] == "member"          # untouched
    assert out["is_active"] is True         # untouched


def test_patch_unknown_user_404(orm_db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(A.admin_update_user(
            999, A._UserPatch(role="admin"), admin=None, db=orm_db))
    assert exc.value.status_code == 404


def test_patch_invalid_role_400(orm_db):
    target = _make_user(orm_db, 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(A.admin_update_user(
            target.id, A._UserPatch(role="superuser"),
            admin=None, db=orm_db))
    assert exc.value.status_code == 400


def test_patch_negative_limit_400(orm_db):
    target = _make_user(orm_db, 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(A.admin_update_user(
            target.id, A._UserPatch(concurrent_pipeline_limit=-1),
            admin=None, db=orm_db))
    assert exc.value.status_code == 400


def test_self_demote_guard(orm_db):
    """An admin acting via JWT cannot demote or deactivate themselves."""
    target = _make_user(orm_db, 1, role="admin")
    for patch in (A._UserPatch(role="member"), A._UserPatch(is_active=False)):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(A.admin_update_user(
                target.id, patch, admin=_FakeUser(target.id, "admin"),
                db=orm_db))
        assert exc.value.status_code == 400


def test_self_limit_change_allowed(orm_db):
    """...but adjusting their own limits is harmless and allowed."""
    target = _make_user(orm_db, 1, role="admin")
    out = asyncio.run(A.admin_update_user(
        target.id, A._UserPatch(daily_pipeline_limit=99),
        admin=_FakeUser(target.id, "admin"), db=orm_db))
    assert out["daily_pipeline_limit"] == 99


def test_secret_path_can_demote_anyone(orm_db):
    """admin=None (service secret) is the recovery path — no lockout guard."""
    target = _make_user(orm_db, 1, role="admin")
    out = asyncio.run(A.admin_update_user(
        target.id, A._UserPatch(role="member", is_active=False),
        admin=None, db=orm_db))
    assert out["role"] == "member"
    assert out["is_active"] is False


# ---------------------------------------------------------------------------
# 4. GET /admin/usage
# ---------------------------------------------------------------------------

def test_usage_aggregates_all_three_sources(orm_db, temp_db):
    alice = _make_user(orm_db, 1)
    bob = _make_user(orm_db, 2)

    # web_runs (run-archive DB) — minimal schema, only the columns read
    db.execute_script("""
        CREATE TABLE IF NOT EXISTS web_runs (
            run_id TEXT PRIMARY KEY, run_at TEXT NOT NULL,
            ticker TEXT NOT NULL, user_id INTEGER
        );
    """)
    db.execute(
        "INSERT INTO web_runs (run_id, run_at, ticker, user_id) "
        "VALUES (?, ?, ?, ?)", ["r1", "2026-08-09T10:00:00", "MSFT", alice.id])
    db.execute(
        "INSERT INTO web_runs (run_id, run_at, ticker, user_id) "
        "VALUES (?, ?, ?, ?)", ["r2", "2026-08-09T11:00:00", "NVDA", alice.id])
    db.execute(
        "INSERT INTO web_runs (run_id, run_at, ticker, user_id) "
        "VALUES (?, ?, ?, ?)", ["r3", "2026-08-09T12:00:00", "AAPL", None])

    # research jobs — one for bob, one unowned
    from app.backend.services import complacency_job_store as job_store
    job_store.create_job("refresh", user_id=bob.id)
    job_store.create_job("refresh")

    # flow runs (ORM) — flow_id is NOT NULL, so a parent flow first
    flow = HedgeFundFlow(user_id=alice.id, name="t", nodes=[], edges=[])
    orm_db.add(flow)
    orm_db.flush()
    for owner in (alice, None):
        orm_db.add(HedgeFundFlowRun(
            flow_id=flow.id, user_id=owner.id if owner else None))
    orm_db.commit()

    out = asyncio.run(A.admin_usage(_admin=None, db=orm_db))

    assert out["by_user"][str(alice.id)]["analysis_runs"] == 2
    assert out["by_user"][str(alice.id)]["last_analysis_run"] == \
        "2026-08-09T11:00:00"
    assert out["by_user"][str(alice.id)]["flow_runs"] == 1
    assert out["by_user"][str(bob.id)]["research_jobs"] == 1
    assert out["unowned"]["analysis_runs"] == 1
    assert out["unowned"]["research_jobs"] == 1
    assert out["unowned"]["flow_runs"] == 1


def test_usage_empty_db_reports_zeros(orm_db, temp_db):
    out = asyncio.run(A.admin_usage(_admin=None, db=orm_db))
    assert out["by_user"] == {}
    assert out["unowned"] == {
        "analysis_runs": 0, "research_jobs": 0, "flow_runs": 0}
