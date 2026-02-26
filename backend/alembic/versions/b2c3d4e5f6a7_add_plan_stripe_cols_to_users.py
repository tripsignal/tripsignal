"""add plan and stripe cols to users

Revision ID: b2c3d4e5f6a7
Revises: 61733afcd940
Create Date: 2026-02-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = '61733afcd940'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('plan_type', sa.Text(), server_default='free', nullable=False))
    op.add_column('users', sa.Column('plan_status', sa.Text(), server_default='active', nullable=False))
    op.add_column('users', sa.Column('trial_ends_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('users', sa.Column('stripe_customer_id', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('stripe_subscription_id', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('stripe_subscription_status', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('subscription_current_period_end', sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.drop_column('users', 'subscription_current_period_end')
    op.drop_column('users', 'stripe_subscription_status')
    op.drop_column('users', 'stripe_subscription_id')
    op.drop_column('users', 'stripe_customer_id')
    op.drop_column('users', 'trial_ends_at')
    op.drop_column('users', 'plan_status')
    op.drop_column('users', 'plan_type')
