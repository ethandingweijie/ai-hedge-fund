"""create_run_archive_tables_postgres

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-08

Creates the run_archive tables in Postgres for multi-instance deployment.
These tables were previously in SQLite (src/data/run_archive.db).

Key tables migrated:
- web_runs: pipeline checkpoints and results
- complacency_jobs: research job state
- watchlist: user watchlists
- screener_cache, fast_vgpm_cache: screener caches
- dd_alerts, dd_reports: DD alert state
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── web_runs: pipeline checkpoints and results ──────────────────────────
    op.create_table(
        'web_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(64), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('full_result_json', sa.Text(), nullable=True),
        sa.Column('is_checkpoint', sa.Boolean(), default=False),
        sa.Column('archive_run_id', sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_web_runs_run_id', 'web_runs', ['run_id'], unique=True)
    op.create_index('ix_web_runs_user_id', 'web_runs', ['user_id'])
    op.create_index('ix_web_runs_ticker', 'web_runs', ['ticker'])

    # ── complacency_jobs: research job state ────────────────────────────────
    op.create_table(
        'complacency_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(32), nullable=False),
        sa.Column('kind', sa.String(50), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('progress_msg', sa.Text(), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_complacency_jobs_job_id', 'complacency_jobs', ['job_id'], unique=True)
    op.create_index('ix_complacency_jobs_kind', 'complacency_jobs', ['kind'])
    op.create_index('ix_complacency_jobs_status', 'complacency_jobs', ['status'])

    # ── watchlist: user watchlists ──────────────────────────────────────────
    op.create_table(
        'watchlist',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('ticker', sa.String(20), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('vgpm_json', sa.Text(), nullable=True),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('vgpm_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('vgpm_source', sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_watchlist_user_ticker', 'watchlist', ['user_id', 'ticker'], unique=True)

    # ── screener_cache: screener results ────────────────────────────────────
    op.create_table(
        'screener_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(255), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_screener_cache_key', 'screener_cache', ['cache_key'], unique=True)

    # ── fast_vgpm_cache: fast VGPM estimates ────────────────────────────────
    op.create_table(
        'fast_vgpm_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=False),
        sa.Column('vgpm_json', sa.Text(), nullable=True),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fast_vgpm_cache_ticker', 'fast_vgpm_cache', ['ticker'], unique=True)

    # ── dd_alerts: DD alert state ───────────────────────────────────────────
    op.create_table(
        'dd_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alert_id', sa.String(64), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=False),
        sa.Column('trigger_type', sa.String(50), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('quote_json', sa.Text(), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dd_alerts_alert_id', 'dd_alerts', ['alert_id'], unique=True)
    op.create_index('ix_dd_alerts_ticker', 'dd_alerts', ['ticker'])

    # ── dd_reports: DD report results ───────────────────────────────────────
    op.create_table(
        'dd_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.String(64), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('full_result_json', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dd_reports_report_id', 'dd_reports', ['report_id'], unique=True)


def downgrade() -> None:
    op.drop_table('dd_reports')
    op.drop_table('dd_alerts')
    op.drop_table('fast_vgpm_cache')
    op.drop_table('screener_cache')
    op.drop_table('watchlist')
    op.drop_table('complacency_jobs')
    op.drop_table('web_runs')
