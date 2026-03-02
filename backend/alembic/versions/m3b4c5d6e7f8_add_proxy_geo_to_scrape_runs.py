"""add proxy_geo to scrape_runs

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'm3b4c5d6e7f8'
down_revision = 'l2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scrape_runs', sa.Column('proxy_geo', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('scrape_runs', 'proxy_geo')
