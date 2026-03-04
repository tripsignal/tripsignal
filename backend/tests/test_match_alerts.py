"""
Integration tests for match alert service.

Tests verify:
- 3 matches in same run => 1 email per signal.
- New low overrides multi-deal subject.
- 9% drop does NOT trigger pct preview.
- 10% drop DOES trigger pct preview.
- Signal intelligence fields updated (last_check_min_price, all_time_low).
- Idempotency: repeat call with same run_id => no double send.
- Deterministic batching per signalId + runId.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_match_alerts.py -v
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.email_log import EmailLog
from app.db.models.signal import Signal
from app.db.models.user import User
from app.services.email_orchestrator import EmailType
from app.services.match_alert import process_signal_matches


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


def _make_user(db: Session, *, plan_type: str = "pro") -> User:
    user = User(
        id=uuid.uuid4(),
        clerk_id=f"test_{uuid.uuid4().hex[:8]}",
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        plan_type=plan_type,
        plan_status="active",
        email_enabled=True,
        email_opt_out=False,
    )
    db.add(user)
    db.flush()
    return user


def _make_signal(
    db: Session,
    user: User,
    *,
    name: str = "Test Signal",
    last_check_min_price: int | None = None,
    all_time_low_price: int | None = None,
) -> Signal:
    signal = Signal(
        id=uuid.uuid4(),
        name=name,
        status="active",
        user_id=user.id,
        departure_airports=["YQR"],
        destination_regions=["cancun"],
        config={
            "departure": {"mode": "single", "airports": ["YQR"]},
            "destination": {"mode": "single", "regions": ["cancun"]},
            "travel_window": {"start_month": "2026-03", "end_month": "2026-06", "min_nights": 7, "max_nights": 10},
            "travellers": {"adults": 2, "children_ages": [], "rooms": 1},
            "budget": {"currency": "CAD", "target_pp": 1500, "strict": False},
            "notifications": {"email_enabled": True, "email": user.email},
            "preferences": {},
        },
        last_check_min_price=last_check_min_price,
        all_time_low_price=all_time_low_price,
    )
    db.add(signal)
    db.flush()
    return signal


def _deal_dict(
    *,
    price_cents: int = 89900,
    hotel_name: str = "Riu Palace",
    star_rating: float = 4.5,
    destination_str: str = "Cancun",
    origin: str = "YQR",
    deeplink_url: str = "https://example.com/deal",
    duration_nights: int = 7,
) -> dict:
    return {
        "deal_id": str(uuid.uuid4()),
        "price_cents": price_cents,
        "hotel_name": hotel_name,
        "star_rating": star_rating,
        "depart_date": date(2026, 4, 15),
        "return_date": date(2026, 4, 22),
        "duration_nights": duration_nights,
        "destination_str": destination_str,
        "origin": origin,
        "deeplink_url": deeplink_url,
        "price_dropped": False,
        "price_delta": 0,
    }


RUN_ID = str(uuid.uuid4())


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestBatching:
    """Multiple matches in one run produce exactly one email per signal."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_three_matches_one_email(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user)
        run_id = str(uuid.uuid4())

        deals = [
            _deal_dict(price_cents=89900, hotel_name="Riu Palace"),
            _deal_dict(price_cents=99900, hotel_name="Iberostar"),
            _deal_dict(price_cents=109900, hotel_name="Barcelo"),
        ]

        results = process_signal_matches(
            db=db,
            signal_deals={str(signal.id): deals},
            run_id=run_id,
        )

        assert len(results) == 1
        assert results[0]["status"] == "sent"

        # Only 1 email logged
        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalars().all()
        assert len(logs) == 1

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_repeat_run_id_no_double_send(self, mock_send, mock_settings, db):
        """Same signal+run_id called twice => only 1 email (idempotency)."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user)
        run_id = str(uuid.uuid4())
        deals = [_deal_dict()]

        # First call
        r1 = process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)
        assert r1[0]["status"] == "sent"

        # Second call (same run_id)
        r2 = process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)
        assert r2[0]["status"] == "duplicate"

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_two_signals_two_emails(self, mock_send, mock_settings, db):
        """Two signals with matches in same run => two separate emails."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        sig1 = _make_signal(db, user, name="Signal A")
        sig2 = _make_signal(db, user, name="Signal B")
        run_id = str(uuid.uuid4())

        results = process_signal_matches(
            db=db,
            signal_deals={
                str(sig1.id): [_deal_dict(price_cents=80000)],
                str(sig2.id): [_deal_dict(price_cents=90000)],
            },
            run_id=run_id,
        )

        assert len(results) == 2
        assert all(r["status"] == "sent" for r in results)


