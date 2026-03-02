"""
Integration tests for Stripe billing webhook handlers.

Tests verify:
- DB updates happen before email triggers.
- No direct email sends — all go through EmailOrchestratorService.
- Repeat webhook does not double-send (Stripe event dedup + idempotency keys).
- Signals paused on payment failure.
- Signals reactivated on payment success.
- SUBSCRIPTION_CANCELED suppressed after account deletion.
- PRO_ACTIVATED uses subscription_id in idempotency key.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_billing_webhooks.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.email_log import EmailLog
from app.db.models.signal import Signal
from app.db.models.stripe_event import StripeEvent
from app.db.models.user import User
from app.services.email_orchestrator import EmailType
from app.api.routes.billing import (
    _handle_checkout_completed,
    _handle_subscription_change,
    _handle_payment_failed,
    _pause_signals,
    _reactivate_signals,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    import os
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db_name = os.getenv("POSTGRES_DB", "tripsignal")
    url = os.getenv(
        "TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}",
    )
    return create_engine(url)


@pytest.fixture
def db(engine):
    """Transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


NOW = datetime(2040, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_user(
    db: Session,
    *,
    plan_type: str = "free",
    plan_status: str = "active",
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_subscription_status: str | None = None,
    deleted_at: datetime | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        clerk_id=f"test_{uuid.uuid4().hex[:8]}",
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        plan_type=plan_type,
        plan_status=plan_status,
        email_enabled=True,
        email_opt_out=False,
        stripe_customer_id=stripe_customer_id or f"cus_{uuid.uuid4().hex[:12]}",
        stripe_subscription_id=stripe_subscription_id,
        stripe_subscription_status=stripe_subscription_status,
        deleted_at=deleted_at,
    )
    db.add(user)
    db.flush()
    return user


def _make_signal(db: Session, user: User, *, status: str = "active") -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        name="Test Signal",
        status=status,
        user_id=user.id,
        config={},
        departure_airports=["YQR"],
        destination_regions=["Caribbean"],
    )
    db.add(sig)
    db.flush()
    return sig


def _checkout_event(clerk_id: str, subscription_id: str = "sub_test_123") -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex[:12]}",
                "metadata": {"clerk_id": clerk_id},
                "subscription": subscription_id,
            }
        },
    }


def _subscription_event(
    event_type: str,
    subscription_id: str,
    status: str = "active",
    current_period_end: int | None = None,
) -> dict:
    return {
        "type": event_type,
        "data": {
            "object": {
                "id": subscription_id,
                "status": status,
                "current_period_end": current_period_end or int(NOW.timestamp()) + 86400 * 30,
            }
        },
    }


def _payment_failed_event(customer_id: str, invoice_id: str = "inv_test_001") -> dict:
    return {
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": invoice_id,
                "customer": customer_id,
            }
        },
    }


def _has_email_log(db: Session, user_id: uuid.UUID, email_type: EmailType) -> bool:
    return db.execute(
        select(EmailLog).where(
            EmailLog.user_id == user_id,
            EmailLog.email_type == email_type.value,
        )
    ).scalar_one_or_none() is not None


def _get_email_log(db: Session, user_id: uuid.UUID, email_type: EmailType) -> EmailLog | None:
    return db.execute(
        select(EmailLog).where(
            EmailLog.user_id == user_id,
            EmailLog.email_type == email_type.value,
        )
    ).scalar_one_or_none()


# ── PRO_ACTIVATED Tests ──────────────────────────────────────────────────────

class TestProActivated:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_checkout_triggers_pro_activated(self, mock_settings, mock_send, db):
        """Checkout completion triggers PRO_ACTIVATED email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free")
        event = _checkout_event(user.clerk_id, "sub_new_123")
        _handle_checkout_completed(db, event)

        # DB updated
        db.refresh(user)
        assert user.plan_type == "pro"
        assert user.stripe_subscription_id == "sub_new_123"

        # Email sent
        assert _has_email_log(db, user.id, EmailType.PRO_ACTIVATED)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_idempotency_key_uses_subscription_id(self, mock_settings, mock_send, db):
        """PRO_ACTIVATED key includes subscription_id for dedup."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free")
        event = _checkout_event(user.clerk_id, "sub_key_test")
        _handle_checkout_completed(db, event)

        log = _get_email_log(db, user.id, EmailType.PRO_ACTIVATED)
        assert log is not None
        assert "sub_key_test" in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_already_pro_no_email(self, mock_settings, mock_send, db):
        """User already on pro plan doesn't get PRO_ACTIVATED email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        event = _checkout_event(user.clerk_id)
        _handle_checkout_completed(db, event)

        assert not _has_email_log(db, user.id, EmailType.PRO_ACTIVATED)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_repeat_checkout_no_double_send(self, mock_settings, mock_send, db):
        """Same subscription_id twice doesn't send duplicate email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free")
        event = _checkout_event(user.clerk_id, "sub_repeat_123")

        _handle_checkout_completed(db, event)
        assert _has_email_log(db, user.id, EmailType.PRO_ACTIVATED)

        # Second call with same subscription — user already pro
        _handle_checkout_completed(db, event)

        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.PRO_ACTIVATED.value,
            )
        ).scalars().all()
        # Only one non-duplicate log entry
        sent_logs = [l for l in logs if l.status in ("sent", "dry_run")]
        assert len(sent_logs) == 1


