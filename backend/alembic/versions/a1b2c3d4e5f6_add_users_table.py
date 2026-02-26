"""add users table

Revision ID: a1b2c3d4e5f6
Revises: 971d327f1066
Create Date: 2026-02-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '971d327f1066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('clerk_id', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('plan_type', sa.Text(), server_default='free', nullable=False),
        sa.Column('plan_status', sa.Text(), server_default='active', nullable=False),
        sa.Column('trial_ends_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('stripe_customer_id', sa.Text(), nullable=True),
        sa.Column('stripe_subscription_id', sa.Text(), nullable=True),
        sa.Column('stripe_subscription_status', sa.Text(), nullable=True),
        sa.Column('subscription_current_period_end', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_clerk_id', 'users', ['clerk_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_clerk_id', table_name='users')
    op.drop_table('users')