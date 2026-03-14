"""add composite index on deal_price_history (deal_id, recorded_at DESC)

Revision ID: z8q9r0s1t2u3
Revises: z7p8q9r0s1t2
Create Date: 2026-03-13

Improves query performance for price delta lookups on the public deal page.
"""
from alembic import op


revision = "z8q9r0s1t2u3"
down_revision = "z7p8q9r0s1t2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_deal_price_history_deal_id_recorded_at_desc "
        "ON deal_price_history (deal_id, recorded_at DESC)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_deal_price_history_deal_id_recorded_at_desc"
    )
