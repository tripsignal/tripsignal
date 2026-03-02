"""add login tracking columns to users

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-01

"""
from alembic import op
import sqlalchemy as sa

revision = 'g7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('login_count', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('users', sa.Column('last_login_ip', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('last_login_user_agent', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'last_login_user_agent')
    op.drop_column('users', 'last_login_ip')
    op.drop_column('users', 'login_count')
