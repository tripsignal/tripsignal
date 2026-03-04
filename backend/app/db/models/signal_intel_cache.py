"""SignalIntelCache database model — computed intelligence values per signal."""
import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, TIMESTAMP, Text, text
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

    # Module 3: Night Length Sweet Spot — Per-Night Value
    best_value_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_value_pct_saving: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Metadata
    total_matches: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0"),
    )
    cache_refreshed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
