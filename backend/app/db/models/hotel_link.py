"""HotelLink model — stores external URLs (TripAdvisor, etc.) keyed by SellOff hotel_id."""
from sqlalchemy import Column, DateTime, Integer, Numeric, Text, func

from app.db.base import Base


class HotelLink(Base):
    __tablename__ = "hotel_links"

    hotel_id = Column(Text, primary_key=True)
    hotel_name = Column(Text, nullable=False)
    destination = Column(Text, nullable=True)
    star_rating = Column(Numeric(2, 1), nullable=True)
    tripadvisor_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Match pipeline metadata
    tripadvisor_id = Column(Integer, nullable=True)
    match_confidence = Column(Numeric(4, 3), nullable=True)
    match_method = Column(Text, nullable=True)
    review_status = Column(Text, nullable=True)  # matched / ambiguous / needs_manual_review / not_found
    suggested_url = Column(Text, nullable=True)   # best candidate URL for review items
    suggested_name = Column(Text, nullable=True)  # best candidate name for review items
    match_notes = Column(Text, nullable=True)
