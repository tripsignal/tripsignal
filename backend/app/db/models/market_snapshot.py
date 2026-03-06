"""MarketSnapshot database model — daily compressed market summaries."""
from datetime import date, datetime

from sqlalchemy import Date, Float, Integer, TIMESTAMP, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketSnapshot(Base):
    """Daily market summary per market bucket.

    Stored once daily after scrapers complete.
    Primary purpose: build long-term historical intelligence and data moat.
    Not yet exposed to UI — future analytics foundation.
    """

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    departure_airport: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    destination_region: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    duration_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    star_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    unique_resort_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    min_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    median_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p75_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_stddev: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