# ── PAYMENT_FAILED Tests ─────────────────────────────────────────────────────

class TestPaymentFailed:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_payment_failed_pauses_signals(self, mock_settings, mock_send, db):
        """Payment failure immediately pauses all active signals."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        s1 = _make_signal(db, user, status="active")
        s2 = _make_signal(db, user, status="active")
        s3 = _make_signal(db, user, status="paused")  # user-paused, should stay

        event = _payment_failed_event(user.stripe_customer_id)
        _handle_payment_failed(db, event)

        db.refresh(s1)
        db.refresh(s2)
        db.refresh(s3)
        assert s1.status == "payment_paused"
        assert s2.status == "payment_paused"
        assert s3.status == "paused"  # unchanged — user-initiated

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_payment_failed_sends_email(self, mock_settings, mock_send, db):
        """Payment failure triggers PAYMENT_FAILED email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        event = _payment_failed_event(user.stripe_customer_id, "inv_fail_001")
        _handle_payment_failed(db, event)

        log = _get_email_log(db, user.id, EmailType.PAYMENT_FAILED)
        assert log is not None
        assert "inv_fail_001" in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_repeat_payment_failed_no_double_send(self, mock_settings, mock_send, db):
        """Same invoice_id doesn't send duplicate PAYMENT_FAILED email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")

        event = _payment_failed_event(user.stripe_customer_id, "inv_dedup_001")
        _handle_payment_failed(db, event)
        _handle_payment_failed(db, event)

        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.PAYMENT_FAILED.value,
            )
        ).scalars().all()
        sent_logs = [l for l in logs if l.status in ("sent", "dry_run")]
        assert len(sent_logs) == 1


# ── Signal Reactivation Tests ────────────────────────────────────────────────

class TestSignalReactivation:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_payment_success_reactivates_signals(self, mock_settings, mock_send, db):
        """Subscription returning to active reactivates payment-paused signals."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_react_123"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)
        s1 = _make_signal(db, user, status="payment_paused")
        s2 = _make_signal(db, user, status="payment_paused")
        s3 = _make_signal(db, user, status="paused")  # user-paused, should stay

        event = _subscription_event("customer.subscription.updated", sub_id, "active")
        _handle_subscription_change(db, event)

        db.refresh(s1)
        db.refresh(s2)
        db.refresh(s3)
        assert s1.status == "active"
        assert s2.status == "active"
        assert s3.status == "paused"  # unchanged

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_checkout_reactivates_signals(self, mock_settings, mock_send, db):
        """Checkout also reactivates payment-paused signals (edge case)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free")
        s1 = _make_signal(db, user, status="payment_paused")

        event = _checkout_event(user.clerk_id, "sub_react_co_123")
        _handle_checkout_completed(db, event)

        db.refresh(s1)
        assert s1.status == "active"

    def test_pause_reactivate_roundtrip(self, db):
        """Pause then reactivate is a clean roundtrip."""
        user = _make_user(db, plan_type="pro")
        s1 = _make_signal(db, user, status="active")
        s2 = _make_signal(db, user, status="active")

        paused = _pause_signals(db, user.id)
        assert paused == 2
        db.flush()

        db.refresh(s1)
        db.refresh(s2)
        assert s1.status == "payment_paused"
        assert s2.status == "payment_paused"

        reactivated = _reactivate_signals(db, user.id)
        assert reactivated == 2
        db.flush()

        db.refresh(s1)
        db.refresh(s2)
        assert s1.status == "active"
        assert s2.status == "active"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_full_payment_failure_recovery_flow(self, mock_settings, mock_send, db):
        """Full flow: payment fails → signals paused → payment succeeds → signals reactivated."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_full_flow"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)
        s1 = _make_signal(db, user, status="active")
        s2 = _make_signal(db, user, status="active")

        # Step 1: Payment fails
        fail_event = _payment_failed_event(user.stripe_customer_id, "inv_flow_001")
        _handle_payment_failed(db, fail_event)
        db.flush()

        db.refresh(s1)
        db.refresh(s2)
        assert s1.status == "payment_paused"
        assert s2.status == "payment_paused"
        assert _has_email_log(db, user.id, EmailType.PAYMENT_FAILED)

        # Step 2: Subscription goes past_due
        past_due_event = _subscription_event("customer.subscription.updated", sub_id, "past_due")
        _handle_subscription_change(db, past_due_event)
        db.refresh(user)
        assert user.plan_type == "pro"  # still pro during past_due
        db.refresh(s1)
        assert s1.status == "payment_paused"  # still paused

        # Step 3: User updates payment method → subscription back to active
        active_event = _subscription_event("customer.subscription.updated", sub_id, "active")
        _handle_subscription_change(db, active_event)
        db.flush()

        db.refresh(s1)
        db.refresh(s2)
        assert s1.status == "active"
        assert s2.status == "active"
        db.refresh(user)
        assert user.plan_type == "pro"


