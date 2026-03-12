"""add tripadvisor scraped data columns to hotel_links

Revision ID: z7p8q9r0s1t2
Revises: z6o7p8q9r0s1
Create Date: 2026-03-12

Adds ta_rating, ta_review_count, ta_ranking_text, ta_data_fetched_at
columns to hotel_links for storing scraped TripAdvisor data.
"""
from alembic import op
import sqlalchemy as sa


revision = "z7p8q9r0s1t2"
down_revision = "z6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hotel_links", sa.Column("ta_rating", sa.Float(), nullable=True))
    op.add_column("hotel_links", sa.Column("ta_review_count", sa.Integer(), nullable=True))
    op.add_column("hotel_links", sa.Column("ta_ranking_text", sa.Text(), nullable=True))
    op.add_column("hotel_links", sa.Column("ta_data_fetched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("hotel_links", "ta_data_fetched_at")
    op.drop_column("hotel_links", "ta_ranking_text")
    op.drop_column("hotel_links", "ta_review_count")
    op.drop_column("hotel_links", "ta_rating")
