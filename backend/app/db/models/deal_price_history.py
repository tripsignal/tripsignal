"""DealPriceHistory database model."""
import uuid
from datetime import datetime

from sqlalchemy import Integer, TIMESTAMP, text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DealPriceHistory(Base):
    """Price history for a deal, recorded on each scrape."""

    __tablename__ = "deal_price_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True
    )

    deal: Mapped["Deal"] = relationship("Deal", back_populates="price_history")

    def __repr__(self) -> str:
        return f"<DealPriceHistory(deal_id={self.deal_id}, price_cents={self.price_cents}, recorded_at={self.recorded_at})>"
