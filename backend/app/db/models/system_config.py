"""SystemConfig database model for key-value system settings."""
from datetime import datetime

from sqlalchemy import TIMESTAMP, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemConfig(Base):
    """Key-value store for system configuration (e.g. next_scan_at)."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<SystemConfig(key={self.key!r}, value={self.value!r})>"
