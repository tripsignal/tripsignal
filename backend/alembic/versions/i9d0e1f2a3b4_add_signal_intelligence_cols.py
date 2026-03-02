"""add signal intelligence columns

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-03-01

Adds price-tracking intelligence fields to signals table:
- last_check_min_price: cheapest matching deal price (cents) from the most recent scrape
- last_check_at: when the signal was last evaluated
- all_time_low_price: lowest price (cents) ever seen for this signal
- all_time_low_at: when the all-time low was recorded

All columns are nullable with no defaults — existing rows get NULL,
no backfill required. Fully backward-compatible.
"""
from alembic import op
import sqlalchemy as sa


revision = 'i9d0e1f2a3b4'
down_revision = 'h8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('signals', sa.Column(
        'last_check_min_price', sa.Integer(), nullable=True,
    ))
    op.add_column('signals', sa.Column(
        'last_check_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))
    op.add_column('signals', sa.Column(
        'all_time_low_price', sa.Integer(), nullable=True,
    ))
    op.add_column('signals', sa.Column(
        'all_time_low_at', sa.TIMESTAMP(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('signals', 'all_time_low_at')
    op.drop_column('signals', 'all_time_low_price')
    op.drop_column('signals', 'last_check_at')
    op.drop_column('signals', 'last_check_min_price')
