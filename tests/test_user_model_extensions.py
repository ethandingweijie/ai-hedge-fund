"""Phase 3b — User model extensions (role, is_active, pipeline limits).

Covers:
1. ORM defaults on new signups (member / active / 20 / 3) and explicit
   value round-trip.
2. is_active gate in auth_service.get_user_from_token — the single choke
   point used by BOTH the AuthGateMiddleware and the require_user
   dependency, so deactivating a user logs them out everywhere at once.
3. The Alembic migration e6a7b8c9d0e1: chains onto the previous head,
   backfills an old-schema users table with server defaults, and is
   idempotent (re-running upgrade — via alembic or directly — is a no-op).

Runs against in-memory SQLite and throwaway file DBs; no network, no env
changes escape the tests (everything through monkeypatch/tmp_path).
"""

import importlib
import sqlite3
import time
from pathlib import Path

import pytest
from jose import jwt as jose_jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.backend.database.connection import Base
from app.backend.database.models import User
from app.backend.services import auth_service

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = REPO_ROOT / "app" / "backend" / "alembic"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def orm_db():
    """Fresh in-memory SQLite with the full ORM schema."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_user(db, n: int, **kwargs) -> User:
    user = User(
        email=f"user{n}@example.com",
        provider="google",
        provider_sub=f"google-sub-{n}",
        **kwargs,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token_for(user_id: int, secret: str) -> str:
    return jose_jwt.encode(
        {"sub": str(user_id), "exp": int(time.time()) + 3600},
        secret,
        algorithm="HS256",
    )


def _alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _create_old_schema_users(db_path: Path) -> None:
    """users table as it existed before Phase 3b, with one legacy row."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME,
                updated_at DATETIME,
                email VARCHAR(255) NOT NULL,
                name VARCHAR(255),
                avatar_url TEXT,
                provider VARCHAR(50) NOT NULL,
                provider_sub VARCHAR(255) NOT NULL
            );
            CREATE UNIQUE INDEX ix_users_email ON users (email);
            INSERT INTO users (email, name, provider, provider_sub)
            VALUES ('existing@example.com', 'Existing User',
                    'google', 'google-sub-existing');
            """
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. ORM model defaults + round-trip
# ---------------------------------------------------------------------------

def test_new_user_gets_member_defaults(orm_db):
    user = _make_user(orm_db, 1)
    assert user.role == "member"
    assert user.is_active is True
    assert user.daily_pipeline_limit == 20
    assert user.concurrent_pipeline_limit == 3


def test_explicit_admin_values_round_trip(orm_db):
    user = _make_user(
        orm_db, 2,
        role="admin", is_active=False,
        daily_pipeline_limit=5, concurrent_pipeline_limit=1,
    )
    orm_db.expire_all()
    reloaded = orm_db.get(User, user.id)
    assert reloaded.role == "admin"
    assert reloaded.is_active is False
    assert reloaded.daily_pipeline_limit == 5
    assert reloaded.concurrent_pipeline_limit == 1


# ---------------------------------------------------------------------------
# 2. is_active gate in get_user_from_token
# ---------------------------------------------------------------------------

def test_valid_token_resolves_active_user(orm_db, monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", "test-secret-key")
    user = _make_user(orm_db, 1)
    resolved = auth_service.get_user_from_token(_token_for(user.id, "test-secret-key"), orm_db)
    assert resolved is not None
    assert resolved.id == user.id


def test_deactivated_user_token_rejected(orm_db, monkeypatch):
    """An unexpired token for an is_active=False user must resolve to None
    (→ 401 in every caller)."""
    monkeypatch.setattr(auth_service, "SECRET_KEY", "test-secret-key")
    user = _make_user(orm_db, 1, is_active=False)
    assert auth_service.get_user_from_token(
        _token_for(user.id, "test-secret-key"), orm_db) is None


def test_reactivation_restores_access(orm_db, monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", "test-secret-key")
    user = _make_user(orm_db, 1, is_active=False)
    token = _token_for(user.id, "test-secret-key")
    assert auth_service.get_user_from_token(token, orm_db) is None
    user.is_active = True
    orm_db.commit()
    assert auth_service.get_user_from_token(token, orm_db) is not None


# ---------------------------------------------------------------------------
# 3. Alembic migration
# ---------------------------------------------------------------------------

def test_migration_is_sole_head_and_chains(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(tmp_path / "chain_check.db")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["e6a7b8c9d0e1"], f"unexpected alembic heads: {heads}"


def test_migration_backfills_old_users_schema(monkeypatch, tmp_path):
    """Pre-3b users table + one legacy row → upgrade adds all four columns
    and backfills member/active/20/3."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "users_old.db"
    _create_old_schema_users(db_path)

    cfg = _alembic_config(db_path)
    command.stamp(cfg, "d4e5f6a7b8c9")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT role, is_active, daily_pipeline_limit, "
            "concurrent_pipeline_limit FROM users WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("member", 1, 20, 3)


