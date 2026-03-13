"""add price_delta_cents to deal_matches

Revision ID: d1e2f3g4h5i6
Revises: c0s1t2u3v4w5
Create Date: 2026-03-13

Stores the price delta (in cents) vs the previous match for the same
(signal_id, deal_id) pair. Nullable integer — NULL means no prior match.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d1e2f3g4h5i6"
down_revision = "c0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deal_matches",
        sa.Column("price_delta_cents", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deal_matches", "price_delta_cents")
