"""HotelLink model — stores external URLs (TripAdvisor, etc.) keyed by SellOff hotel_id."""
from sqlalchemy import Column, DateTime, Numeric, Text, func

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