def test_migration_skips_columns_created_by_create_all(monkeypatch, tmp_path):
    """Fresh installs: create_all already emits the four columns from the
    model; the migration must inspect-and-skip, not crash."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "users_fresh.db"

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    cfg = _alembic_config(db_path)
    command.stamp(cfg, "d4e5f6a7b8c9")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()
    assert {"role", "is_active", "daily_pipeline_limit",
            "concurrent_pipeline_limit"} <= cols


def test_migration_upgrade_idempotent_on_direct_double_call(monkeypatch, tmp_path):
    """The SQL itself is guarded: calling upgrade() twice in a row (bypassing
    the alembic version table) must not error on the second pass."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "users_double.db"
    _create_old_schema_users(db_path)

    mig = importlib.import_module(
        "app.backend.alembic.versions.e6a7b8c9d0e1_add_user_role_and_limits")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()
            mig.upgrade()  # second pass must be a silent no-op
    engine.dispose()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT role, is_active, daily_pipeline_limit, "
            "concurrent_pipeline_limit FROM users WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("member", 1, 20, 3)


def test_migration_downgrade_safe_on_sqlite(monkeypatch, tmp_path):
    """SQLite downgrade is a documented no-op (column drops need a table
    rebuild) — must not raise."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "users_down.db"
    _create_old_schema_users(db_path)

    mig = importlib.import_module(
        "app.backend.alembic.versions.e6a7b8c9d0e1_add_user_role_and_limits")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mig.upgrade()
            mig.downgrade()  # no-op on SQLite, must not raise
    engine.dispose()


# ---------------------------------------------------------------------------
# 4. Runtime schema guard (boot-time self-heal, independent of Alembic)
# ---------------------------------------------------------------------------

def test_schema_guard_backfills_old_users_table(tmp_path):
    """The production incident: model gained columns, predeploy migration
    didn't land, every User query 500'd. The boot guard must heal it."""
    from app.backend.database.schema_guard import ensure_user_extensions

    db_path = tmp_path / "users_guard.db"
    _create_old_schema_users(db_path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    ensure_user_extensions(engine)
    engine.dispose()

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        row = conn.execute(
            "SELECT role, is_active, daily_pipeline_limit, "
            "concurrent_pipeline_limit FROM users WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert {"role", "is_active", "daily_pipeline_limit",
            "concurrent_pipeline_limit"} <= cols
    assert row == ("member", 1, 20, 3)


def test_schema_guard_noop_when_columns_present(tmp_path):
    from app.backend.database.schema_guard import ensure_user_extensions

    db_path = tmp_path / "users_guard_fresh.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    ensure_user_extensions(engine)   # silent no-op
    ensure_user_extensions(engine)   # still a no-op
    engine.dispose()

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()
    assert {"role", "is_active", "daily_pipeline_limit",
            "concurrent_pipeline_limit"} <= cols


def test_schema_guard_no_users_table_is_noop(tmp_path):
    """Before create_all has run there is nothing to patch."""
    from app.backend.database.schema_guard import ensure_user_extensions

    db_path = tmp_path / "empty_guard.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    ensure_user_extensions(engine)   # must not raise
    engine.dispose()


def test_ensure_all_swallows_guard_failures(monkeypatch, tmp_path):
    """A failing guard logs but never crashes startup — a crash loop is
    worse than serving with the diag endpoint reporting the gap."""
    from app.backend.database import schema_guard

    def _boom(engine):
        raise RuntimeError("simulated ALTER failure")

    monkeypatch.setattr(schema_guard, "ensure_user_extensions", _boom)
    db_path = tmp_path / "guard_fail.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    schema_guard.ensure_all(engine)  # must not raise
    engine.dispose()
