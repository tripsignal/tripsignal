"""Add provider column to scrape_runs.

Revision ID: v2k3l4m5n6o7
Revises: u1j2k3l4m5n6
Create Date: 2026-03-05
"""
from alembic import op
import sqlalchemy as sa

revision = "v2k3l4m5n6o7"
down_revision = "u1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scrape_runs",
        sa.Column("provider", sa.Text(), nullable=True),
    )
    # Backfill existing rows as selloff
    op.execute("UPDATE scrape_runs SET provider = 'selloff' WHERE provider IS NULL")


def downgrade() -> None:
    op.drop_column("scrape_runs", "provider")
