"""DealMatch database model."""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy import TIMESTAMP
from sqlalchemy import text
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.db.base import Base


class DealMatch(Base):
    """DealMatch model for linking deals to signal runs."""

    __tablename__ = "deal_matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    signal_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signal_runs.id"),
        nullable=False,
        index=True,
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id"),
        nullable=False,
        index=True,
    )
    matched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True
    )

    __table_args__ = (
        UniqueConstraint("signal_run_id", "deal_id", name="uq_deal_matches_signal_run_deal"),
    )

    def __repr__(self) -> str:
        """String representation of DealMatch."""
        return (
            f"<DealMatch(id={self.id}, signal_run_id={self.signal_run_id}, "
            f"deal_id={self.deal_id}, matched_at={self.matched_at})>"
        )
