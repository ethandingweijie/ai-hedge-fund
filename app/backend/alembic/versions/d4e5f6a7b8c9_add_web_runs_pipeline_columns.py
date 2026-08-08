"""add_web_runs_pipeline_columns

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-08-08

The Phase 1b web_runs table omitted run_at and model_name, which
analysis_service writes on every checkpoint/save. Add them so the
pipeline can persist runs in Postgres.

SQLite note: analysis_service._migrate_web_runs_columns already applies
these via PRAGMA-guarded ALTERs at runtime; this migration is defensive
and skips silently if the table is absent or the columns exist.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE web_runs ADD COLUMN IF NOT EXISTS run_at TEXT")
        op.execute("ALTER TABLE web_runs ADD COLUMN IF NOT EXISTS model_name VARCHAR(100)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_web_runs_run_at ON web_runs(run_at)")
        return

    # SQLite: guarded ALTERs (skip if the table doesn't exist yet — the
    # service module creates it with the full schema on first use).
    inspector = sa.inspect(bind)
    if "web_runs" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("web_runs")}
    if "run_at" not in cols:
        op.add_column("web_runs", sa.Column("run_at", sa.Text(), nullable=True))
    if "model_name" not in cols:
        op.add_column("web_runs", sa.Column("model_name", sa.String(100), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_web_runs_run_at")
        op.execute("ALTER TABLE web_runs DROP COLUMN IF EXISTS model_name")
        op.execute("ALTER TABLE web_runs DROP COLUMN IF EXISTS run_at")
        return
    # SQLite: column drops require table rebuild — not worth it
    pass
