"""Add reactivated_at to deals table.

Revision ID: y5n6o7p8q9r0
Revises: x4m5n6o7p8q9
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = "y5n6o7p8q9r0"
down_revision = "x4m5n6o7p8q9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("reactivated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_deals_reactivated_at", "deals", ["reactivated_at"])


def downgrade() -> None:
    op.drop_index("ix_deals_reactivated_at", table_name="deals")
    op.drop_column("deals", "reactivated_at")
