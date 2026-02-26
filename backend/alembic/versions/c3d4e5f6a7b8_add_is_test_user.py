"""add is_test_user to users

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'is_test_user',
        sa.Boolean(),
        nullable=False,
        server_default=sa.text('false'),
    ))
    op.create_index('ix_users_is_test_user', 'users', ['is_test_user'])


def downgrade() -> None:
    op.drop_index('ix_users_is_test_user', table_name='users')
    op.drop_column('users', 'is_test_user')
