"""Signal database model."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Signal(Base):
    """Signal model for all-inclusive package monitoring."""

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")

    departure_airports: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    destination_regions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    config: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    # Relationships
    deal_matches: Mapped[list["DealMatch"]] = relationship(
        "DealMatch",
        back_populates="signal",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    runs: Mapped[list["SignalRun"]] = relationship(
        "SignalRun",
        back_populates="signal",
        cascade="all, delete-orphan",
    )
