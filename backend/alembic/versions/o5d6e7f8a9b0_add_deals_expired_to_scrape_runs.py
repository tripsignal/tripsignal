"""add deals_expired to scrape_runs

Revision ID: o5d6e7f8a9b0
Revises: n4c5d6e7f8a9
Create Date: 2026-03-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'o5d6e7f8a9b0'
down_revision = 'n4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scrape_runs', sa.Column('deals_expired', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('scrape_runs', 'deals_expired')
