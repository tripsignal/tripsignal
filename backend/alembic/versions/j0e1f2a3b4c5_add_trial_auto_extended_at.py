"""add trial_auto_extended_at to users

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-03-01

Adds trial_auto_extended_at TIMESTAMP(timezone) nullable to users table.
Used to enforce one-time 7-day trial extension logic.
"""
from alembic import op
import sqlalchemy as sa


revision = 'j0e1f2a3b4c5'
down_revision = 'i9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'trial_auto_extended_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('users', 'trial_auto_extended_at')
