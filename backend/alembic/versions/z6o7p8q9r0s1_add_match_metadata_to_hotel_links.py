"""add match metadata to hotel_links

Revision ID: z6o7p8q9r0s1
Revises: y5n6o7p8q9r0
Create Date: 2026-03-11

Adds confidence, method, review status, and suggested URL columns
to hotel_links for TripAdvisor matching pipeline integration.
"""
from alembic import op
import sqlalchemy as sa


revision = "z6o7p8q9r0s1"
down_revision = "y5n6o7p8q9r0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hotel_links", sa.Column("tripadvisor_id", sa.Integer(), nullable=True))
    op.add_column("hotel_links", sa.Column("match_confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("hotel_links", sa.Column("match_method", sa.Text(), nullable=True))
    op.add_column("hotel_links", sa.Column("review_status", sa.Text(), nullable=True))
    op.add_column("hotel_links", sa.Column("suggested_url", sa.Text(), nullable=True))
    op.add_column("hotel_links", sa.Column("suggested_name", sa.Text(), nullable=True))
    op.add_column("hotel_links", sa.Column("match_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("hotel_links", "match_notes")
    op.drop_column("hotel_links", "suggested_name")
    op.drop_column("hotel_links", "suggested_url")
    op.drop_column("hotel_links", "review_status")
    op.drop_column("hotel_links", "match_method")
    op.drop_column("hotel_links", "match_confidence")
    op.drop_column("hotel_links", "tripadvisor_id")
