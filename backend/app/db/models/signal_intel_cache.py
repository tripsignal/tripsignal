"""SignalIntelCache database model — computed intelligence values per signal."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalIntelCache(Base):
    """Cached intelligence values for a signal, refreshed after each scrape cycle."""

    __tablename__ = "signal_intel_cache"

    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signals.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Module 1: Price History — All-Time Low & Percentile Rank
    min_price_ever_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_deal_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Module 2: Trend Direction — Price Momentum
    trend_direction: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'stable'"),
    )
    trend_consecutive_weeks: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0"),
    )

    # Module 2b: Price Momentum Velocity — rate of change
    trend_velocity: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )  # 'accelerating', 'decelerating', 'steady'
    trend_last_week_delta_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trend_prev_week_delta_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Module 2c: Trend Inflection — prices reversing direction
    trend_inflection: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false"),
    )
    inflection_pct_change: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Module 3: Night Length Sweet Spot — Per-Night Value
    best_value_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_value_pct_saving: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Module 4: Price-per-Star Anomaly Detection
    star_price_anomaly_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    hero_star_rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Module 5: Price Floor Proximity
    floor_proximity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Module 7: Price-to-Quality Value Score (0-100)
    value_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Metadata
    total_matches: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0"),
    )
    cache_refreshed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