class TestSubjectPriority:
    """Subject line follows locked priority: new_low > pct_drop > single > multi."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_new_low_overrides_multi_deal_subject(self, mock_send, mock_settings, db):
        """New all-time low takes priority over multi-deal subject."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        # Signal has existing all-time low of $1000
        signal = _make_signal(db, user, all_time_low_price=100000)
        run_id = str(uuid.uuid4())

        # 3 deals, best is $899 (below all-time low of $1000)
        deals = [
            _deal_dict(price_cents=89900, hotel_name="Riu"),
            _deal_dict(price_cents=95000, hotel_name="Iberostar"),
            _deal_dict(price_cents=99900, hotel_name="Barcelo"),
        ]

        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        # Subject should start with "New low:" — not "New deals found (3):"
        assert log.subject.startswith("New low:")

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_first_check_is_always_new_low(self, mock_send, mock_settings, db):
        """First-ever check (no all_time_low_price) is always a new low."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, all_time_low_price=None)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=89900)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        assert log.subject.startswith("New low:")


class TestPctDropThresholds:
    """Percentage drop preview: >= 10% triggers, < 10% does not."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_nine_pct_drop_no_pct_preview(self, mock_send, mock_settings, db):
        """9% price drop does NOT trigger percentage preview."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        # Previous min was $1000, now $910 = 9% drop
        signal = _make_signal(db, user, last_check_min_price=100000, all_time_low_price=91000)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=91000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        # Subject should NOT be "Price drop:" — 9% is below threshold
        assert not log.subject.startswith("Price drop:")
        # Should NOT mention "New low" since $910 == existing all_time_low
        assert not log.subject.startswith("New low:")
        # Should be single/multi deal subject
        assert "New deal:" in log.subject or "New deals found" in log.subject

        # Check rendered body in metadata — should NOT contain "Down 9%"
        meta = log.metadata_json or {}
        rendered = meta.get("rendered_body", "")
        assert "Down 9%" not in rendered

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_ten_pct_drop_triggers_pct_preview(self, mock_send, mock_settings, db):
        """10% price drop DOES trigger percentage preview."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        # Previous min was $1000, now $900 = 10% drop
        signal = _make_signal(db, user, last_check_min_price=100000, all_time_low_price=90000)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=90000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        # Subject: "Price drop:" (10% >= threshold, no new low)
        assert log.subject.startswith("Price drop:")

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_twenty_pct_drop_also_triggers(self, mock_send, mock_settings, db):
        """20% price drop triggers percentage preview."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = True
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        # Previous min was $1000, now $800 = 20% drop
        signal = _make_signal(db, user, last_check_min_price=100000, all_time_low_price=80000)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=80000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        assert log.subject.startswith("Price drop:")


class TestSignalIntelligence:
    """Signal intelligence fields updated correctly."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_last_check_min_price_updated(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, last_check_min_price=None)
        run_id = str(uuid.uuid4())

        deals = [
            _deal_dict(price_cents=89900),
            _deal_dict(price_cents=99900),
        ]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        db.refresh(signal)
        assert signal.last_check_min_price == 89900
        assert signal.last_check_at is not None

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_all_time_low_set_on_first_check(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, all_time_low_price=None)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=75000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        db.refresh(signal)
        assert signal.all_time_low_price == 75000
        assert signal.all_time_low_at is not None

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_all_time_low_updated_when_new_low(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, all_time_low_price=90000)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=85000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        db.refresh(signal)
        assert signal.all_time_low_price == 85000

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_all_time_low_not_updated_when_higher(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, all_time_low_price=70000)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(price_cents=85000)]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        db.refresh(signal)
        # Should remain at 70000, not jump to 85000
        assert signal.all_time_low_price == 70000

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_no_match_guard_cleared(self, mock_send, mock_settings, db):
        """no_match_email_sent_at is cleared when matches arrive."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user)
        signal.no_match_email_sent_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        db.flush()

        run_id = str(uuid.uuid4())
        deals = [_deal_dict()]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        db.refresh(signal)
        assert signal.no_match_email_sent_at is None


class TestIdempotencyKey:
    """Idempotency key format: match_alert:{signalId}:{runId}."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_key_format(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict()]
        results = process_signal_matches(
            db=db, signal_deals={str(signal.id): deals}, run_id=run_id,
        )

        expected_key = f"match_alert:{signal.id}:{run_id}"
        assert results[0]["idempotency_key"] == expected_key

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_different_run_ids_different_emails(self, mock_send, mock_settings, db):
        """Same signal, different run_ids => separate emails."""
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user)
        run1 = str(uuid.uuid4())
        run2 = str(uuid.uuid4())

        deals = [_deal_dict()]

        r1 = process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run1)
        r2 = process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run2)

        assert r1[0]["status"] == "sent"
        assert r2[0]["status"] == "sent"

        logs = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
                EmailLog.status == "sent",
            )
        ).scalars().all()
        assert len(logs) == 2


class TestRouteBuilding:
    """Route string for subject line."""

    @patch("app.services.email_orchestrator.settings")
    @patch("app.services.email_orchestrator.send_email", return_value="msg_123")
    def test_route_in_subject(self, mock_send, mock_settings, db):
        mock_settings.EMAIL_V2_ENABLED = True
        mock_settings.EMAIL_DRY_RUN = False
        mock_settings.EMAIL_SUSPEND_NONCRITICAL = False

        user = _make_user(db)
        signal = _make_signal(db, user, all_time_low_price=None)
        run_id = str(uuid.uuid4())

        deals = [_deal_dict(origin="YQR", destination_str="Cancun")]
        process_signal_matches(db=db, signal_deals={str(signal.id): deals}, run_id=run_id)

        log = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
            )
        ).scalar_one()

        # Route should appear in subject
        assert "YQR" in log.subject
        assert "Cancun" in log.subject


class TestNoDirectSend:
    """Match alert service must use orchestrator, never send directly."""

    def test_no_send_email_import(self):
        """match_alert.py should not import send_email."""
        import inspect
        import app.services.match_alert as mod
        source = inspect.getsource(mod)
        assert "send_email" not in source
        assert "email_trigger" in source or "trigger" in source

    def test_uses_only_orchestrator(self):
        """match_alert.py only uses orchestrator trigger."""
        import inspect
        import app.services.match_alert as mod
        source = inspect.getsource(mod)
        assert "from app.services.email_orchestrator import" in source
