"""create hotel_intel table

Revision ID: d1t2u3v4w5x6
Revises: c0s1t2u3v4w5
Create Date: 2026-03-13

Hotel intelligence data from Gemini research.
Includes index on LOWER(hotel_name) for case-insensitive lookups
and unique constraint on hotel_name.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "d1t2u3v4w5x6"
down_revision = "c0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hotel_intel",
        sa.Column("hotel_id", sa.Text(), nullable=False),
        sa.Column("hotel_name", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("total_rooms", sa.Integer(), nullable=True),
        sa.Column("resort_size", sa.Text(), nullable=True),
        sa.Column("adults_only", sa.Boolean(), nullable=True),
        sa.Column("kids_club", sa.Boolean(), nullable=True),
        sa.Column("teen_club", sa.Boolean(), nullable=True),
        sa.Column("waterpark", sa.Boolean(), nullable=True),
        sa.Column("num_restaurants", sa.Integer(), nullable=True),
        sa.Column("transfer_time_minutes", sa.Integer(), nullable=True),
        sa.Column("sargassum_risk", sa.Text(), nullable=True),
        sa.Column("sargassum_notes", sa.Text(), nullable=True),
        sa.Column("vibe", sa.Text(), nullable=True),
        sa.Column("red_flags", JSONB(), nullable=True),
        sa.Column("top_complaints", JSONB(), nullable=True),
        sa.Column("top_praise", JSONB(), nullable=True),
        sa.Column("accommodates_5", sa.Boolean(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True, server_default=sa.text("'gemini'")),
        sa.Column("researched_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("hotel_id"),
        sa.UniqueConstraint("hotel_name"),
    )
    op.create_index("ix_hotel_intel_hotel_name", "hotel_intel", ["hotel_name"])
    op.create_index("ix_hotel_intel_hotel_name_lower", "hotel_intel", [sa.text("LOWER(hotel_name)")])


def downgrade() -> None:
    op.drop_index("ix_hotel_intel_hotel_name_lower", table_name="hotel_intel")
    op.drop_index("ix_hotel_intel_hotel_name", table_name="hotel_intel")
    op.drop_table("hotel_intel")
