"""
Lifecycle Email Worker — scheduled jobs for trial, inactivity, no-match,
and payment retry reminder emails.

Runs as an infinite-loop polling worker (same pattern as notifications_log_worker).
Each cycle scans for eligible users/signals and calls the orchestrator.

Job execution order matters:
  1. Trial auto-extension (extends before warning fires)
  2. Trial expiring soon
  3. Trial expired
  4. No signal reminder
  5. Inactive re-engagement (PRO only)
  6. No match update (PRO only)
  7. Payment failed reminders
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal_match import DealMatch
from app.db.models.email_log import EmailLog
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import SessionLocal
from app.services.email_orchestrator import EmailType, trigger as email_trigger

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("LIFECYCLE_POLL_SECONDS", "300"))  # 5 min default


def main() -> None:
    """Entry point: infinite loop polling for lifecycle email jobs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logger.info("Lifecycle email worker starting (poll=%ds)", POLL_INTERVAL)

    while True:
        try:
            db = SessionLocal()
            try:
                run_cycle(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Lifecycle worker cycle error")

        time.sleep(POLL_INTERVAL)


def run_cycle(db: Session, now: datetime | None = None) -> None:
    """Run all lifecycle jobs once.  Extracted for testability."""
    if now is None:
        now = datetime.now(timezone.utc)

    # Order matters — extension must run before trial warnings.
    _run_trial_auto_extension(db, now)
    _run_trial_expiring_soon(db, now)
    _run_trial_expired(db, now)
    _run_no_signal_reminder(db, now)
    _run_inactive_reengagement(db, now)
    _run_no_match_update(db, now)
    _run_payment_failed_reminders(db, now)


# ── Job 1: Automatic 7-day trial extension (48h before expiry) ───────────────

def _run_trial_auto_extension(db: Session, now: datetime) -> int:
    """Extend trial by 7 days for users within 48h of expiry (one-time only).

    Guard: trial_auto_extended_at IS NULL ensures this fires exactly once.
    After extension, clears trial_expiring_email_sent_at so the warning
    re-fires with the correct days_left for the new end date.

    Returns the number of users extended (for testing).
    """
    window_end = now + timedelta(hours=48)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.plan_status == "active",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at <= window_end,
            User.trial_ends_at > now,  # trial hasn't expired yet
            User.trial_auto_extended_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    extended = 0
    for user in users:
        user.trial_ends_at = user.trial_ends_at + timedelta(days=7)
        user.trial_auto_extended_at = now
        # Clear so trial_expiring_soon re-fires for new date
        user.trial_expiring_email_sent_at = None
        extended += 1
        logger.info(
            "trial_auto_extension: extended %s to %s",
            user.email, user.trial_ends_at.isoformat(),
        )

    if extended:
        db.commit()
        logger.info("trial_auto_extension: extended %d users", extended)

    return extended


# ── Job 2: Trial expiring soon (72h before trial_ends_at) ────────────────────

def _run_trial_expiring_soon(db: Session, now: datetime) -> int:
    """Send TRIAL_EXPIRING_SOON to free-trial users whose trial ends in ~72h.

    Window: trial_ends_at between now+48h and now+96h (centered on 72h).
    Returns the number of users processed.
    """
    window_start = now + timedelta(hours=48)
    window_end = now + timedelta(hours=96)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.plan_status == "active",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at.between(window_start, window_end),
            User.trial_expiring_email_sent_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    sent = 0
    for user in users:
        days_left = max(1, round((user.trial_ends_at - now).total_seconds() / 86400))
        trial_end_date = user.trial_ends_at.strftime("%Y-%m-%d")
        try:
            email_trigger(
                db=db,
                email_type=EmailType.TRIAL_EXPIRING_SOON,
                user_id=str(user.id),
                context={
                    "days_left": days_left,
                    "trial_end_date": trial_end_date,
                },
            )
            sent += 1
        except Exception:
            logger.exception("trial_expiring_soon failed for %s", user.email)

    if users:
        logger.info("trial_expiring_soon: processed %d users", len(users))
    return sent


# ── Job 3: Trial expired ─────────────────────────────────────────────────────

def _run_trial_expired(db: Session, now: datetime) -> int:
    """Send TRIAL_EXPIRED_UPSELL to users whose trial ended recently.

    Window: trial_ends_at between now-24h and now (generous window so
    the 5-min poll interval doesn't miss anyone).
    Returns the number of users processed.
    """
    window_start = now - timedelta(hours=24)

    users = db.execute(
        select(User).where(
            User.plan_type == "free",
            User.trial_ends_at.isnot(None),
            User.trial_ends_at <= now,
            User.trial_ends_at >= window_start,
            User.trial_expired_email_sent_at.is_(None),
            User.deleted_at.is_(None),
        )
    ).scalars().all()

    sent = 0
    for user in users:
        trial_end_date = user.trial_ends_at.strftime("%Y-%m-%d")
        try:
            email_trigger(
                db=db,
                email_type=EmailType.TRIAL_EXPIRED_UPSELL,
                user_id=str(user.id),
                context={"trial_end_date": trial_end_date},
            )
            sent += 1
        except Exception:
            logger.exception("trial_expired failed for %s", user.email)

    if users:
        logger.info("trial_expired: processed %d users", len(users))
    return sent


# ── Job 4: No signal reminder (user created >24h ago, 0 signals) ─────────────

def _run_no_signal_reminder(db: Session, now: datetime) -> int:
    """Send NO_SIGNAL_REMINDER to users who signed up >24h ago with no signals."""
    cutoff = now - timedelta(hours=24)

    signal_count = (
        select(func.count(Signal.id))
        .where(Signal.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )

    users = db.execute(
        select(User).where(
            User.created_at <= cutoff,
            User.no_signal_email_sent_at.is_(None),
            User.deleted_at.is_(None),
            User.email != "",
            signal_count == 0,
        )
    ).scalars().all()

    sent = 0
    for user in users:
        try:
            email_trigger(
                db=db,
                email_type=EmailType.NO_SIGNAL_REMINDER,
                user_id=str(user.id),
            )
            sent += 1
        except Exception:
            logger.exception("no_signal_reminder failed for %s", user.email)

    if users:
        logger.info("no_signal_reminder: processed %d users", len(users))
    return sent


# ── Job 5: Inactive re-engagement (PRO only, last_login >21d) ────────────────

def _run_inactive_reengagement(db: Session, now: datetime) -> int:
    """Send INACTIVE_REENGAGEMENT to PRO users inactive for 21+ days.

    PRO only — free users don't get re-engagement emails.
    30-day cooldown between re-engagement emails per user.
    Returns the number of emails triggered.
    """
    cutoff = now - timedelta(days=21)

    active_signal_count = (
        select(func.count(Signal.id))
        .where(Signal.user_id == User.id, Signal.status == "active")
        .correlate(User)
        .scalar_subquery()
    )

    users = db.execute(
        select(User).where(
            User.plan_type == "pro",
            User.last_login_at.isnot(None),
            User.last_login_at <= cutoff,
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
            active_signal_count > 0,
        )
    ).scalars().all()

    sent = 0
    for user in users:
        # 30-day cooldown between re-engagement emails
        last_engagement = db.execute(
            select(func.max(EmailLog.sent_at)).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.INACTIVE_REENGAGEMENT.value,
                EmailLog.status.in_(("sent", "dry_run")),
            )
        ).scalar()
        if last_engagement and (now - last_engagement) < timedelta(days=30):
            continue

        days_inactive = (now - user.last_login_at).days
        try:
            email_trigger(
                db=db,
                email_type=EmailType.INACTIVE_REENGAGEMENT,
                user_id=str(user.id),
                context={
                    "days_inactive": days_inactive,
                    "period": now.strftime("%Y-%m"),
                },
            )
            sent += 1
        except Exception:
            logger.exception("inactive_reengagement failed for %s", user.email)

    if users:
        logger.info("inactive_reengagement: checked %d users", len(users))
    return sent


# ── Job 6: No match update (PRO only, signal active 14d, 0 matches) ──────────

def _run_no_match_update(db: Session, now: datetime) -> int:
    """Send NO_MATCH_UPDATE for PRO signals active 14+ days with 0 matches.

    PRO only — free users don't get no-match updates.
    One email per signal (guarded by signal.no_match_email_sent_at).
    Suppressed if user received a MATCH_ALERT in the last 7 days.
    Returns the number of emails triggered.
    """
    cutoff = now - timedelta(days=14)

    match_count = (
        select(func.count(DealMatch.id))
        .where(DealMatch.signal_id == Signal.id)
        .correlate(Signal)
        .scalar_subquery()
    )

    signals = db.execute(
        select(Signal).join(User, Signal.user_id == User.id).where(
            Signal.status == "active",
            Signal.created_at <= cutoff,
            Signal.no_match_email_sent_at.is_(None),
            User.plan_type == "pro",
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
            match_count == 0,
        )
    ).scalars().all()

    sent = 0
    for signal in signals:
        # Suppress if user got a match email in last 7 days
        recent_match_email = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == signal.user_id,
                EmailLog.email_type == EmailType.MATCH_ALERT.value,
                EmailLog.status.in_(("sent", "dry_run")),
                EmailLog.sent_at >= now - timedelta(days=7),
            ).limit(1)
        ).scalar_one_or_none()
        if recent_match_email:
            continue

        days_active = (now - signal.created_at).days
        # window_start for idempotency key — using ISO date of signal creation
        window_start = signal.created_at.strftime("%Y-%m-%d")
        try:
            result = email_trigger(
                db=db,
                email_type=EmailType.NO_MATCH_UPDATE,
                user_id=str(signal.user_id),
                context={
                    "signal_name": signal.name,
                    "signal_id": str(signal.id),
                    "days_active": days_active,
                    "window_start": window_start,
                },
            )
            # Stamp signal-level flag if sent or dry_run
            if result.get("status") in ("sent", "dry_run"):
                signal.no_match_email_sent_at = now
                db.commit()
            sent += 1
        except Exception:
            logger.exception("no_match_update failed for signal %s", signal.id)

    if signals:
        logger.info("no_match_update: checked %d signals", len(signals))
    return sent


