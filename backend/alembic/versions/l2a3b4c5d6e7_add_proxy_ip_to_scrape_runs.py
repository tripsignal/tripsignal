"""add proxy_ip to scrape_runs

Revision ID: l2a3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-03-02

Adds proxy_ip TEXT column to scrape_runs table.
"""
from alembic import op
import sqlalchemy as sa


revision = 'l2a3b4c5d6e7'
down_revision = 'k1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scrape_runs', sa.Column('proxy_ip', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('scrape_runs', 'proxy_ip')
