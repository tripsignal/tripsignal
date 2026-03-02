"""ScrapeRun database model."""
from datetime import datetime

from sqlalchemy import Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScrapeRun(Base):
    """ScrapeRun model for tracking each scraper execution cycle."""

    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    total_deals: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_matches: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    error_log: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    deals_deactivated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_geo: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ScrapeRun(id={self.id}, started_at={self.started_at}, status={self.status})>"