# ── Job 7: Payment failed reminders (+3d and +7d) ────────────────────────────

def _run_payment_failed_reminders(db: Session, now: datetime) -> int:
    """Send PAYMENT_FAILED_REMINDER at +3 days and +7 days after initial failure."""
    sent = 0
    for reminder_num, days_after in [(1, 3), (2, 7)]:
        window_start = now - timedelta(days=days_after, hours=6)
        window_end = now - timedelta(days=days_after - 1)

        # Find users who got a PAYMENT_FAILED email in that window
        failed_logs = db.execute(
            select(EmailLog).where(
                EmailLog.email_type == EmailType.PAYMENT_FAILED.value,
                EmailLog.status.in_(("sent", "dry_run")),
                EmailLog.sent_at.between(window_start, window_end),
            )
        ).scalars().all()

        for log_entry in failed_logs:
            user = db.execute(
                select(User).where(User.id == log_entry.user_id)
            ).scalar_one_or_none()
            if not user or user.deleted_at:
                continue
            # Skip if subscription is resolved
            if user.stripe_subscription_status == "active":
                continue

            invoice_id = ""
            if log_entry.metadata_json:
                invoice_id = log_entry.metadata_json.get("invoice_id", "")

            try:
                email_trigger(
                    db=db,
                    email_type=EmailType.PAYMENT_FAILED_REMINDER,
                    user_id=str(user.id),
                    context={
                        "invoice_id": invoice_id,
                        "reminder_num": reminder_num,
                    },
                )
                sent += 1
            except Exception:
                logger.exception("payment_failed_reminder failed for %s", user.email)

    return sent


if __name__ == "__main__":
    main()
