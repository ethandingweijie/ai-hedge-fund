"""add_user_role_and_limits

Revision ID: e6a7b8c9d0e1
Revises: d4e5f6a7b8c9
Create Date: 2026-08-09

Phase 3b — User model extensions for RBAC and per-user rate limits:
role ('admin'|'member'), is_active (soft-disable — deactivated users'
tokens are rejected), daily_pipeline_limit, concurrent_pipeline_limit.

All four columns are NOT NULL with server defaults so pre-existing rows
(the single production user) are backfilled in place: member / active /
20 / 3. Fresh installs get the columns from create_all, and both branches
below skip silently when the columns already exist.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e6a7b8c9d0e1'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                   "role VARCHAR(20) NOT NULL DEFAULT 'member'")
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                   "is_active BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                   "daily_pipeline_limit INTEGER NOT NULL DEFAULT 20")
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                   "concurrent_pipeline_limit INTEGER NOT NULL DEFAULT 3")
        return

    # SQLite: guarded ALTERs (skip when the columns already exist — a
    # fresh create_all from the model includes them).
    inspector = sa.inspect(bind)
    if "users" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "role" not in cols:
        op.add_column("users", sa.Column(
            "role", sa.String(20), nullable=False, server_default="member"))
    if "is_active" not in cols:
        op.add_column("users", sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("1")))
    if "daily_pipeline_limit" not in cols:
        op.add_column("users", sa.Column(
            "daily_pipeline_limit", sa.Integer(), nullable=False,
            server_default="20"))
    if "concurrent_pipeline_limit" not in cols:
        op.add_column("users", sa.Column(
            "concurrent_pipeline_limit", sa.Integer(), nullable=False,
            server_default="3"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS concurrent_pipeline_limit")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS daily_pipeline_limit")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_active")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role")
        return
    # SQLite: column drops require table rebuild — not worth it
    pass
