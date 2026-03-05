"""Add last_seen_at and missed_cycles to deals for graduated staleness.

Revision ID: u1j2k3l4m5n6
Revises: s9h0i1j2k3l4
Create Date: 2026-03-05
"""
from alembic import op
import sqlalchemy as sa

revision = "u1j2k3l4m5n6"
down_revision = "s9h0i1j2k3l4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "deals",
        sa.Column(
            "missed_cycles",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Backfill: best approximation for existing deals
    op.execute("UPDATE deals SET last_seen_at = found_at WHERE last_seen_at = now()")


def downgrade() -> None:
    op.drop_column("deals", "missed_cycles")
    op.drop_column("deals", "last_seen_at")
