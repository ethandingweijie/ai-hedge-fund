"""Phase 3e — per-user API keys.

api_keys gains a nullable user_id owner (NULL = global admin-managed
key). Resolution chain: pipelines hydrate globals, overlaid with the
caller's own rows per provider. User rows never leak to other users or
to service/unauthenticated calls.

Covered:
1. Model — composite (user_id, provider) uniqueness lets a global key
   and per-user overrides coexist for the same provider.
2. Repository — owner-scoped CRUD: global rows are untouched by
   per-user writes and vice versa.
3. Service — get_api_keys_dict resolution chain (None → globals only,
   uid → globals + overrides, inactive rows excluded).
4. Alembic migration — pre-3e api_keys table gains user_id; chain head
   is f8c9d0e1a2b3.
5. Schema guard — boot-time backfill of api_keys.user_id.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.database.models import ApiKey, User
from app.backend.database import schema_guard
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services.api_key_service import ApiKeyService

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = REPO_ROOT / "app" / "backend" / "alembic"


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


def _make_user(db, n: int) -> User:
    user = User(email=f"u{n}@example.com", provider="google",
                provider_sub=f"sub-{n}")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _create_old_schema_api_keys(db_path: Path) -> None:
    """api_keys as it existed before Phase 3e — provider-only UNIQUE,
    no user_id — with one legacy global row."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME,
                updated_at DATETIME,
                provider VARCHAR(100) NOT NULL,
                key_value TEXT NOT NULL,
                is_active BOOLEAN,
                description TEXT,
                last_used DATETIME
            );
            CREATE UNIQUE INDEX ix_api_keys_provider
                ON api_keys (provider);
            INSERT INTO api_keys (provider, key_value, is_active)
            VALUES ('ANTHROPIC_API_KEY', 'legacy-global', 1);
            """
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Model: composite uniqueness
# ---------------------------------------------------------------------------

def test_global_and_user_key_same_provider_coexist(orm_db):
    alice = _make_user(orm_db, 1)
    orm_db.add(ApiKey(provider="ANTHROPIC_API_KEY", key_value="global-k",
                      user_id=None))
    orm_db.add(ApiKey(provider="ANTHROPIC_API_KEY", key_value="alice-k",
                      user_id=alice.id))
    orm_db.commit()
    rows = orm_db.query(ApiKey).filter(
        ApiKey.provider == "ANTHROPIC_API_KEY").all()
    assert {r.user_id for r in rows} == {None, alice.id}


def test_duplicate_user_key_same_provider_rejected(orm_db):
    """Same owner + provider twice → composite UNIQUE violation."""
    alice = _make_user(orm_db, 1)
    orm_db.add(ApiKey(provider="P", key_value="a", user_id=alice.id))
    orm_db.commit()
    orm_db.add(ApiKey(provider="P", key_value="b", user_id=alice.id))
    with pytest.raises(IntegrityError):
        orm_db.commit()
    orm_db.rollback()


# ---------------------------------------------------------------------------
# 2. Repository: owner-scoped CRUD
# ---------------------------------------------------------------------------

def test_create_global_then_user_row(orm_db):
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    g = repo.create_or_update_api_key("P", "global-v")
    u = repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)
    assert g.id != u.id
    assert g.user_id is None
    assert u.user_id == alice.id


def test_user_upsert_updates_user_row_not_global(orm_db):
    """Regression: upserting a user key must match on (provider, owner),
    not provider alone — otherwise it would clobber the global key."""
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "alice-v1", user_id=alice.id)
    repo.create_or_update_api_key("P", "alice-v2", user_id=alice.id)
    assert repo.get_api_key_by_provider("P").key_value == "global-v"
    assert repo.get_user_api_keys(alice.id)[0].key_value == "alice-v2"
    assert orm_db.query(ApiKey).count() == 2  # no extra rows


def test_global_upsert_targets_global_row_only(orm_db):
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)
    repo.create_or_update_api_key("P", "global-v")  # None owner
    assert repo.get_api_key_by_provider("P").key_value == "global-v"
    assert repo.get_user_api_keys(alice.id)[0].key_value == "alice-v"


def test_global_scoped_crud_ignores_user_rows(orm_db):
    """update/delete/deactivate/last-used all hit the GLOBAL row even
    when a user row with the same provider exists."""
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)

    updated = repo.update_api_key("P", key_value="global-v2")
    assert updated.user_id is None
    assert repo.get_user_api_keys(alice.id)[0].key_value == "alice-v"

    assert repo.deactivate_api_key("P") is True
    assert repo.get_api_key_by_provider("P") is None  # inactive globals hidden
    assert repo.get_user_api_keys(alice.id)[0].is_active is True

    assert repo.delete_api_key("P") is True
    assert repo.get_user_api_keys(alice.id)[0].provider == "P"  # still there


def test_get_global_and_user_key_lists_are_disjoint(orm_db):
    alice, bob = _make_user(orm_db, 1), _make_user(orm_db, 2)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("G1", "g1")
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)
    repo.create_or_update_api_key("P", "bob-v", user_id=bob.id)
    assert {k.provider for k in repo.get_global_api_keys()} == {"G1"}
    assert {k.provider for k in repo.get_user_api_keys(alice.id)} == {"P"}
    assert len(repo.get_all_api_keys()) == 3  # admin listing sees all owners


def test_owner_targeted_delete_and_deactivate(orm_db):
    """Admin CRUD with an explicit user_id hits ONLY that owner's row."""
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)

    assert repo.deactivate_api_key("P", user_id=alice.id) is True
    assert repo.get_api_key_by_provider("P").key_value == "global-v"
    # Just-deactivated row is still fetchable with include_inactive —
    # the deactivate ROUTE re-fetches it for its response body.
    assert repo.get_api_key_by_provider(
        "P", user_id=alice.id) is None
    assert repo.get_api_key_by_provider(
        "P", user_id=alice.id, include_inactive=True).is_active is False

    assert repo.delete_api_key("P", user_id=alice.id) is True
    assert repo.get_api_key_by_provider("P").key_value == "global-v"
    assert repo.get_user_api_keys(alice.id) == []


