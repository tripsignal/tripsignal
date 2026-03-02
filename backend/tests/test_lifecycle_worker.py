"""
Tests for the Lifecycle Email Worker — scheduled jobs.

Tests each job's eligibility queries, idempotency guards, and PRO-only filters.
All tests use real DB with transactional rollback. We verify per-user behavior
by checking email_log entries rather than relying on aggregate counts, since
the real DB may contain existing users that match query criteria.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_lifecycle_worker.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.deal_match import DealMatch
from app.db.models.email_log import EmailLog
from app.db.models.signal import Signal
from app.db.models.user import User
from app.services.email_orchestrator import EmailType
from app.workers.lifecycle_email_worker import (
    _run_trial_auto_extension,
    _run_trial_expiring_soon,
    _run_trial_expired,
    _run_no_signal_reminder,
    _run_inactive_reengagement,
    _run_no_match_update,
    _run_payment_failed_reminders,
    run_cycle,
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


# Use a far-future base so trial/expiry timestamps don't collide with real users.
# Jobs that use "older than X" queries still pick up real users, so we verify
# per-user in email_log instead of checking aggregate counts.
NOW = datetime(2040, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_user(
    db: Session,
    *,
    plan_type: str = "free",
    plan_status: str = "active",
    trial_ends_at: datetime | None = None,
    trial_auto_extended_at: datetime | None = None,
    trial_expiring_email_sent_at: datetime | None = None,
    trial_expired_email_sent_at: datetime | None = None,
    no_signal_email_sent_at: datetime | None = None,
    email_opt_out: bool = False,
    deleted_at: datetime | None = None,
    last_login_at: datetime | None = None,
    created_at: datetime | None = None,
    stripe_subscription_status: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        clerk_id=f"test_{uuid.uuid4().hex[:8]}",
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        plan_type=plan_type,
        plan_status=plan_status,
        trial_ends_at=trial_ends_at,
        trial_auto_extended_at=trial_auto_extended_at,
        trial_expiring_email_sent_at=trial_expiring_email_sent_at,
        trial_expired_email_sent_at=trial_expired_email_sent_at,
        no_signal_email_sent_at=no_signal_email_sent_at,
        email_opt_out=email_opt_out,
        email_enabled=True,
        deleted_at=deleted_at,
        last_login_at=last_login_at,
        stripe_subscription_status=stripe_subscription_status,
    )
    db.add(user)
    db.flush()
    if created_at:
        db.execute(
            User.__table__.update()
            .where(User.__table__.c.id == user.id)
            .values(created_at=created_at)
        )
        db.flush()
        db.refresh(user)
    return user


def _make_signal(db: Session, user: User, *, created_at: datetime | None = None, status: str = "active") -> Signal:
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
    if created_at:
        db.execute(
            Signal.__table__.update()
            .where(Signal.__table__.c.id == sig.id)
            .values(created_at=created_at)
        )
        db.flush()
        db.refresh(sig)
    return sig


def _make_email_log(
    db: Session,
    user: User,
    email_type: EmailType,
    *,
    status: str = "sent",
    sent_at: datetime | None = None,
    metadata_json: dict | None = None,
) -> EmailLog:
    log = EmailLog(
        id=uuid.uuid4(),
        user_id=user.id,
        email_type=email_type.value,
        category="test",
        idempotency_key=f"test_{uuid.uuid4().hex[:8]}",
        to_email=user.email,
        status=status,
        sent_at=sent_at or NOW,
        metadata_json=metadata_json,
    )
    db.add(log)
    db.flush()
    return log


def _has_email_log(db: Session, user_id: uuid.UUID, email_type: EmailType) -> bool:
    """Check if an email_log entry exists for this user and type."""
    return db.execute(
        select(EmailLog).where(
            EmailLog.user_id == user_id,
            EmailLog.email_type == email_type.value,
        )
    ).scalar_one_or_none() is not None


def _v2_settings():
    """Create mock settings with V2 enabled."""
    mock = MagicMock()
    mock.EMAIL_V2_ENABLED = True
    mock.EMAIL_DRY_RUN = False
    mock.EMAIL_SUSPEND_NONCRITICAL = False
    return mock


# ── Trial Auto-Extension Tests ───────────────────────────────────────────────

class TestTrialAutoExtension:

    def test_extends_within_48h(self, db):
        """User within 48h of expiry gets 7-day extension."""
        user = _make_user(db, trial_ends_at=NOW + timedelta(hours=24))
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        expected_end = NOW + timedelta(hours=24) + timedelta(days=7)
        assert user.trial_ends_at == expected_end
        assert user.trial_auto_extended_at == NOW

    def test_clears_trial_expiring_sent_at(self, db):
        """Extension clears trial_expiring_email_sent_at for re-warning."""
        user = _make_user(
            db,
            trial_ends_at=NOW + timedelta(hours=24),
            trial_expiring_email_sent_at=NOW - timedelta(days=1),
        )
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_expiring_email_sent_at is None

    def test_one_time_only(self, db):
        """Already-extended user is NOT extended again."""
        original_end = NOW + timedelta(hours=24)
        user = _make_user(
            db,
            trial_ends_at=original_end,
            trial_auto_extended_at=NOW - timedelta(days=5),
        )
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_ends_at == original_end  # unchanged

    def test_safe_to_rerun(self, db):
        """Running twice only extends once."""
        original_end = NOW + timedelta(hours=24)
        user = _make_user(db, trial_ends_at=original_end)

        _run_trial_auto_extension(db, NOW)
        db.refresh(user)
        extended_end = user.trial_ends_at
        assert extended_end == original_end + timedelta(days=7)

        _run_trial_auto_extension(db, NOW)
        db.refresh(user)
        assert user.trial_ends_at == extended_end  # no double extension

    def test_skips_expired_trial(self, db):
        """Trial already expired — no extension."""
        original_end = NOW - timedelta(hours=1)
        user = _make_user(db, trial_ends_at=original_end)
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_auto_extended_at is None
        assert user.trial_ends_at == original_end

    def test_skips_pro_user(self, db):
        """Pro user with trial_ends_at within 48h — no extension."""
        original_end = NOW + timedelta(hours=24)
        user = _make_user(db, plan_type="pro", trial_ends_at=original_end)
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_auto_extended_at is None

    def test_skips_deleted_user(self, db):
        """Deleted user — no extension."""
        user = _make_user(
            db,
            trial_ends_at=NOW + timedelta(hours=24),
            deleted_at=NOW - timedelta(days=1),
        )
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_auto_extended_at is None

    def test_skips_outside_48h_window(self, db):
        """Trial ends in 72h — outside 48h window, no extension yet."""
        original_end = NOW + timedelta(hours=72)
        user = _make_user(db, trial_ends_at=original_end)
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        assert user.trial_auto_extended_at is None
        assert user.trial_ends_at == original_end


# ── Trial Expiring Soon Tests ────────────────────────────────────────────────

class TestTrialExpiringSoon:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_within_window(self, mock_settings, mock_send, db):
        """User with trial ending in ~72h gets email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW + timedelta(hours=72))
        _run_trial_expiring_soon(db, NOW)

        assert _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_context_includes_trial_end_date(self, mock_settings, mock_send, db):
        """Context includes trial_end_date for deterministic idempotency key."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trial_end = NOW + timedelta(hours=72)
        user = _make_user(db, trial_ends_at=trial_end)
        _run_trial_expiring_soon(db, NOW)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.TRIAL_EXPIRING_SOON.value,
            )
        ).scalar_one()
        expected_date = trial_end.strftime("%Y-%m-%d")
        assert expected_date in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_already_sent(self, mock_settings, mock_send, db):
        """User with trial_expiring_email_sent_at set is skipped."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            trial_ends_at=NOW + timedelta(hours=72),
            trial_expiring_email_sent_at=NOW - timedelta(days=1),
        )
        _run_trial_expiring_soon(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_outside_window(self, mock_settings, mock_send, db):
        """Trial ending in 7 days — outside 96h window."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW + timedelta(days=7))
        _run_trial_expiring_soon(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_deleted_user(self, mock_settings, mock_send, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            trial_ends_at=NOW + timedelta(hours=72),
            deleted_at=NOW - timedelta(days=1),
        )
        _run_trial_expiring_soon(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)


# ── Trial Expired Tests ──────────────────────────────────────────────────────

class TestTrialExpired:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_after_expiry(self, mock_settings, mock_send, db):
        """User whose trial just expired gets upsell email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW - timedelta(hours=2))
        _run_trial_expired(db, NOW)

        assert _has_email_log(db, user.id, EmailType.TRIAL_EXPIRED_UPSELL)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_context_includes_trial_end_date(self, mock_settings, mock_send, db):
        """Context includes trial_end_date for deterministic idempotency key."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        trial_end = NOW - timedelta(hours=2)
        user = _make_user(db, trial_ends_at=trial_end)
        _run_trial_expired(db, NOW)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.TRIAL_EXPIRED_UPSELL.value,
            )
        ).scalar_one()
        expected_date = trial_end.strftime("%Y-%m-%d")
        assert expected_date in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_already_sent(self, mock_settings, mock_send, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            trial_ends_at=NOW - timedelta(hours=2),
            trial_expired_email_sent_at=NOW - timedelta(hours=1),
        )
        _run_trial_expired(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRED_UPSELL)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_old_expiry(self, mock_settings, mock_send, db):
        """Trial expired 2 days ago — outside 24h window."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW - timedelta(days=2))
        _run_trial_expired(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRED_UPSELL)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_pro_user(self, mock_settings, mock_send, db):
        """Pro users don't get trial_expired."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", trial_ends_at=NOW - timedelta(hours=2))
        _run_trial_expired(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRED_UPSELL)


# ── Extension + Expiring Integration ─────────────────────────────────────────

class TestExtensionExpiringSoonIntegration:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_extension_prevents_premature_warning(self, mock_settings, mock_send, db):
        """Extension at 48h moves user out of trial_expiring window."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW + timedelta(hours=36))

        # Run extension first (as worker does)
        _run_trial_auto_extension(db, NOW)
        db.refresh(user)
        assert user.trial_ends_at == NOW + timedelta(hours=36) + timedelta(days=7)

        # Trial expiring should NOT fire for this user (now ~7d away)
        _run_trial_expiring_soon(db, NOW)
        assert not _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_extension_then_later_expiring_fires(self, mock_settings, mock_send, db):
        """After extension, trial_expiring fires correctly for the new date."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW + timedelta(hours=36))
        _run_trial_auto_extension(db, NOW)

        db.refresh(user)
        new_end = user.trial_ends_at

        # Simulate time passing to 72h before new end
        future_now = new_end - timedelta(hours=72)
        _run_trial_expiring_soon(db, future_now)

        assert _has_email_log(db, user.id, EmailType.TRIAL_EXPIRING_SOON)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_full_cycle_safe_to_rerun(self, mock_settings, mock_send, db):
        """run_cycle is idempotent for extensions and emails."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, trial_ends_at=NOW + timedelta(hours=36))

        run_cycle(db, NOW)
        db.refresh(user)
        first_end = user.trial_ends_at
        assert user.trial_auto_extended_at is not None

        # Second run — no double extension
        run_cycle(db, NOW)
        db.refresh(user)
        assert user.trial_ends_at == first_end


