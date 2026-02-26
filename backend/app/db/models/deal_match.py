"""DealMatch database model."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DealMatch(Base):
    """DealMatch model for linking deals to signals."""

    __tablename__ = "deal_matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # NEW: link to the run that created this match
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    matched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    is_favourite: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    # Relationships
    deal: Mapped["Deal"] = relationship("Deal", back_populates="deal_matches")
    signal: Mapped["Signal"] = relationship("Signal", back_populates="deal_matches")
    run: Mapped[Optional["SignalRun"]] = relationship(
        "SignalRun",
        back_populates="deal_matches",
    )

    __table_args__ = (
        UniqueConstraint("signal_id", "deal_id", name="uq_deal_matches_signal_deal"),
    )

    def __repr__(self) -> str:
        return (
            f"<DealMatch(id={self.id}, signal_id={self.signal_id}, "
            f"deal_id={self.deal_id}, run_id={self.run_id}, matched_at={self.matched_at})>"
        )
