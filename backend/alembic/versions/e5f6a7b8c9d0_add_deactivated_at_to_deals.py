"""add deactivated_at to deals

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('deals', sa.Column(
        'deactivated_at',
        sa.TIMESTAMP(timezone=True),
        nullable=True,
    ))
    op.create_index('ix_deals_deactivated_at', 'deals', ['deactivated_at'])


def downgrade() -> None:
    op.drop_index('ix_deals_deactivated_at', table_name='deals')
    op.drop_column('deals', 'deactivated_at')
