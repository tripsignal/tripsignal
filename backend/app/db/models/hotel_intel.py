"""HotelIntel database model."""
from datetime import datetime

from sqlalchemy import Boolean, Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HotelIntel(Base):
    """Hotel intelligence data from research (Gemini etc.)."""

    __tablename__ = "hotel_intel"

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

    def __repr__(self) -> str:
        return f"<HotelIntel(hotel_id={self.hotel_id}, hotel_name={self.hotel_name})>"