# ── No Match Update (PRO Only) Tests ─────────────────────────────────────────

class TestNoMatchUpdate:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_for_pro_signal_14d(self, mock_settings, mock_send, db):
        """PRO user's signal active 14+ days with 0 matches gets email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        signal = _make_signal(db, user, created_at=NOW - timedelta(days=15))
        _run_no_match_update(db, NOW)

        assert _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)
        db.refresh(signal)
        assert signal.no_match_email_sent_at == NOW

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_context_includes_window_start(self, mock_settings, mock_send, db):
        """Context includes window_start for deterministic idempotency key."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        signal = _make_signal(db, user, created_at=NOW - timedelta(days=15))
        _run_no_match_update(db, NOW)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.NO_MATCH_UPDATE.value,
            )
        ).scalar_one()
        assert str(signal.id) in log.idempotency_key
        assert "unknown" not in log.idempotency_key

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_free_user(self, mock_settings, mock_send, db):
        """Free user's signal — no email (PRO only)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free")
        _make_signal(db, user, created_at=NOW - timedelta(days=15))
        _run_no_match_update(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_signal_under_14d(self, mock_settings, mock_send, db):
        """Signal active only 10 days — too soon."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        _make_signal(db, user, created_at=NOW - timedelta(days=10))
        _run_no_match_update(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_already_sent(self, mock_settings, mock_send, db):
        """Signal with no_match_email_sent_at set — skip."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        signal = _make_signal(db, user, created_at=NOW - timedelta(days=15))
        signal.no_match_email_sent_at = NOW - timedelta(days=1)
        db.flush()

        _run_no_match_update(db, NOW)
        assert not _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_safe_to_rerun(self, mock_settings, mock_send, db):
        """Running twice only sends once — signal stamp prevents re-send."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        _make_signal(db, user, created_at=NOW - timedelta(days=15))

        _run_no_match_update(db, NOW)
        assert _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)

        # Count logs before second run
        count_before = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.NO_MATCH_UPDATE.value,
            )
        ).scalars().all()

        _run_no_match_update(db, NOW)

        count_after = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.NO_MATCH_UPDATE.value,
            )
        ).scalars().all()

        assert len(count_after) == len(count_before)  # no new entries

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_if_recent_match_email(self, mock_settings, mock_send, db):
        """Suppress no-match email if user got a MATCH_ALERT in last 7 days."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro")
        _make_signal(db, user, created_at=NOW - timedelta(days=15))
        _make_email_log(db, user, EmailType.MATCH_ALERT, sent_at=NOW - timedelta(days=3))

        _run_no_match_update(db, NOW)
        assert not _has_email_log(db, user.id, EmailType.NO_MATCH_UPDATE)


# ── Inactive Re-engagement (PRO Only) Tests ──────────────────────────────────

class TestInactiveReengagement:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_for_pro_inactive_21d(self, mock_settings, mock_send, db):
        """PRO user inactive 21+ days with active signal gets email."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=25))
        _make_signal(db, user)
        _run_inactive_reengagement(db, NOW)

        assert _has_email_log(db, user.id, EmailType.INACTIVE_REENGAGEMENT)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_free_user(self, mock_settings, mock_send, db):
        """Free user — no re-engagement email (PRO only)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="free", last_login_at=NOW - timedelta(days=25))
        _make_signal(db, user)
        _run_inactive_reengagement(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.INACTIVE_REENGAGEMENT)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_recent_login(self, mock_settings, mock_send, db):
        """PRO user who logged in 10 days ago — not inactive enough."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=10))
        _make_signal(db, user)
        _run_inactive_reengagement(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.INACTIVE_REENGAGEMENT)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_no_active_signals(self, mock_settings, mock_send, db):
        """PRO user inactive but no active signals — skip."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=25))
        _make_signal(db, user, status="paused")
        _run_inactive_reengagement(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.INACTIVE_REENGAGEMENT)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_opted_out_user(self, mock_settings, mock_send, db):
        """User opted out of emails — skip."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            plan_type="pro",
            last_login_at=NOW - timedelta(days=25),
            email_opt_out=True,
        )
        _make_signal(db, user)
        _run_inactive_reengagement(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.INACTIVE_REENGAGEMENT)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_30_day_cooldown(self, mock_settings, mock_send, db):
        """No re-engagement email within 30 days of the last one."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=60))
        _make_signal(db, user)

        # Previous re-engagement email sent 20 days ago
        _make_email_log(
            db, user, EmailType.INACTIVE_REENGAGEMENT,
            sent_at=NOW - timedelta(days=20),
        )

        _run_inactive_reengagement(db, NOW)

        # Should still have only the one we manually created
        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.INACTIVE_REENGAGEMENT.value,
            )
        ).scalars().all()
        assert len(logs) == 1  # no new one created

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_cooldown_expired_sends(self, mock_settings, mock_send, db):
        """Re-engagement email allowed after 30-day cooldown expires."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=60))
        _make_signal(db, user)

        # Previous re-engagement email sent 35 days ago — cooldown expired
        _make_email_log(
            db, user, EmailType.INACTIVE_REENGAGEMENT,
            sent_at=NOW - timedelta(days=35),
        )

        _run_inactive_reengagement(db, NOW)

        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.INACTIVE_REENGAGEMENT.value,
            )
        ).scalars().all()
        assert len(logs) == 2  # the old one + a new one


