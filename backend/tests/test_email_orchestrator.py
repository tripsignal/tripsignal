"""
Tests for the Email Orchestrator — suppression, idempotency, and trigger flow.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_email_orchestrator.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.email_log import EmailLog
from app.db.models.user import User
from app.services.email_orchestrator import (
    EmailCategory,
    EmailType,
    _build_idempotency_key,
    _check_suppression,
    trigger,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    """Use the real database for integration-style tests."""
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
    eng = create_engine(url)
    yield eng


@pytest.fixture
def db(engine):
    """Provide a transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_user(db: Session) -> User:
    """Create a test user for each test."""
    user = User(
        id=uuid.uuid4(),
        clerk_id=f"test_clerk_{uuid.uuid4().hex[:8]}",
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        plan_type="free",
        plan_status="active",
        email_enabled=True,
        email_opt_out=False,
    )
    db.add(user)
    db.flush()
    return user


# ── Suppression Tests ─────────────────────────────────────────────────────────

class TestSuppression:

    def test_deleted_user_suppresses_non_deletion_emails(self, db, test_user):
        test_user.deleted_at = datetime.now(timezone.utc)
        db.flush()
        result = _check_suppression(db, test_user, EmailType.WELCOME, EmailCategory.TRANSACTIONAL)
        assert result == "user_deleted"

    def test_deleted_user_allows_account_deleted_free(self, db, test_user):
        test_user.deleted_at = datetime.now(timezone.utc)
        db.flush()
        result = _check_suppression(db, test_user, EmailType.ACCOUNT_DELETED_FREE, EmailCategory.TRANSACTIONAL)
        assert result is None

    def test_deleted_user_allows_account_deleted_pro(self, db, test_user):
        test_user.deleted_at = datetime.now(timezone.utc)
        db.flush()
        result = _check_suppression(db, test_user, EmailType.ACCOUNT_DELETED_PRO, EmailCategory.TRANSACTIONAL)
        assert result is None

    def test_opt_out_suppresses_engagement(self, db, test_user):
        test_user.email_opt_out = True
        db.flush()
        result = _check_suppression(db, test_user, EmailType.NO_SIGNAL_REMINDER, EmailCategory.ENGAGEMENT)
        assert result == "email_opt_out"

    def test_opt_out_suppresses_upsell(self, db, test_user):
        test_user.email_opt_out = True
        db.flush()
        result = _check_suppression(db, test_user, EmailType.TRIAL_EXPIRED_UPSELL, EmailCategory.UPSELL)
        assert result == "email_opt_out"

    def test_opt_out_does_not_suppress_billing(self, db, test_user):
        test_user.email_opt_out = True
        db.flush()
        result = _check_suppression(db, test_user, EmailType.PAYMENT_FAILED, EmailCategory.BILLING)
        assert result is None

    def test_opt_out_does_not_suppress_transactional(self, db, test_user):
        test_user.email_opt_out = True
        db.flush()
        result = _check_suppression(db, test_user, EmailType.WELCOME, EmailCategory.TRANSACTIONAL)
        assert result is None

    def test_opt_out_does_not_suppress_alert(self, db, test_user):
        test_user.email_opt_out = True
        db.flush()
        result = _check_suppression(db, test_user, EmailType.MATCH_ALERT, EmailCategory.ALERT)
        assert result is None

    def test_email_disabled_suppresses_alerts(self, db, test_user):
        test_user.email_enabled = False
        db.flush()
        result = _check_suppression(db, test_user, EmailType.MATCH_ALERT, EmailCategory.ALERT)
        assert result == "email_disabled"

    def test_email_disabled_does_not_suppress_billing(self, db, test_user):
        test_user.email_enabled = False
        db.flush()
        result = _check_suppression(db, test_user, EmailType.PAYMENT_FAILED, EmailCategory.BILLING)
        assert result is None

    def test_rate_limit_engagement(self, db, test_user):
        """After 2 engagement emails in 24h, further ones are suppressed."""
        now = datetime.now(timezone.utc)
        for i in range(2):
            db.add(EmailLog(
                user_id=test_user.id,
                email_type="NO_SIGNAL_REMINDER",
                category="engagement",
                idempotency_key=f"test_rate_{test_user.id}_{i}",
                to_email=test_user.email,
                status="sent",
                sent_at=now - timedelta(hours=1),
            ))
        db.flush()
        result = _check_suppression(db, test_user, EmailType.INACTIVE_REENGAGEMENT, EmailCategory.ENGAGEMENT)
        assert result == "rate_limit_24h"

    def test_rate_limit_counts_dry_run_status(self, db, test_user):
        """dry_run statuses count toward the rate limit."""
        now = datetime.now(timezone.utc)
        for i in range(2):
            db.add(EmailLog(
                user_id=test_user.id,
                email_type="NO_SIGNAL_REMINDER",
                category="engagement",
                idempotency_key=f"test_dryrate_{test_user.id}_{i}",
                to_email=test_user.email,
                status="dry_run",
                sent_at=now - timedelta(hours=1),
            ))
        db.flush()
        result = _check_suppression(db, test_user, EmailType.INACTIVE_REENGAGEMENT, EmailCategory.ENGAGEMENT)
        assert result == "rate_limit_24h"

    def test_rate_limit_ignores_old_emails(self, db, test_user):
        """Emails older than 24h don't count toward rate limit."""
        now = datetime.now(timezone.utc)
        for i in range(5):
            db.add(EmailLog(
                user_id=test_user.id,
                email_type="NO_SIGNAL_REMINDER",
                category="engagement",
                idempotency_key=f"test_old_{test_user.id}_{i}",
                to_email=test_user.email,
                status="sent",
                sent_at=now - timedelta(hours=25),
            ))
        db.flush()
        result = _check_suppression(db, test_user, EmailType.INACTIVE_REENGAGEMENT, EmailCategory.ENGAGEMENT)
        assert result is None

    def test_upsell_cooldown_after_trial_warning(self, db, test_user):
        """UPSELL suppressed within 48h of TRIAL_EXPIRING_SOON."""
        now = datetime.now(timezone.utc)
        db.add(EmailLog(
            user_id=test_user.id,
            email_type=EmailType.TRIAL_EXPIRING_SOON.value,
            category="upsell",
            idempotency_key=f"trial_expiring:{test_user.id}:2026-03-01",
            to_email=test_user.email,
            status="sent",
            sent_at=now - timedelta(hours=12),
        ))
        db.flush()
        result = _check_suppression(db, test_user, EmailType.TRIAL_EXPIRED_UPSELL, EmailCategory.UPSELL)
        assert result == "upsell_after_trial_warning"

    def test_upsell_cooldown_expired(self, db, test_user):
        """UPSELL allowed after 48h since TRIAL_EXPIRING_SOON."""
        now = datetime.now(timezone.utc)
        db.add(EmailLog(
            user_id=test_user.id,
            email_type=EmailType.TRIAL_EXPIRING_SOON.value,
            category="upsell",
            idempotency_key=f"trial_expiring:{test_user.id}:2026-02-25",
            to_email=test_user.email,
            status="sent",
            sent_at=now - timedelta(hours=50),
        ))
        db.flush()
        result = _check_suppression(db, test_user, EmailType.TRIAL_EXPIRED_UPSELL, EmailCategory.UPSELL)
        assert result is None

    def test_canceled_after_deletion_suppressed(self, db, test_user):
        """SUBSCRIPTION_CANCELED suppressed if deleted within 24h."""
        test_user.deleted_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.flush()
        # deleted_at is set → rule 2 (user_deleted) fires first for non-deletion emails
        result = _check_suppression(db, test_user, EmailType.SUBSCRIPTION_CANCELED, EmailCategory.BILLING)
        assert result == "user_deleted"

    def test_healthy_user_no_suppression(self, db, test_user):
        """A healthy user with no flags set should not be suppressed for any category."""
        for email_type, category in [
            (EmailType.WELCOME, EmailCategory.TRANSACTIONAL),
            (EmailType.PAYMENT_FAILED, EmailCategory.BILLING),
            (EmailType.MATCH_ALERT, EmailCategory.ALERT),
            (EmailType.TRIAL_EXPIRING_SOON, EmailCategory.UPSELL),
            (EmailType.NO_SIGNAL_REMINDER, EmailCategory.ENGAGEMENT),
        ]:
            result = _check_suppression(db, test_user, email_type, category)
            assert result is None, f"Unexpected suppression for {email_type}: {result}"


