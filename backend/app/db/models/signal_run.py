"""SignalRun database model."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Integer, TIMESTAMP, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SignalRunType(str, enum.Enum):
    """Type of run (scheduled/manual)."""

    morning = "morning"
    afternoon = "afternoon"
    manual = "manual"
    test = "test"


class SignalRunStatus(str, enum.Enum):
    """Run status."""

    running = "running"
    success = "success"
    failed = "failed"


class SignalRun(Base):
    """SignalRun model for tracking each matching execution."""

    __tablename__ = "signal_runs"

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

    run_type: Mapped[SignalRunType] = mapped_column(
        Enum(SignalRunType, name="signal_run_type"),
        nullable=False,
    )

    status: Mapped[SignalRunStatus] = mapped_column(
        Enum(SignalRunStatus, name="signal_run_status"),
        nullable=False,
        server_default=text("'running'"),
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    matches_created_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    signal: Mapped["Signal"] = relationship("Signal", back_populates="runs")
    deal_matches: Mapped[list["DealMatch"]] = relationship("DealMatch", back_populates="run")

    def __repr__(self) -> str:
        """String representation of SignalRun."""
        return (
            f"<SignalRun(id={self.id}, signal_id={self.signal_id}, "
            f"run_type={self.run_type}, status={self.status}, "
            f"started_at={self.started_at}, completed_at={self.completed_at})>"
        )
