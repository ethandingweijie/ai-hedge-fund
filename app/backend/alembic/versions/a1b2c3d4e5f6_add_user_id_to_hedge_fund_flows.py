"""add_user_id_to_hedge_fund_flows

Revision ID: a1b2c3d4e5f6
Revises: b92dcde35467
Create Date: 2026-08-08

Adds user_id FK to hedge_fund_flows for multi-user isolation.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'b92dcde35467'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('hedge_fund_flows', sa.Column('user_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_hedge_fund_flows_user_id'), 'hedge_fund_flows', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_hedge_fund_flows_user_id'), table_name='hedge_fund_flows')
    op.drop_column('hedge_fund_flows', 'user_id')