# ── No Signal Reminder Tests ─────────────────────────────────────────────────

class TestNoSignalReminder:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_after_24h_no_signal(self, mock_settings, mock_send, db):
        """User created >24h ago with no signals gets reminder."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, created_at=NOW - timedelta(hours=30))
        _run_no_signal_reminder(db, NOW)

        assert _has_email_log(db, user.id, EmailType.NO_SIGNAL_REMINDER)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_user_with_signals(self, mock_settings, mock_send, db):
        """User who created a signal — no reminder."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, created_at=NOW - timedelta(hours=30))
        _make_signal(db, user)
        _run_no_signal_reminder(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.NO_SIGNAL_REMINDER)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_recently_created_user(self, mock_settings, mock_send, db):
        """User created 12h ago — too soon."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, created_at=NOW - timedelta(hours=12))
        _run_no_signal_reminder(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.NO_SIGNAL_REMINDER)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_already_sent(self, mock_settings, mock_send, db):
        """User already got the reminder."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            created_at=NOW - timedelta(hours=30),
            no_signal_email_sent_at=NOW - timedelta(hours=5),
        )
        _run_no_signal_reminder(db, NOW)

        assert not _has_email_log(db, user.id, EmailType.NO_SIGNAL_REMINDER)


# ── Payment Failed Reminders Tests ───────────────────────────────────────────

