import uuid
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class NotificationOutbox(Base):
    __tablename__ = "notifications_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, server_default="pending")
    attempts = Column(Integer, nullable=False, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    last_error = Column(Text, nullable=True)

    signal_id = Column(UUID(as_uuid=True), nullable=False)
    match_id = Column(UUID(as_uuid=True), nullable=False)

    channel = Column(String(20), nullable=False, server_default="log")

    to_email = Column(Text, nullable=False)
    subject = Column(Text, nullable=False)
    body_text = Column(Text, nullable=False)
