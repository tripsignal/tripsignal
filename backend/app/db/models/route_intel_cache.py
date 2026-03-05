"""RouteIntelCache database model — computed intelligence values per route (origin + destination)."""
from datetime import datetime

from sqlalchemy import Date, Float, Integer, TIMESTAMP, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RouteIntelCache(Base):
    """Cached intelligence values per route, refreshed after each scrape cycle.

    A route is defined as an (origin airport, destination region) pair.
    Used for insights that span across signals: departure window heatmap,
    destination price index, booking countdown pressure.
    """

    __tablename__ = "route_intel_cache"

    origin: Mapped[str] = mapped_column(Text, primary_key=True)
    destination_region: Mapped[str] = mapped_column(Text, primary_key=True)

    # Departure Window Heatmap — cheapest vs priciest departure week
    cheapest_depart_week: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    cheapest_week_avg_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priciest_depart_week: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    priciest_week_avg_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Destination Price Index — current week vs previous week
    current_week_avg_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prev_week_avg_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    week_over_week_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Booking Countdown Pressure — price by days-until-departure
    avg_price_4plus_weeks_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_price_2to4_weeks_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_price_under_2_weeks_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_booking_premium_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Metadata
    total_deals_analyzed: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0"),
    )
    cache_refreshed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
