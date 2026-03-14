"""User database model."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    clerk_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    signup_email_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    trial_flagged_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name_prompt_dismissed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    plan_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'free'"))
    plan_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    trial_ends_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_subscription_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_current_period_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Notification preferences
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    sms_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    email_opt_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    unsubscribe_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )

    # Pro activation
    pro_activation_completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    login_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0"),
    )
    last_login_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_test_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True,
    )

    # Soft-delete
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, index=True,
    )
    deleted_by: Mapped[str | None] = mapped_column(Text, nullable=True)  # 'admin' | 'user'
    deleted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_reason_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_canceled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    trial_auto_extended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    trial_expired_email_sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    welcome_email_sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    trial_expiring_email_sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    no_signal_email_sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )

    # Email intelligence — user mode & engagement tracking
    email_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"),
    )
    last_email_opened_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    last_email_clicked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    notification_delivery_frequency: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'all'"),
    )
    timezone: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'America/Toronto'"),
    )

    notification_weekly_summary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )

    # Quiet hours
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    quiet_hours_start: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'21:00'"),
    )
    quiet_hours_end: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'08:00'"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"),
        onupdate=text("now()"),
    )

    @property
    def frequency_windows(self) -> list[str]:
        """Parse comma-separated frequency into list: ['morning', 'evening'] or ['all']."""
        return [w.strip() for w in (self.notification_delivery_frequency or "all").split(",") if w.strip()]

    @property
    def is_instant_delivery(self) -> bool:
        return "all" in self.frequency_windows
