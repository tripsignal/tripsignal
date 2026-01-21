"""DealMatch database model."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, TIMESTAMP, UniqueConstraint, text
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

    matched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    # Relationships
    deal: Mapped["Deal"] = relationship("Deal", back_populates="deal_matches")
    signal: Mapped["Signal"] = relationship("Signal", back_populates="deal_matches")

    __table_args__ = (
        UniqueConstraint("signal_id", "deal_id", name="uq_deal_matches_signal_deal"),
    )

    def __repr__(self) -> str:
        """String representation of DealMatch."""
        return (
            f"<DealMatch(id={self.id}, signal_id={self.signal_id}, "
            f"deal_id={self.deal_id}, matched_at={self.matched_at})>"
        )
