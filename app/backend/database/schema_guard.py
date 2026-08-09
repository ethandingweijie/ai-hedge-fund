"""
app/backend/database/schema_guard.py
=====================================
Runtime schema guards for columns the ORM's create_all cannot add to
tables that already exist.

Railway's predeploy hook runs scripts/sync_schema.py (create_all +
Alembic), but when that hook doesn't fire or its Alembic step no-ops,
the app would otherwise serve with a stale schema — and any ORM query
against a model column the database lacks is a hard 500 (this happened
in production on the Phase 3b deploy: the users table missed the four
new columns and every authenticated route broke).

Each guard inspects first and ALTERs only missing columns, so it is
idempotent and safe to run on every boot on both PostgreSQL and SQLite.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa

logger = logging.getLogger(__name__)


def ensure_user_extensions(engine: sa.engine.Engine) -> None:
    """Phase 3b user columns: role / is_active / daily_pipeline_limit /
    concurrent_pipeline_limit. NOT NULL with server defaults so existing
    rows are backfilled in place (member / active / 20 / 3)."""
    inspector = sa.inspect(engine)
    if "users" not in inspector.get_table_names():
        return  # nothing to patch — create_all hasn't produced the table yet
    cols = {c["name"] for c in inspector.get_columns("users")}

    bool_default = "TRUE" if engine.dialect.name == "postgresql" else "1"
    pending: list[str] = []
    if "role" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN role VARCHAR(20) "
            "NOT NULL DEFAULT 'member'")
    if "is_active" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN is_active BOOLEAN "
            f"NOT NULL DEFAULT {bool_default}")
    if "daily_pipeline_limit" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN daily_pipeline_limit INTEGER "
            "NOT NULL DEFAULT 20")
    if "concurrent_pipeline_limit" not in cols:
        pending.append(
            "ALTER TABLE users ADD COLUMN concurrent_pipeline_limit INTEGER "
            "NOT NULL DEFAULT 3")

    if not pending:
        return
    with engine.begin() as conn:
        for stmt in pending:
            conn.execute(sa.text(stmt))
    logger.info(
        "Schema guard: added %d missing users column(s): %s",
        len(pending),
        [s.split("ADD COLUMN ")[1].split(" ")[0] for s in pending],
    )


def ensure_api_key_user_scope(engine: sa.engine.Engine) -> None:
    """Phase 3e per-user API keys: api_keys.user_id owner column (NULL =
    global key). On PostgreSQL also swaps the provider-only UNIQUE
    constraint for the composite (user_id, provider) so per-user
    overrides can coexist with the global key for the same provider.
    SQLite can't drop constraints without a table rebuild — dev-only, so
    the column backfill is all it gets (fresh SQLite DBs get the
    composite constraint from create_all)."""
    inspector = sa.inspect(engine)
    if "api_keys" not in inspector.get_table_names():
        return  # nothing to patch — create_all hasn't produced the table yet
    cols = {c["name"] for c in inspector.get_columns("api_keys")}

    if "user_id" not in cols:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "ALTER TABLE api_keys ADD COLUMN user_id INTEGER "
                "REFERENCES users(id)"))
        logger.info("Schema guard: added api_keys.user_id column")

    if engine.dialect.name != "postgresql":
        return

    # Constraint swap (inspect-then-act, idempotent).
    inspector = sa.inspect(engine)
    drop_names = [uc["name"] for uc in inspector.get_unique_constraints("api_keys")
                  if uc["column_names"] == ["provider"]]
    have_composite = any(uc["name"] == "uq_api_keys_user_provider"
                         for uc in inspector.get_unique_constraints("api_keys"))
    if not drop_names and have_composite:
        return
    with engine.begin() as conn:
        for name in drop_names:
            conn.execute(sa.text(
                f'ALTER TABLE api_keys DROP CONSTRAINT "{name}"'))
        if not have_composite:
            conn.execute(sa.text(
                "ALTER TABLE api_keys ADD CONSTRAINT "
                "uq_api_keys_user_provider UNIQUE (user_id, provider)"))
    logger.info("Schema guard: api_keys uniqueness now (user_id, provider)")


def ensure_all(engine: sa.engine.Engine) -> None:
    """Run every runtime schema guard. Called at boot (main.py) and by
    scripts/sync_schema.py. Failures are logged, not raised — a startup
    crash loop is worse than serving with the diag endpoint reporting
    the missing columns."""
    for guard in (ensure_user_extensions, ensure_api_key_user_scope):
        try:
            guard(engine)
        except Exception:
            logger.exception("Schema guard failed — database may be stale")
