"""
Centralized Email Orchestrator — single entry point for all lifecycle emails.

Every email in the system flows through ``trigger()`` which:
1. Loads user + validates state.
2. Applies suppression rules (see ``_check_suppression``).
3. Computes a deterministic idempotency key and dedupes via email_log.
4. Inserts a "queued" row into email_log.
5. Renders the template.
6. Enqueues into email_queue for async, rate-limited delivery.
7. Stamps user-level sent_at flags.

The actual sending happens in the email queue drain worker, not here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.email_log import EmailLog
from app.db.models.user import User
from app.services.email_queue import enqueue as queue_enqueue

logger = logging.getLogger(__name__)


# ── Email categories ─────────────────────────────────────────────────────────

class EmailCategory(str, Enum):
    TRANSACTIONAL = "transactional"   # welcome, first signal, account deleted
    BILLING = "billing"               # pro activated, payment failed, canceled
    ALERT = "alert"                   # match found, major drop
    UPSELL = "upsell"                 # trial expiring, trial expired
    ENGAGEMENT = "engagement"         # no signal, inactive, no matches


class EmailType(str, Enum):
    WELCOME = "WELCOME_EMAIL"
    FIRST_SIGNAL = "FIRST_SIGNAL_EMAIL"
    NO_SIGNAL_REMINDER = "NO_SIGNAL_REMINDER"
    MATCH_ALERT = "MATCH_ALERT_EMAIL"
    MAJOR_DROP_ALERT = "MAJOR_DROP_ALERT"
    TRIAL_EXPIRING_SOON = "TRIAL_EXPIRING_SOON"
    TRIAL_EXPIRED_UPSELL = "TRIAL_EXPIRED_UPSELL"
    PRO_ACTIVATED = "PRO_ACTIVATED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_FAILED_REMINDER = "PAYMENT_FAILED_REMINDER"
    SUBSCRIPTION_CANCELED = "SUBSCRIPTION_CANCELED_CONFIRMATION"
    ACCOUNT_DELETED_FREE = "ACCOUNT_DELETED_FREE"
    ACCOUNT_DELETED_PRO = "ACCOUNT_DELETED_PRO"
    NO_MATCH_UPDATE = "NO_MATCH_UPDATE"
    INACTIVE_REENGAGEMENT = "INACTIVE_REENGAGEMENT"
    WEEKLY_DIGEST = "WEEKLY_DIGEST"


# Map each email type to its category
EMAIL_TYPE_CATEGORY: dict[str, EmailCategory] = {
    EmailType.WELCOME: EmailCategory.TRANSACTIONAL,
    EmailType.FIRST_SIGNAL: EmailCategory.TRANSACTIONAL,
    EmailType.ACCOUNT_DELETED_FREE: EmailCategory.TRANSACTIONAL,
    EmailType.ACCOUNT_DELETED_PRO: EmailCategory.TRANSACTIONAL,
    EmailType.PRO_ACTIVATED: EmailCategory.BILLING,
    EmailType.PAYMENT_FAILED: EmailCategory.BILLING,
    EmailType.PAYMENT_FAILED_REMINDER: EmailCategory.BILLING,
    EmailType.SUBSCRIPTION_CANCELED: EmailCategory.BILLING,
    EmailType.MATCH_ALERT: EmailCategory.ALERT,
    EmailType.MAJOR_DROP_ALERT: EmailCategory.ALERT,
    EmailType.TRIAL_EXPIRING_SOON: EmailCategory.UPSELL,
    EmailType.TRIAL_EXPIRED_UPSELL: EmailCategory.UPSELL,
    EmailType.NO_SIGNAL_REMINDER: EmailCategory.ENGAGEMENT,
    EmailType.NO_MATCH_UPDATE: EmailCategory.ENGAGEMENT,
    EmailType.INACTIVE_REENGAGEMENT: EmailCategory.ENGAGEMENT,
    EmailType.WEEKLY_DIGEST: EmailCategory.ALERT,
}

# Categories that honour the user's marketing/engagement opt-out
SUPPRESSIBLE_CATEGORIES = {EmailCategory.ENGAGEMENT, EmailCategory.UPSELL}

# Categories that are never suppressed (even if user is deleted — handled specially)
ALWAYS_SEND_CATEGORIES = {EmailCategory.TRANSACTIONAL, EmailCategory.BILLING}


# ── Orchestrator ─────────────────────────────────────────────────────────────

def trigger(
    *,
    db: Session,
    email_type: str | EmailType,
    user_id: str,
    context: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """
    Main entry point.  Returns dict with outcome:
      {"status": "queued"|"suppressed"|"duplicate"|"deferred"|"error",
       "reason": ...}
    """
    email_type = EmailType(email_type) if isinstance(email_type, str) else email_type
    context = context or {}
    category = EMAIL_TYPE_CATEGORY.get(email_type, EmailCategory.TRANSACTIONAL)

    # ── 1. Load user ──────────────────────────────────────────────────────
    user = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if not user:
        logger.warning("orchestrator: user %s not found", user_id)
        return {"status": "error", "reason": "user_not_found"}

    # ── 2. Suppression checks ─────────────────────────────────────────────
    suppression = _check_suppression(db, user, email_type, category)
    if suppression:
        if suppression in ("quiet_hours", "frequency_deferred"):
            _log_deferred(db, user, email_type, category, idempotency_key, context)
            return {"status": "deferred", "reason": suppression}
        _log_suppressed(db, user, email_type, category, idempotency_key, suppression)
        return {"status": "suppressed", "reason": suppression}

    # ── 3. Compute idempotency key ────────────────────────────────────────
    if not idempotency_key:
        idempotency_key = _build_idempotency_key(email_type, str(user.id), context)

    # ── 4. Insert pending row BEFORE sending (dedupe via unique key) ──────
    try:
        existing = db.execute(
            select(EmailLog).where(EmailLog.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            logger.info("orchestrator: duplicate key %s for %s", idempotency_key, email_type.value)
            return {"status": "duplicate", "reason": "idempotency_key_exists"}

        log_row = EmailLog(
            user_id=user.id,
            email_type=email_type.value,
            category=category.value,
            idempotency_key=idempotency_key,
            to_email=user.email,
            status="pending",
        )
        db.add(log_row)
        db.flush()
    except Exception as e:
        logger.error("orchestrator: email_log insert error: %s", e)
        db.rollback()
        return {"status": "error", "reason": str(e)}

    # ── 5. Render template ────────────────────────────────────────────────
    if "_unsub_url" not in context:
        try:
            from app.core.tokens import generate_unsub_token
            token = generate_unsub_token(str(user.id))
            context["_unsub_url"] = f"https://tripsignal.ca/unsubscribe?token={token}"
        except Exception:
            logger.warning("orchestrator: failed to generate unsub token for %s", user_id)

    context.setdefault("_notification_frequency", getattr(user, "notification_delivery_frequency", "all") or "all")

    from app.services.email_templates import render_template
    subject, html = render_template(email_type, user=user, context=context, db=db)

    # ── 6. Enqueue for async delivery ────────────────────────────────────
    log_row.subject = subject
    log_row.status = "queued"
    log_row.metadata_json = context if context else None

    try:
        queue_enqueue(
            db,
            to_email=user.email,
            subject=subject,
            html_body=html,
            email_log_id=log_row.id,
            email_type=email_type.value,
            category=category.value,
            user_id=str(user.id),
        )
    except Exception as e:
        logger.error("orchestrator: enqueue error: %s", e)
        log_row.status = "failed"

    # ── 7. Stamp user-level sent_at flags ─────────────────────────────────
    _stamp_user_sent(user, email_type, datetime.now(timezone.utc))

    try:
        db.commit()
    except Exception as e:
        logger.error("orchestrator: commit error: %s", e)
        db.rollback()
        return {"status": "error", "reason": str(e)}

    logger.info(
        "orchestrator: %s → %s (queued) key=%s",
        email_type.value, user.email, idempotency_key,
    )
    return {
        "status": "queued",
        "reason": None,
        "idempotency_key": idempotency_key,
    }


# ── Suppression logic ────────────────────────────────────────────────────────
#
# Evaluation order matters — most decisive rules first.
#
# 1. EMAIL_SUSPEND_NONCRITICAL (global kill-switch for ENGAGEMENT + UPSELL)
# 2. Deleted user (suppress all except ACCOUNT_DELETED_*)
# 3. email_opt_out / unsubscribed_marketing (suppress ENGAGEMENT + UPSELL)
# 4. email_enabled=false (suppress ALERT — user paused notifications)
# 5. 24-hour rate limit (max 2 ENGAGEMENT + UPSELL per 24h)
# 6. Upsell cooldown (no UPSELL within 48h of TRIAL_EXPIRING_SOON)
# 7. Canceled-after-deletion guard
#

def _check_suppression(
    db: Session, user: User, email_type: EmailType, category: EmailCategory,
) -> str | None:
    """Return a suppression reason string, or None if email should be sent."""

    # 1. Global noncritical suspension: suppress ENGAGEMENT and UPSELL emails.
    #    NEVER suppresses BILLING, TRANSACTIONAL, or ALERT.
    if settings.EMAIL_SUSPEND_NONCRITICAL and category in SUPPRESSIBLE_CATEGORIES:
        return "global_noncritical_suspended"

    # 2. Deleted users: suppress everything except the deletion confirmation itself.
    is_deletion_email = email_type in (EmailType.ACCOUNT_DELETED_FREE, EmailType.ACCOUNT_DELETED_PRO)
    if user.deleted_at is not None and not is_deletion_email:
        return "user_deleted"

    # 3. Marketing opt-out (email_opt_out): suppress ENGAGEMENT and UPSELL.
    #    BILLING and TRANSACTIONAL always send regardless of opt-out.
    if user.email_opt_out and category in SUPPRESSIBLE_CATEGORIES:
        return "email_opt_out"

    # 4. Notifications paused (email_enabled=False): suppress ALERT emails.
    if not user.email_enabled and category == EmailCategory.ALERT:
        return "email_disabled"

    # 5. Rate limit: max 2 non-alert lifecycle emails per 24h.
    #    Counts "sent" and "dry_run" statuses to prevent dry-run floods.
    if category in SUPPRESSIBLE_CATEGORIES:
        recent_count = db.execute(
            select(func.count(EmailLog.id)).where(
                EmailLog.user_id == user.id,
                EmailLog.category.in_(["engagement", "upsell"]),
                EmailLog.status.in_(["sent", "dry_run"]),
                EmailLog.sent_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        ).scalar() or 0
        if recent_count >= 2:
            return "rate_limit_24h"

    # 6. Upsell cooldown: suppress UPSELL within 48h of a TRIAL_EXPIRING_SOON email.
    if category == EmailCategory.UPSELL and email_type != EmailType.TRIAL_EXPIRING_SOON:
        trial_email = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.TRIAL_EXPIRING_SOON.value,
                EmailLog.status.in_(["sent", "dry_run"]),
                EmailLog.sent_at >= datetime.now(timezone.utc) - timedelta(hours=48),
            ).limit(1)
        ).scalar_one_or_none()
        if trial_email:
            return "upsell_after_trial_warning"

    # 7. Suppress SUBSCRIPTION_CANCELED if account was deleted within last 24h.
    if email_type == EmailType.SUBSCRIPTION_CANCELED:
        if user.deleted_at and (datetime.now(timezone.utc) - user.deleted_at) < timedelta(hours=24):
            return "canceled_after_deletion"

    # ── Anti-fatigue rules (Email Intelligence Spec) ──────────────────────

    # 8. Daily cap: max 3 instant alerts per user per day.
    if category == EmailCategory.ALERT and email_type != EmailType.WEEKLY_DIGEST:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        alert_today = db.execute(
            select(func.count(EmailLog.id)).where(
                EmailLog.user_id == user.id,
                EmailLog.category == "alert",
                EmailLog.status.in_(["sent", "dry_run", "delivered"]),
                EmailLog.sent_at >= today_start,
            )
        ).scalar() or 0
        if alert_today >= 3:
            return "daily_cap"

    # 10. Frequency-based deferral: non-"all" users get deferred for batch delivery.
    if category == EmailCategory.ALERT and email_type != EmailType.WEEKLY_DIGEST:
        if not user.is_instant_delivery:
            return "frequency_deferred"

    # 11. Re-engagement cap: max 1 re-engagement email per 60 days.
    if email_type == EmailType.INACTIVE_REENGAGEMENT:
        recent_reengage = db.execute(
            select(EmailLog).where(
                EmailLog.user_id == user.id,
                EmailLog.email_type == EmailType.INACTIVE_REENGAGEMENT.value,
                EmailLog.status.in_(["sent", "dry_run", "delivered"]),
                EmailLog.sent_at >= datetime.now(timezone.utc) - timedelta(days=60),
            ).limit(1)
        ).scalar_one_or_none()
        if recent_reengage:
            return "reengage_cap_60d"

    return None


# ── Deterministic idempotency keys ───────────────────────────────────────────
#
# Every key is a pure function of its inputs — no random UUIDs, no timestamps
# unless the timestamp IS the deduplication window (e.g. trial_end_date).
#
# Format: ``{type_prefix}:{scope_id}[:{qualifier}]``
#
# | EmailType               | Key                                           |
# |-------------------------|-----------------------------------------------|
# | WELCOME                 | welcome:{userId}                              |
# | FIRST_SIGNAL            | first_signal:{userId}                         |
# | TRIAL_EXPIRING_SOON     | trial_expiring:{userId}:{trial_end_date}      |
# | TRIAL_EXPIRED_UPSELL    | trial_expired:{userId}:{trial_end_date}       |
# | PRO_ACTIVATED           | pro_activated:{subscription_id}               |
# | PAYMENT_FAILED          | payment_failed:{invoice_id}                   |
# | PAYMENT_FAILED_REMINDER | payment_failed_reminder:{invoice_id}:{index}  |
# | SUBSCRIPTION_CANCELED   | subscription_canceled:{subscription_id}       |
# | ACCOUNT_DELETED_FREE    | account_deleted_free:{userId}                 |
# | ACCOUNT_DELETED_PRO     | account_deleted_pro:{userId}                  |
# | MATCH_ALERT             | match_alert:{userId}:{runId}                  |
# | MAJOR_DROP_ALERT        | major_drop:{signalId}:{dealId}                |
# | NO_SIGNAL_REMINDER      | no_signal:{userId}                            |
# | NO_MATCH_UPDATE         | no_match:{signalId}:{window_start}            |
# | INACTIVE_REENGAGEMENT   | inactive:{userId}:{window_start}              |

def _build_idempotency_key(email_type: EmailType, user_id: str, context: dict) -> str:
    """Generate a deterministic idempotency key from event identifiers.

    Callers can also pass an explicit ``idempotency_key`` to ``trigger()``
    to override this default.
    """
    uid = str(user_id)

    if email_type == EmailType.WELCOME:
        return f"welcome:{uid}"

    if email_type == EmailType.FIRST_SIGNAL:
        return f"first_signal:{uid}"

    if email_type == EmailType.TRIAL_EXPIRING_SOON:
        trial_end = context.get("trial_end_date", "unknown")
        return f"trial_expiring:{uid}:{trial_end}"

    if email_type == EmailType.TRIAL_EXPIRED_UPSELL:
        trial_end = context.get("trial_end_date", "unknown")
        return f"trial_expired:{uid}:{trial_end}"

    if email_type == EmailType.PRO_ACTIVATED:
        sub_id = context.get("subscription_id", uid)
        return f"pro_activated:{sub_id}"

    if email_type == EmailType.PAYMENT_FAILED:
        invoice_id = context.get("invoice_id", "unknown")
        return f"payment_failed:{invoice_id}"

    if email_type == EmailType.PAYMENT_FAILED_REMINDER:
        invoice_id = context.get("invoice_id", "unknown")
        index = context.get("reminder_num", context.get("index", "0"))
        return f"payment_failed_reminder:{invoice_id}:{index}"

    if email_type == EmailType.SUBSCRIPTION_CANCELED:
        sub_id = context.get("subscription_id", uid)
        return f"subscription_canceled:{sub_id}"

    if email_type == EmailType.ACCOUNT_DELETED_FREE:
        return f"account_deleted_free:{uid}"

    if email_type == EmailType.ACCOUNT_DELETED_PRO:
        return f"account_deleted_pro:{uid}"

    if email_type == EmailType.MATCH_ALERT:
        run_id = context.get("run_id", "unknown")
        return f"match_alert:{uid}:{run_id}"

    if email_type == EmailType.MAJOR_DROP_ALERT:
        signal_id = context.get("signal_id", "unknown")
        deal_id = context.get("deal_id", "unknown")
        return f"major_drop:{signal_id}:{deal_id}"

    if email_type == EmailType.NO_SIGNAL_REMINDER:
        return f"no_signal:{uid}"

    if email_type == EmailType.NO_MATCH_UPDATE:
        signal_id = context.get("signal_id", "unknown")
        window_start = context.get("window_start", "unknown")
        return f"no_match:{signal_id}:{window_start}"

    if email_type == EmailType.INACTIVE_REENGAGEMENT:
        window_start = context.get("window_start", context.get("period", "unknown"))
        return f"inactive:{uid}:{window_start}"

    if email_type == EmailType.WEEKLY_DIGEST:
        week_iso = context.get("week_iso", datetime.now(timezone.utc).strftime("%Y-W%W"))
        return f"weekly_digest:{uid}:{week_iso}"

    # Fallback (should never happen if all types are covered above)
    return f"{email_type.value}:{uid}"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _stamp_user_sent(user: User, email_type: EmailType, now: datetime) -> None:
    """Set user-level sent_at flags to prevent re-queries by scheduled jobs."""
    stamp_map = {
        EmailType.WELCOME: "welcome_email_sent_at",
        EmailType.TRIAL_EXPIRING_SOON: "trial_expiring_email_sent_at",
        EmailType.TRIAL_EXPIRED_UPSELL: "trial_expired_email_sent_at",
        EmailType.NO_SIGNAL_REMINDER: "no_signal_email_sent_at",
    }
    attr = stamp_map.get(email_type)
    if attr and hasattr(user, attr):
        setattr(user, attr, now)


def _log_suppressed(
    db: Session,
    user: User,
    email_type: EmailType,
    category: EmailCategory,
    idempotency_key: str | None,
    reason: str,
) -> None:
    """Record a suppressed email in the log for audit purposes."""
    key = idempotency_key or _build_idempotency_key(email_type, str(user.id), {})
    try:
        stmt = pg_insert(EmailLog).values(
            user_id=user.id,
            email_type=email_type.value,
            category=category.value,
            idempotency_key=f"suppressed:{key}:{reason}",
            to_email=user.email,
            status="suppressed",
            suppressed_reason=reason,
        ).on_conflict_do_nothing(index_elements=["idempotency_key"])
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
    logger.info("orchestrator: SUPPRESSED %s → %s reason=%s", email_type.value, user.email, reason)


def _log_deferred(
    db: Session,
    user: User,
    email_type: EmailType,
    category: EmailCategory,
    idempotency_key: str | None,
    context: dict | None,
) -> None:
    """Record a deferred email with full context for later delivery."""
    key = idempotency_key or _build_idempotency_key(email_type, str(user.id), context or {})
    reason = "frequency_deferred" if not user.is_instant_delivery else "quiet_hours"
    try:
        stmt = pg_insert(EmailLog).values(
            user_id=user.id,
            email_type=email_type.value,
            category=category.value,
            idempotency_key=key,
            to_email=user.email,
            status="deferred",
            suppressed_reason=reason,
            metadata_json=context,
        ).on_conflict_do_nothing(index_elements=["idempotency_key"])
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
    logger.info("orchestrator: DEFERRED %s → %s reason=%s", email_type.value, user.email, reason)


FREQUENCY_WINDOWS = {
    "morning": (7, 10),   # 7:00 AM – 10:59 AM
    "noon": (11, 13),     # 11:00 AM – 1:59 PM
    "evening": (17, 20),  # 5:00 PM – 8:59 PM
}


def drain_deferred_emails(db: Session) -> int:
    """Enqueue deferred frequency-window emails when the user's chosen window arrives.

    Called by the lifecycle worker every poll cycle (~5 min).
    Renders deferred emails and pushes them into the email_queue for delivery.
    Returns the number of emails enqueued.
    """
    rows = db.execute(
        select(EmailLog).where(
            EmailLog.status == "deferred",
            EmailLog.suppressed_reason.in_(["quiet_hours", "frequency_deferred"]),
        ).order_by(EmailLog.created_at.asc()).limit(50)
    ).scalars().all()

    if not rows:
        return 0

    enqueued = 0
    for row in rows:
        user = db.execute(
            select(User).where(User.id == row.user_id)
        ).scalar_one_or_none()

        if not user:
            row.status = "suppressed"
            row.suppressed_reason = "user_not_found"
            db.commit()
            continue

        # Not yet in a delivery window — skip, will retry next cycle
        if not _in_delivery_window(user):
            continue

        # Window matched — render and enqueue
        email_type = EmailType(row.email_type)
        context = row.metadata_json or {}

        if "_unsub_url" not in context:
            try:
                from app.core.tokens import generate_unsub_token
                token = generate_unsub_token(str(user.id))
                context["_unsub_url"] = f"https://tripsignal.ca/unsubscribe?token={token}"
            except Exception:
                logger.warning("drain_deferred: failed to generate unsub token for %s", user.id)

        context.setdefault("_notification_frequency", getattr(user, "notification_delivery_frequency", "all") or "all")

        try:
            from app.services.email_templates import render_template
            subject, html = render_template(email_type, user=user, context=context, db=db)

            row.subject = subject
            row.status = "queued"
            row.suppressed_reason = None

            queue_enqueue(
                db,
                to_email=user.email,
                subject=subject,
                html_body=html,
                email_log_id=row.id,
                email_type=email_type.value,
                category=row.category,
                user_id=str(user.id),
            )
            _stamp_user_sent(user, email_type, datetime.now(timezone.utc))
            db.commit()
            enqueued += 1
        except Exception:
            logger.exception("drain_deferred: failed to enqueue %s to %s", row.email_type, user.email)
            db.rollback()

    logger.info("drain_deferred: processed %d deferred emails, enqueued %d", len(rows), enqueued)
    return enqueued


def _in_delivery_window(user: User) -> bool:
    """Check if the current time falls within one of the user's frequency windows.

    For "all" users (instant delivery), always returns True (they shouldn't have
    deferred emails, but handle gracefully).
    For time-based windows, matches if the current hour in the user's timezone
    falls within the window range:
      morning = 7–10 AM, noon = 11 AM–1 PM, evening = 5–8 PM.
    """
    if user.is_instant_delivery:
        return True

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(user.timezone or "America/Toronto")
    except Exception:
        tz = __import__("zoneinfo").ZoneInfo("America/Toronto")

    now_local = datetime.now(timezone.utc).astimezone(tz)
    current_hour = now_local.hour

    for window in user.frequency_windows:
        bounds = FREQUENCY_WINDOWS.get(window)
        if bounds is not None:
            start_hour, end_hour = bounds
            if start_hour <= current_hour <= end_hour:
                return True

    return False