class TestSuspendNoncritical:

    @patch("app.services.email_orchestrator.settings")
    def test_suspend_suppresses_engagement(self, mock_settings, db, test_user):
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        result = _check_suppression(db, test_user, EmailType.NO_SIGNAL_REMINDER, EmailCategory.ENGAGEMENT)
        assert result == "global_noncritical_suspended"

    @patch("app.services.email_orchestrator.settings")
    def test_suspend_suppresses_upsell(self, mock_settings, db, test_user):
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        result = _check_suppression(db, test_user, EmailType.TRIAL_EXPIRED_UPSELL, EmailCategory.UPSELL)
        assert result == "global_noncritical_suspended"

    @patch("app.services.email_orchestrator.settings")
    def test_suspend_does_not_affect_transactional(self, mock_settings, db, test_user):
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        result = _check_suppression(db, test_user, EmailType.WELCOME, EmailCategory.TRANSACTIONAL)
        assert result is None

    @patch("app.services.email_orchestrator.settings")
    def test_suspend_does_not_affect_billing(self, mock_settings, db, test_user):
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        result = _check_suppression(db, test_user, EmailType.PAYMENT_FAILED, EmailCategory.BILLING)
        assert result is None

    @patch("app.services.email_orchestrator.settings")
    def test_suspend_does_not_affect_alert(self, mock_settings, db, test_user):
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        result = _check_suppression(db, test_user, EmailType.MATCH_ALERT, EmailCategory.ALERT)
        assert result is None


