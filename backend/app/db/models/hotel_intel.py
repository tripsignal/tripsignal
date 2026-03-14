"""HotelIntel database model."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HotelIntel(Base):
    """Hotel intelligence data from research (Gemini etc.)."""

    __tablename__ = "hotel_intel"

    # --- Original columns ---
    hotel_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hotel_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resort_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    adults_only: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    kids_club: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    teen_club: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    waterpark: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    num_restaurants: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfer_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sargassum_risk: Mapped[str | None] = mapped_column(Text, nullable=True)
    sargassum_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vibe: Mapped[str | None] = mapped_column(Text, nullable=True)
    red_flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    top_complaints: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    top_praise: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    accommodates_5: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True, server_default=text("'gemini'"))
    researched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    # --- Identity ---
    star_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    official_website: Mapped[str | None] = mapped_column(Text, nullable=True)
    resort_chain: Mapped[str | None] = mapped_column(Text, nullable=True)
    loyalty_program: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Family / rooms ---
    room_fit_for_5_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    room_types_for_5: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    max_occupancy_standard_room: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_occupancy_any_room: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connecting_rooms_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cribs_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rollaway_beds: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Kids ---
    kids_club_ages: Mapped[str | None] = mapped_column(Text, nullable=True)
    kids_club_hours: Mapped[str | None] = mapped_column(Text, nullable=True)
    teen_club_ages: Mapped[str | None] = mapped_column(Text, nullable=True)
    waterpark_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    kids_pool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    babysitting_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Property ---
    resort_layout: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_renovation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_demographics: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Food ---
    restaurant_names: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    cuisine_types: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    num_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buffet_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    room_service_24h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    food_quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Beach / Pool ---
    beach_access: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    beach_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    beach_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool_types: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Location ---
    nearest_airport_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    airport_transfer_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    surrounding_area: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Reviews ---
    tripadvisor_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    tripadvisor_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    google_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_time_to_visit: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Quality metadata ---
    field_confidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Full pipeline data blob ---
    full_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Stable identity for matching ---
    record_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True, index=True)

    def __repr__(self) -> str:
        return f"<HotelIntel(hotel_id={self.hotel_id}, hotel_name={self.hotel_name})>"
