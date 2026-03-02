"""Admin-editable email template overrides."""
from __future__ import annotations

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from app.db.base import Base


class EmailTemplateOverride(Base):
    __tablename__ = "email_template_overrides"

    email_type = Column(Text, primary_key=True)       # e.g. "WELCOME_EMAIL"
    subject = Column(Text, nullable=True)              # null = use Python default
    body_html = Column(Text, nullable=True)            # null = use Python default
    updated_at = Column(TIMESTAMP(timezone=True))
    updated_by = Column(Text, nullable=True)           # admin identifier