# ── Idempotency Key Tests ────────────────────────────────────────────────────

class TestIdempotencyKeys:
    """Test deterministic key generation for all 15 email types."""

    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_welcome(self):
        key = _build_idempotency_key(EmailType.WELCOME, self.uid, {})
        assert key == f"welcome:{self.uid}"

    def test_first_signal(self):
        key = _build_idempotency_key(EmailType.FIRST_SIGNAL, self.uid, {})
        assert key == f"first_signal:{self.uid}"

    def test_trial_expiring_soon(self):
        key = _build_idempotency_key(
            EmailType.TRIAL_EXPIRING_SOON, self.uid,
            {"trial_end_date": "2026-03-15"},
        )
        assert key == f"trial_expiring:{self.uid}:2026-03-15"

    def test_trial_expiring_missing_date_uses_unknown(self):
        key = _build_idempotency_key(EmailType.TRIAL_EXPIRING_SOON, self.uid, {})
        assert key == f"trial_expiring:{self.uid}:unknown"

    def test_trial_expired_upsell(self):
        key = _build_idempotency_key(
            EmailType.TRIAL_EXPIRED_UPSELL, self.uid,
            {"trial_end_date": "2026-03-15"},
        )
        assert key == f"trial_expired:{self.uid}:2026-03-15"

    def test_pro_activated(self):
        key = _build_idempotency_key(
            EmailType.PRO_ACTIVATED, self.uid,
            {"subscription_id": "sub_test_123"},
        )
        assert key == "pro_activated:sub_test_123"

    def test_pro_activated_fallback_to_uid(self):
        key = _build_idempotency_key(EmailType.PRO_ACTIVATED, self.uid, {})
        assert key == f"pro_activated:{self.uid}"

    def test_payment_failed(self):
        key = _build_idempotency_key(
            EmailType.PAYMENT_FAILED, self.uid,
            {"invoice_id": "inv_abc123"},
        )
        assert key == "payment_failed:inv_abc123"

    def test_payment_failed_reminder(self):
        key = _build_idempotency_key(
            EmailType.PAYMENT_FAILED_REMINDER, self.uid,
            {"invoice_id": "inv_abc123", "reminder_num": "2"},
        )
        assert key == "payment_failed_reminder:inv_abc123:2"

    def test_payment_failed_reminder_uses_index_fallback(self):
        key = _build_idempotency_key(
            EmailType.PAYMENT_FAILED_REMINDER, self.uid,
            {"invoice_id": "inv_abc123", "index": "1"},
        )
        assert key == "payment_failed_reminder:inv_abc123:1"

    def test_subscription_canceled(self):
        key = _build_idempotency_key(
            EmailType.SUBSCRIPTION_CANCELED, self.uid,
            {"subscription_id": "sub_test_456"},
        )
        assert key == "subscription_canceled:sub_test_456"

    def test_subscription_canceled_fallback_to_uid(self):
        key = _build_idempotency_key(EmailType.SUBSCRIPTION_CANCELED, self.uid, {})
        assert key == f"subscription_canceled:{self.uid}"

    def test_account_deleted_free(self):
        key = _build_idempotency_key(EmailType.ACCOUNT_DELETED_FREE, self.uid, {})
        assert key == f"account_deleted_free:{self.uid}"

    def test_account_deleted_pro(self):
        key = _build_idempotency_key(EmailType.ACCOUNT_DELETED_PRO, self.uid, {})
        assert key == f"account_deleted_pro:{self.uid}"

    def test_match_alert(self):
        key = _build_idempotency_key(
            EmailType.MATCH_ALERT, self.uid,
            {"signal_id": "sig_1", "run_id": "run_42"},
        )
        assert key == "match_alert:sig_1:run_42"

    def test_major_drop_alert(self):
        key = _build_idempotency_key(
            EmailType.MAJOR_DROP_ALERT, self.uid,
            {"signal_id": "sig_1", "deal_id": "deal_99"},
        )
        assert key == "major_drop:sig_1:deal_99"

    def test_no_signal_reminder(self):
        key = _build_idempotency_key(EmailType.NO_SIGNAL_REMINDER, self.uid, {})
        assert key == f"no_signal:{self.uid}"

    def test_no_match_update(self):
        key = _build_idempotency_key(
            EmailType.NO_MATCH_UPDATE, self.uid,
            {"signal_id": "sig_1", "window_start": "2026-03-01"},
        )
        assert key == "no_match:sig_1:2026-03-01"

    def test_inactive_reengagement(self):
        key = _build_idempotency_key(
            EmailType.INACTIVE_REENGAGEMENT, self.uid,
            {"window_start": "2026-W09"},
        )
        assert key == f"inactive:{self.uid}:2026-W09"

    def test_inactive_reengagement_uses_period_fallback(self):
        key = _build_idempotency_key(
            EmailType.INACTIVE_REENGAGEMENT, self.uid,
            {"period": "2026-03-01"},
        )
        assert key == f"inactive:{self.uid}:2026-03-01"

    def test_keys_are_deterministic(self):
        """Same inputs always produce the same key."""
        ctx = {"invoice_id": "inv_xyz", "reminder_num": "1"}
        key1 = _build_idempotency_key(EmailType.PAYMENT_FAILED_REMINDER, self.uid, ctx)
        key2 = _build_idempotency_key(EmailType.PAYMENT_FAILED_REMINDER, self.uid, ctx)
        assert key1 == key2

    def test_different_inputs_produce_different_keys(self):
        """Different context values must produce different keys."""
        key1 = _build_idempotency_key(
            EmailType.PAYMENT_FAILED, self.uid, {"invoice_id": "inv_1"},
        )
        key2 = _build_idempotency_key(
            EmailType.PAYMENT_FAILED, self.uid, {"invoice_id": "inv_2"},
        )
        assert key1 != key2


