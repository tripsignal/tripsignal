"""add error_log and deals_deactivated to scrape_runs

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scrape_runs', sa.Column('error_log', JSONB, nullable=True))
    op.add_column('scrape_runs', sa.Column('deals_deactivated', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('scrape_runs', 'deals_deactivated')
    op.drop_column('scrape_runs', 'error_log')
