"""Stripe webhook event storage for deduplication and audit."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StripeEvent(Base):
    __tablename__ = "stripe_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    stripe_event_id: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
