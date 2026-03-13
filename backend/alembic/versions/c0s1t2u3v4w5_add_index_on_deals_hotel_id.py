"""add index on deals.hotel_id

Revision ID: c0s1t2u3v4w5
Revises: b9r0s1t2u3v4
Create Date: 2026-03-12

The stats endpoint uses COUNT(DISTINCT hotel_id) on active deals.
Without an index this requires a full table scan on hotel_id.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c0s1t2u3v4w5"
down_revision = "b9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_deals_hotel_id", "deals", ["hotel_id"])


def downgrade() -> None:
    op.drop_index("ix_deals_hotel_id", table_name="deals")