# ── SUBSCRIPTION_CANCELED Tests ──────────────────────────────────────────────

class TestSubscriptionCanceled:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_cancellation_triggers_email(self, mock_settings, mock_send, db):
        """Subscription deletion triggers SUBSCRIPTION_CANCELED email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_cancel_123"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.deleted", sub_id, "canceled")
        _handle_subscription_change(db, event)

        log = _get_email_log(db, user.id, EmailType.SUBSCRIPTION_CANCELED)
        assert log is not None
        assert "sub_cancel_123" in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_cancellation_sets_free_plan(self, mock_settings, mock_send, db):
        """Subscription deletion sets user to free plan."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_cancel_plan"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.deleted", sub_id, "canceled")
        _handle_subscription_change(db, event)

        db.refresh(user)
        assert user.plan_type == "free"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_suppressed_after_account_deletion(self, mock_settings, mock_send, db):
        """SUBSCRIPTION_CANCELED suppressed if account was recently deleted."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_deleted_user"
        user = _make_user(
            db,
            plan_type="pro",
            stripe_subscription_id=sub_id,
            deleted_at=NOW - timedelta(hours=1),  # deleted 1h ago
        )

        event = _subscription_event("customer.subscription.deleted", sub_id, "canceled")
        _handle_subscription_change(db, event)

        # Orchestrator suppression rule 2 catches this (deleted user)
        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.SUBSCRIPTION_CANCELED.value,
                EmailLog.status == "sent",
            )
        ).scalar_one_or_none()
        assert log is None  # no "sent" log — was suppressed

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_repeat_cancellation_no_double_send(self, mock_settings, mock_send, db):
        """Same subscription cancellation doesn't double-send."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_repeat_cancel"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.deleted", sub_id, "canceled")
        _handle_subscription_change(db, event)
        _handle_subscription_change(db, event)

        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.SUBSCRIPTION_CANCELED.value,
            )
        ).scalars().all()
        sent_logs = [l for l in logs if l.status in ("sent", "dry_run")]
        assert len(sent_logs) == 1


# ── Subscription Status Handling Tests ────────────────────────────────────────

class TestSubscriptionStatus:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_past_due_keeps_pro(self, mock_settings, mock_send, db):
        """past_due status keeps user as pro (still subscribed)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_past_due"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.updated", sub_id, "past_due")
        _handle_subscription_change(db, event)

        db.refresh(user)
        assert user.plan_type == "pro"
        assert user.stripe_subscription_status == "past_due"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_active_sets_pro(self, mock_settings, mock_send, db):
        """active status sets user to pro."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_active"
        user = _make_user(db, plan_type="free", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.updated", sub_id, "active")
        _handle_subscription_change(db, event)

        db.refresh(user)
        assert user.plan_type == "pro"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_unpaid_keeps_pro(self, mock_settings, mock_send, db):
        """unpaid status keeps user as pro (still subscribed, just failing)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        sub_id = "sub_unpaid"
        user = _make_user(db, plan_type="pro", stripe_subscription_id=sub_id)

        event = _subscription_event("customer.subscription.updated", sub_id, "unpaid")
        _handle_subscription_change(db, event)

        db.refresh(user)
        assert user.plan_type == "pro"
        assert user.stripe_subscription_status == "unpaid"


# ── No Direct Email Send Proof ───────────────────────────────────────────────

class TestNoDirectEmailSend:

    def test_billing_module_has_no_send_email_import(self):
        """billing.py does not import send_email directly."""
        import inspect
        import app.api.routes.billing as billing_module
        source = inspect.getsource(billing_module)
        # Should NOT find direct send_email imports or Resend API calls
        assert "from app.services.email import send_email" not in source
        assert "resend.Emails" not in source
        assert "api.resend.com" not in source
        # Should find only orchestrator trigger
        assert "email_trigger" in source or "trigger as email_trigger" in source

    def test_billing_module_uses_only_orchestrator(self):
        """All email sends go through email_trigger (orchestrator)."""
        import inspect
        import app.api.routes.billing as billing_module
        source = inspect.getsource(billing_module)
        # Count email_trigger calls vs any direct send
        assert source.count("email_trigger(") >= 3  # PRO_ACTIVATED, SUBSCRIPTION_CANCELED, PAYMENT_FAILED
        assert "send_email(" not in source