# ── Idempotency Deduplication Tests ──────────────────────────────────────────

class TestIdempotencyDedup:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    @patch("app.services.email_orchestrator.settings")
    def test_duplicate_trigger_returns_duplicate(self, mock_settings, mock_send, db, test_user):
        """Second call with same idempotency key returns duplicate, no email sent."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result1 = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
            idempotency_key=f"test_dup_{test_user.id}",
        )
        assert result1["status"] == "sent"

        result2 = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
            idempotency_key=f"test_dup_{test_user.id}",
        )
        assert result2["status"] == "duplicate"
        assert mock_send.call_count == 1

    @patch("app.services.email_orchestrator.send_email", return_value="msg_456")
    @patch("app.services.email_orchestrator.settings")
    def test_auto_generated_key_dedupes(self, mock_settings, mock_send, db, test_user):
        """Auto-generated idempotency key deduplicates correctly."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result1 = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert result1["status"] == "sent"
        assert result1["idempotency_key"] == f"welcome:{test_user.id}"

        result2 = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert result2["status"] == "duplicate"


# ── Trigger Flow Tests ────────────────────────────────────────────────────────

class TestTriggerFlow:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_abc")
    @patch("app.services.email_orchestrator.settings")
    def test_successful_send_logs_to_email_log(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert result["status"] == "sent"

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == test_user.id,
                EmailLog.email_type == "WELCOME_EMAIL",
            )
        ).scalar_one_or_none()
        assert log is not None
        assert log.status == "sent"
        assert log.sent_at is not None
        assert log.provider_message_id == "msg_abc"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_xyz")
    @patch("app.services.email_orchestrator.settings")
    def test_welcome_stamps_user(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        assert test_user.welcome_email_sent_at is None
        trigger(db=db, email_type=EmailType.WELCOME, user_id=str(test_user.id))
        assert test_user.welcome_email_sent_at is not None

    @patch("app.services.email_orchestrator.send_email", return_value=None)
    @patch("app.services.email_orchestrator.settings")
    def test_send_failure_logged(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
            idempotency_key=f"fail_{test_user.id}",
        )
        assert result["status"] == "failed"
        assert result["reason"] == "send_failed"

        log = db.execute(
            select(EmailLog).where(EmailLog.idempotency_key == f"fail_{test_user.id}")
        ).scalar_one()
        assert log.status == "failed"
        assert log.provider_message_id is None

    @patch("app.services.email_orchestrator.settings")
    def test_nonexistent_user_returns_error(self, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(uuid.uuid4()),
        )
        assert result["status"] == "error"
        assert result["reason"] == "user_not_found"

    @patch("app.services.email_orchestrator.settings")
    def test_suppressed_user_logged(self, mock_settings, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        test_user.deleted_at = datetime.now(timezone.utc)
        db.flush()
        result = trigger(
            db=db,
            email_type=EmailType.NO_SIGNAL_REMINDER,
            user_id=str(test_user.id),
        )
        assert result["status"] == "suppressed"
        assert result["reason"] == "user_deleted"

    @patch("app.services.email_orchestrator.settings")
    def test_v2_disabled_returns_skipped(self, mock_settings, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = False
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "v2_disabled"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_ret")
    @patch("app.services.email_orchestrator.settings")
    def test_result_includes_idempotency_key(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert "idempotency_key" in result
        assert result["idempotency_key"] == f"welcome:{test_user.id}"


# ── DRY_RUN Tests ────────────────────────────────────────────────────────────

class TestDryRun:

    @patch("app.services.email_orchestrator.send_email", return_value="dry_run")
    @patch("app.services.email_orchestrator.settings")
    def test_dry_run_returns_dry_run_status(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        result = trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )
        assert result["status"] == "dry_run"

    @patch("app.services.email_orchestrator.send_email", return_value="dry_run")
    @patch("app.services.email_orchestrator.settings")
    def test_dry_run_stores_rendered_body_in_metadata(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == test_user.id,
                EmailLog.email_type == "WELCOME_EMAIL",
            )
        ).scalar_one()
        assert log.status == "dry_run"
        assert log.metadata_json is not None
        assert log.metadata_json.get("dry_run") is True
        assert "rendered_subject" in log.metadata_json
        assert "rendered_body" in log.metadata_json

    @patch("app.services.email_orchestrator.send_email", return_value="dry_run")
    @patch("app.services.email_orchestrator.settings")
    def test_dry_run_does_not_store_provider_message_id(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trigger(
            db=db,
            email_type=EmailType.WELCOME,
            user_id=str(test_user.id),
        )

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == test_user.id,
                EmailLog.email_type == "WELCOME_EMAIL",
            )
        ).scalar_one()
        assert log.provider_message_id is None


# ── Provider Message ID Tests ────────────────────────────────────────────────

class TestProviderMessageId:

    @patch("app.services.email_orchestrator.send_email", return_value="resend_msg_abc123")
    @patch("app.services.email_orchestrator.settings")
    def test_provider_id_stored_on_success(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trigger(
            db=db,
            email_type=EmailType.PRO_ACTIVATED,
            user_id=str(test_user.id),
        )

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == test_user.id,
                EmailLog.email_type == "PRO_ACTIVATED",
            )
        ).scalar_one()
        assert log.provider_message_id == "resend_msg_abc123"

    @patch("app.services.email_orchestrator.send_email", return_value=None)
    @patch("app.services.email_orchestrator.settings")
    def test_provider_id_none_on_failure(self, mock_settings, mock_send, db, test_user):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trigger(
            db=db,
            email_type=EmailType.PRO_ACTIVATED,
            user_id=str(test_user.id),
        )

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == test_user.id,
                EmailLog.email_type == "PRO_ACTIVATED",
            )
        ).scalar_one()
        assert log.provider_message_id is None
        assert log.status == "failed"


# ── Template Registry Tests ──────────────────────────────────────────────────

class TestTemplates:

    def test_all_types_have_templates(self):
        from app.services.email_templates import _REGISTRY
        for email_type in EmailType:
            assert email_type in _REGISTRY, f"Missing template for {email_type}"

    def test_welcome_renders(self, test_user):
        from app.services.email_templates import render_template
        subject, html = render_template(EmailType.WELCOME, user=test_user, context={})
        assert "Welcome" in subject
        assert "Trip Signal" in html
        assert "tripsignal.ca" in html

    def test_trial_expired_includes_coffee(self, test_user):
        from app.services.email_templates import render_template
        subject, html = render_template(EmailType.TRIAL_EXPIRED_UPSELL, user=test_user, context={})
        assert "cup of coffee" in html

    def test_payment_failed_renders(self, test_user):
        from app.services.email_templates import render_template
        subject, html = render_template(EmailType.PAYMENT_FAILED, user=test_user, context={})
        assert "payment" in subject.lower()
        assert "Update payment method" in html
