"""add_api_key_user_scope

Revision ID: f8c9d0e1a2b3
Revises: e6a7b8c9d0e1
Create Date: 2026-08-09

Phase 3e — per-user API keys. api_keys gains a nullable user_id owner
(NULL = global admin-managed key), and the provider-only UNIQUE
constraint is replaced by the composite (user_id, provider) so a global
key and per-user overrides for the same provider can coexist.

SQLite cannot DROP constraints without a table rebuild — it's the local
dev fallback only, so there we just add the column; fresh SQLite DBs get
the composite constraint from create_all. PostgreSQL does the full
constraint swap, inspect-then-act so re-runs are no-ops.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f8c9d0e1a2b3'
down_revision = 'e6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS "
                   "user_id INTEGER REFERENCES users(id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_api_keys_user_id "
                   "ON api_keys (user_id)")
        # Swap provider-only uniqueness for the composite constraint.
        inspector = sa.inspect(bind)
        for uc in inspector.get_unique_constraints("api_keys"):
            if uc["column_names"] == ["provider"]:
                op.drop_constraint(uc["name"], "api_keys", type_="unique")
        # Re-inspect after the drop.
        existing = {uc["name"] for uc in
                    sa.inspect(bind).get_unique_constraints("api_keys")}
        if "uq_api_keys_user_provider" not in existing:
            op.create_unique_constraint(
                "uq_api_keys_user_provider", "api_keys",
                ["user_id", "provider"])
        return

    # SQLite: guarded ADD COLUMN only (constraint swap not possible
    # without a table rebuild; dev-only database). The column is added
    # WITHOUT the FK — Alembic's SQLite impl cannot ALTER constraints,
    # and SQLite wouldn't enforce an ALTER-added FK anyway.
    inspector = sa.inspect(bind)
    if "api_keys" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("api_keys")}
    if "user_id" not in cols:
        op.add_column("api_keys", sa.Column(
            "user_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS "
                   "uq_api_keys_user_provider")
        # Restore the original provider-only uniqueness.
        existing = {uc["name"] for uc in
                    sa.inspect(bind).get_unique_constraints("api_keys")}
        if "api_keys_provider_key" not in existing:
            op.create_unique_constraint(
                "api_keys_provider_key", "api_keys", ["provider"])
        op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS user_id")
        return
    # SQLite: column drops require table rebuild — not worth it
    pass
