"""Deal database model."""
import uuid
from datetime import date
from datetime import datetime

from sqlalchemy import Date, Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Deal(Base):
    """Deal model for storing travel deals."""

    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    destination: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    depart_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    return_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CAD'"),
    )
    deeplink_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    airline: Mapped[str | None] = mapped_column(Text, nullable=True)
    cabin: Mapped[str | None] = mapped_column(Text, nullable=True)
    stops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    dedupe_key: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"), index=True
    )
    hotel_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    hotel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    destination_str: Mapped[str | None] = mapped_column(Text, nullable=True)
    star_rating: Mapped[float | None] = mapped_column(nullable=True)

    price_history: Mapped[list["DealPriceHistory"]] = relationship(
        "DealPriceHistory",
        back_populates="deal",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    deal_matches: Mapped[list["DealMatch"]] = relationship(
        "DealMatch",
        back_populates="deal",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        """String representation of Deal."""
        return (
            f"<Deal(id={self.id}, provider={self.provider}, "
            f"origin={self.origin}, destination={self.destination}, "
            f"depart_date={self.depart_date}, price_cents={self.price_cents})>"
        )
