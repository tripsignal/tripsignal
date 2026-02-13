"""add clerk_id to users

Revision ID: add_clerk_id_001
Revises: 7320e9169655
Create Date: 2026-02-12

"""
from alembic import op
import sqlalchemy as sa


revision = 'add_clerk_id_001'
down_revision = '7320e9169655'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('clerk_id', sa.String(), nullable=True))
    op.create_index('ix_users_clerk_id', 'users', ['clerk_id'], unique=True)


def downgrade():
    op.drop_index('ix_users_clerk_id', table_name='users')
    op.drop_column('users', 'clerk_id')