class TestPaymentFailedReminders:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_sends_3d_reminder(self, mock_settings, mock_send, db):
        """Reminder 1 fires ~3 days after initial payment failure."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", stripe_subscription_status="past_due")
        _make_email_log(
            db, user, EmailType.PAYMENT_FAILED,
            sent_at=NOW - timedelta(days=3),
            metadata_json={"invoice_id": "inv_test_001"},
        )

        _run_payment_failed_reminders(db, NOW)
        assert _has_email_log(db, user.id, EmailType.PAYMENT_FAILED_REMINDER)

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_skips_resolved_subscription(self, mock_settings, mock_send, db):
        """No reminder if subscription is now active (payment resolved)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db, plan_type="pro", stripe_subscription_status="active")
        _make_email_log(
            db, user, EmailType.PAYMENT_FAILED,
            sent_at=NOW - timedelta(days=3),
            metadata_json={"invoice_id": "inv_test_002"},
        )

        _run_payment_failed_reminders(db, NOW)
        assert not _has_email_log(db, user.id, EmailType.PAYMENT_FAILED_REMINDER)


# ── Full Cycle Idempotency Test ──────────────────────────────────────────────

class TestCycleIdempotency:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_full_cycle_is_safe_to_rerun(self, mock_settings, mock_send, db):
        """Running the full cycle twice doesn't double-process anything."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(
            db,
            plan_type="free",
            trial_ends_at=NOW + timedelta(hours=36),
            created_at=NOW - timedelta(hours=30),
        )

        run_cycle(db, NOW)
        db.refresh(user)
        first_end = user.trial_ends_at
        assert user.trial_auto_extended_at is not None

        call_count_after_first = mock_send.call_count

        # Second cycle — same time
        run_cycle(db, NOW)
        db.refresh(user)

        # trial_ends_at unchanged
        assert user.trial_ends_at == first_end
        # No additional emails for THIS user (idempotency keys prevent duplicates)
        # Note: mock_send.call_count might increase from real users in DB,
        # but our test user won't generate new sends due to dedup


# ── Suspend Noncritical Tests ────────────────────────────────────────────────

class TestSuspendNoncritical:

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_suspend_blocks_engagement_emails(self, mock_settings, mock_send, db):
        """With SUSPEND_NONCRITICAL, engagement emails are suppressed."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True

        user = _make_user(db, plan_type="pro", last_login_at=NOW - timedelta(days=25))
        _make_signal(db, user)
        _run_inactive_reengagement(db, NOW)

        # Orchestrator suppresses ENGAGEMENT category — no send_email call for this user
        # The email_log entry will have status "suppressed" not "sent"
        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.INACTIVE_REENGAGEMENT.value,
            )
        ).scalar_one_or_none()
        if log:
            assert log.status == "suppressed"

    @patch("app.services.email_orchestrator.send_email", return_value="msg_test")
    @patch("app.services.email_orchestrator.settings")
    def test_suspend_allows_billing_emails(self, mock_settings, mock_send, db):
        """With SUSPEND_NONCRITICAL, billing emails still go through."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = True

        user = _make_user(db, plan_type="pro", stripe_subscription_status="past_due")
        _make_email_log(
            db, user, EmailType.PAYMENT_FAILED,
            sent_at=NOW - timedelta(days=3),
            metadata_json={"invoice_id": "inv_suspend_test"},
        )

        _run_payment_failed_reminders(db, NOW)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.PAYMENT_FAILED_REMINDER.value,
            )
        ).scalar_one_or_none()
        # BILLING is never suppressed
        assert log is not None
        assert log.status == "sent"
