"""Expand hotel_intel with full pipeline schema + full_data JSONB."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1h1b2c3d4e5"
down_revision = "d1e2f3g4h5i6"
branch_labels = None
depends_on = None


def upgrade():
    # Existing columns (do NOT re-add):
    # hotel_id, hotel_name, destination, total_rooms, resort_size, adults_only,
    # kids_club, teen_club, waterpark, num_restaurants, transfer_time_minutes,
    # sargassum_risk, sargassum_notes, vibe, red_flags, top_complaints, top_praise,
    # accommodates_5, source, researched_at, created_at, updated_at

    columns_to_add = [
        # Identity
        ("star_rating", sa.Numeric(2, 1)),
        ("official_website", sa.Text()),
        ("resort_chain", sa.Text()),
        ("loyalty_program", sa.Text()),

        # Family / rooms
        ("room_fit_for_5_type", sa.Text()),
        ("room_types_for_5", JSONB()),
        ("max_occupancy_standard_room", sa.Integer()),
        ("max_occupancy_any_room", sa.Integer()),
        ("connecting_rooms_available", sa.Boolean()),
        ("cribs_available", sa.Boolean()),
        ("rollaway_beds", sa.Boolean()),

        # Kids
        ("kids_club_ages", sa.Text()),
        ("kids_club_hours", sa.Text()),
        ("teen_club_ages", sa.Text()),
        ("waterpark_notes", sa.Text()),
        ("kids_pool", sa.Boolean()),
        ("babysitting_available", sa.Boolean()),

        # Property
        ("resort_layout", sa.Text()),
        ("last_renovation_year", sa.Integer()),
        ("primary_demographics", sa.Text()),

        # Food
        ("restaurant_names", JSONB()),
        ("cuisine_types", JSONB()),
        ("num_bars", sa.Integer()),
        ("buffet_available", sa.Boolean()),
        ("room_service_24h", sa.Boolean()),
        ("food_quality_notes", sa.Text()),

        # Beach / Pool
        ("beach_access", sa.Boolean()),
        ("beach_type", sa.Text()),
        ("beach_description", sa.Text()),
        ("pool_count", sa.Integer()),
        ("pool_types", JSONB()),

        # Location
        ("nearest_airport_code", sa.Text()),
        ("airport_transfer_included", sa.Boolean()),
        ("surrounding_area", sa.Text()),

        # Reviews
        ("tripadvisor_rating", sa.Numeric(2, 1)),
        ("tripadvisor_review_count", sa.Integer()),
        ("google_rating", sa.Numeric(2, 1)),
        ("google_review_count", sa.Integer()),
        ("best_time_to_visit", sa.Text()),

        # Quality metadata
        ("field_confidence", JSONB()),
        ("source_urls", JSONB()),

        # Full pipeline data blob
        ("full_data", JSONB()),

        # Stable identity for matching
        ("record_id", sa.Text()),
    ]

    for col_name, col_type in columns_to_add:
        op.add_column("hotel_intel", sa.Column(col_name, col_type, nullable=True))

    op.create_index("ix_hotel_intel_record_id", "hotel_intel", ["record_id"], unique=True)


def downgrade():
    op.drop_index("ix_hotel_intel_record_id", table_name="hotel_intel")
    cols = [
        "star_rating", "official_website", "resort_chain", "loyalty_program",
        "room_fit_for_5_type", "room_types_for_5", "max_occupancy_standard_room",
        "max_occupancy_any_room", "connecting_rooms_available", "cribs_available",
        "rollaway_beds", "kids_club_ages", "kids_club_hours", "teen_club_ages",
        "waterpark_notes", "kids_pool", "babysitting_available", "resort_layout",
        "last_renovation_year", "primary_demographics", "restaurant_names",
        "cuisine_types", "num_bars", "buffet_available", "room_service_24h",
        "food_quality_notes", "beach_access", "beach_type", "beach_description",
        "pool_count", "pool_types", "nearest_airport_code", "airport_transfer_included",
        "surrounding_area", "tripadvisor_rating", "tripadvisor_review_count",
        "google_rating", "google_review_count", "best_time_to_visit",
        "field_confidence", "source_urls", "full_data", "record_id",
    ]
    for col in cols:
        op.drop_column("hotel_intel", col)
