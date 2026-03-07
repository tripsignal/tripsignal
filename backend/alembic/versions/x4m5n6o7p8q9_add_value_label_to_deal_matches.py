"""Add value_label to deal_matches table.

Revision ID: x4m5n6o7p8q9
Revises: w3l4m5n6o7p8
Create Date: 2026-03-07
"""
from alembic import op
import sqlalchemy as sa

revision = "x4m5n6o7p8q9"
down_revision = "w3l4m5n6o7p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deal_matches",
        sa.Column("value_label", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deal_matches", "value_label")
