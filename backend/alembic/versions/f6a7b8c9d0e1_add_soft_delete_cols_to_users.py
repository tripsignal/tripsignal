"""add soft-delete columns to users

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'deleted_at',
        sa.TIMESTAMP(timezone=True),
        nullable=True,
    ))
    op.add_column('users', sa.Column(
        'deleted_by',
        sa.Text(),
        nullable=True,
    ))
    op.add_column('users', sa.Column(
        'deleted_reason',
        sa.Text(),
        nullable=True,
    ))
    op.add_column('users', sa.Column(
        'deleted_reason_other',
        sa.Text(),
        nullable=True,
    ))
    op.add_column('users', sa.Column(
        'stripe_canceled_at',
        sa.TIMESTAMP(timezone=True),
        nullable=True,
    ))
    op.add_column('users', sa.Column(
        'trial_expired_email_sent_at',
        sa.TIMESTAMP(timezone=True),
        nullable=True,
    ))
    op.create_index('ix_users_deleted_at', 'users', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('ix_users_deleted_at', table_name='users')
    op.drop_column('users', 'trial_expired_email_sent_at')
    op.drop_column('users', 'stripe_canceled_at')
    op.drop_column('users', 'deleted_reason_other')
    op.drop_column('users', 'deleted_reason')
    op.drop_column('users', 'deleted_by')
    op.drop_column('users', 'deleted_at')
