"""Plan database model."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Plan(Base):
    """Subscription plan limits and entitlements."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "free"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    max_active_signals: Mapped[int] = mapped_column(Integer, nullable=False)
    max_runs_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    min_check_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    allow_email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    allow_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    allow_push: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    # Relationships
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription",
        back_populates="plan",
    )
