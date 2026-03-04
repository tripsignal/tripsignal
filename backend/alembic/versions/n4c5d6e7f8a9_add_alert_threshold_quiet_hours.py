"""add alert_threshold, timezone, quiet_hours to users

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'n4c5d6e7f8a9'
down_revision = 'm3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('alert_threshold', sa.Integer(), nullable=False, server_default=sa.text("10")))
    op.add_column('users', sa.Column('timezone', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('quiet_hours_enabled', sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column('users', sa.Column('quiet_hours_start', sa.Text(), nullable=False, server_default=sa.text("'21:00'")))
    op.add_column('users', sa.Column('quiet_hours_end', sa.Text(), nullable=False, server_default=sa.text("'08:00'")))


def downgrade() -> None:
    op.drop_column('users', 'quiet_hours_end')
    op.drop_column('users', 'quiet_hours_start')
    op.drop_column('users', 'quiet_hours_enabled')
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'alert_threshold')
