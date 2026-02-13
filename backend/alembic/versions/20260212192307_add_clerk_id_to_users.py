"""add clerk_id to users

Revision ID: $(openssl rand -hex 6)
Revises: 7320e9169655
Create Date: $(date -u +"%Y-%m-%d %H:%M:%S.%6N")

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '$(openssl rand -hex 6)'
down_revision = '7320e9169655'
branch_labels = None
depends_on = None


def upgrade():
    # Add clerk_id column to users table
    op.add_column('users', sa.Column('clerk_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_users_clerk_id'), 'users', ['clerk_id'], unique=True)


def downgrade():
    # Remove clerk_id column
    op.drop_index(op.f('ix_users_clerk_id'), table_name='users')
    op.drop_column('users', 'clerk_id')