# ---------------------------------------------------------------------------
# 3. Service: resolution chain
# ---------------------------------------------------------------------------

def test_no_user_gets_globals_only(orm_db):
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id)
    # user_id=None (service/unauthenticated) must NOT see alice's key
    assert ApiKeyService(orm_db).get_api_keys_dict() == {"P": "global-v"}


def test_user_without_own_keys_gets_globals(orm_db):
    alice, bob = _make_user(orm_db, 1), _make_user(orm_db, 2)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "bob-v", user_id=bob.id)
    # alice has no keys of her own → plain globals; bob's row must not leak
    assert ApiKeyService(orm_db).get_api_keys_dict(alice.id) == \
        {"P": "global-v"}


def test_user_keys_override_globals_per_provider(orm_db):
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P1", "global-1")
    repo.create_or_update_api_key("P2", "global-2")
    repo.create_or_update_api_key("P1", "alice-1", user_id=alice.id)
    merged = ApiKeyService(orm_db).get_api_keys_dict(alice.id)
    assert merged == {"P1": "alice-1", "P2": "global-2"}


def test_inactive_rows_excluded_from_resolution(orm_db):
    alice = _make_user(orm_db, 1)
    repo = ApiKeyRepository(orm_db)
    repo.create_or_update_api_key("P", "global-v")
    repo.create_or_update_api_key("P", "alice-v", user_id=alice.id,
                                  is_active=False)
    # alice's inactive override doesn't apply → global still wins
    assert ApiKeyService(orm_db).get_api_keys_dict(alice.id) == \
        {"P": "global-v"}


# ---------------------------------------------------------------------------
# 4. Alembic migration
# ---------------------------------------------------------------------------

def test_migration_chain_head():
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["f8c9d0e1a2b3"]


def test_migration_backfills_user_id_on_old_schema(monkeypatch, tmp_path):
    """Pre-3e api_keys table → upgrade adds the user_id column and the
    legacy row stays global (NULL owner)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "api_keys_old.db"
    _create_old_schema_api_keys(db_path)

    cfg = _alembic_config(db_path)
    command.stamp(cfg, "e6a7b8c9d0e1")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        row = conn.execute(
            "SELECT user_id, key_value FROM api_keys "
            "WHERE provider = 'ANTHROPIC_API_KEY'").fetchone()
    finally:
        conn.close()
    assert "user_id" in cols
    assert row == (None, "legacy-global")


def test_migration_noop_after_create_all(monkeypatch, tmp_path):
    """Fresh installs already have user_id from the model; the SQLite
    branch must inspect-and-skip."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "api_keys_fresh.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()

    cfg = _alembic_config(db_path)
    command.stamp(cfg, "e6a7b8c9d0e1")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
    finally:
        conn.close()
    assert "user_id" in cols


# ---------------------------------------------------------------------------
# 5. Schema guard
# ---------------------------------------------------------------------------

def _old_schema_engine(tmp_path):
    db_path = tmp_path / "guard_old.db"
    _create_old_schema_api_keys(db_path)
    return create_engine(f"sqlite:///{db_path.as_posix()}")


def test_schema_guard_backfills_api_keys_user_id(tmp_path):
    engine = _old_schema_engine(tmp_path)
    try:
        schema_guard.ensure_api_key_user_scope(engine)
        import sqlalchemy as sa
        cols = {c["name"] for c in sa.inspect(engine).get_columns("api_keys")}
        assert "user_id" in cols
        # idempotent
        schema_guard.ensure_api_key_user_scope(engine)
    finally:
        engine.dispose()


def test_schema_guard_noop_when_column_present(tmp_path):
    db_path = tmp_path / "guard_fresh.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    try:
        schema_guard.ensure_api_key_user_scope(engine)  # must not raise
    finally:
        engine.dispose()


def test_schema_guard_noop_when_table_missing(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    try:
        schema_guard.ensure_api_key_user_scope(engine)  # must not raise
    finally:
        engine.dispose()


def test_ensure_all_covers_api_keys(tmp_path):
    """ensure_all (boot hook) backfills api_keys too, not just users."""
    engine = _old_schema_engine(tmp_path)
    try:
        schema_guard.ensure_all(engine)
        import sqlalchemy as sa
        cols = {c["name"] for c in sa.inspect(engine).get_columns("api_keys")}
        assert "user_id" in cols
    finally:
        engine.dispose()
