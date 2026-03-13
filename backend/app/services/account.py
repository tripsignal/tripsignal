"""Account lifecycle operations (delete, etc.)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass

import requests as http_requests
import stripe
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.email_log import EmailLog
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.signal import Signal
from app.db.models.user import User
logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# Valid reason codes
VALID_REASONS = {"price", "not_needed", "technical", "found_better", "privacy", "other"}

# Sentinel domain for scrubbed PII
_DELETED_DOMAIN = "deleted.tripsignal.ca"


@dataclass
class DeleteResult:
    ok: bool
    already_deleted: bool = False
    stripe_canceled: bool = False
    email_sent: bool = False
    clerk_deleted: bool = False
    error: str | None = None


def delete_account(
    *,
    db: Session,
    user: User,
    initiated_by: str,   # 'admin' | 'user'
    reason_code: str | None = None,
    reason_other: str | None = None,
) -> DeleteResult:
    """
    Delete a user account (PIPEDA-compliant).

    Phase 1: Cancel Stripe, mark deleted, send confirmation email.
    Phase 2: Scrub PII, deactivate signals, scrub related tables.
    Best-effort: Delete Clerk user via API.

    Safe to call multiple times — returns early if already deleted.
    """
    # Already deleted → no-op
    if user.deleted_at is not None:
        logger.info("delete_account: user %s already deleted, skipping", user.id)
        return DeleteResult(ok=True, already_deleted=True)

    # Validate reason
    if reason_code and reason_code not in VALID_REASONS:
        reason_code = "other"

    # Save PII before any mutations (needed for email + Clerk deletion)
    original_email = user.email
    original_clerk_id = user.clerk_id
    user_id_str = str(user.id)

    stripe_canceled = False

    # ── Step 1: Cancel Stripe subscription (idempotent) ───────────────
    if user.stripe_subscription_id:
        try:
            sub = stripe.Subscription.retrieve(user.stripe_subscription_id)
            if sub.status not in ("canceled", "incomplete_expired"):
                stripe.Subscription.cancel(user.stripe_subscription_id)
                logger.info(
                    "Stripe subscription %s canceled for %s",
                    user.stripe_subscription_id, original_email,
                )
            else:
                logger.info(
                    "Stripe subscription %s already %s for %s",
                    user.stripe_subscription_id, sub.status, original_email,
                )
            stripe_canceled = True
        except stripe.error.InvalidRequestError as e:
            if "No such subscription" in str(e):
                logger.warning(
                    "Stripe subscription %s not found — treating as canceled for %s",
                    user.stripe_subscription_id, original_email,
                )
                stripe_canceled = True
            else:
                logger.error(
                    "Stripe cancellation failed for %s: %s", original_email, e,
                )
                return DeleteResult(ok=False, error=f"Stripe cancellation failed: {e}")
        except Exception as e:
            logger.error("Stripe cancellation error for %s: %s", original_email, e)
            return DeleteResult(ok=False, error=f"Stripe error: {e}")

    # ── Step 2 (Phase 1 commit): Mark deleted — email still intact ────
    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.deleted_by = initiated_by
    user.deleted_reason = reason_code
    user.deleted_reason_other = reason_other
    user.plan_status = "deleted"
    user.email_opt_out = True

    if stripe_canceled:
        user.stripe_canceled_at = now
        user.stripe_subscription_status = "canceled"

    try:
        db.commit()
        logger.info(
            "[%s] delete_account phase 1: %s marked deleted (reason=%s)",
            initiated_by.upper(), original_email, reason_code,
        )
    except Exception as e:
        db.rollback()
        logger.error("Phase 1 commit failed for %s: %s", original_email, e)
        return DeleteResult(ok=False, error=f"Database error: {e}")

    # ── Step 3: Send confirmation email (email still readable in DB) ──
    had_subscription = stripe_canceled or user.plan_type == "pro"
    email_sent = False
    try:
        from app.services.email_orchestrator import EmailType
        from app.services.email_orchestrator import trigger as email_trigger
        email_type = (
            EmailType.ACCOUNT_DELETED_PRO if had_subscription
            else EmailType.ACCOUNT_DELETED_FREE
        )
        email_trigger(db=db, email_type=email_type, user_id=user_id_str)
        email_sent = True
    except Exception:
        logger.exception("Failed to trigger deletion email for %s", original_email)

    # ── Step 4 (Phase 2): Scrub PII + deactivate signals ─────────────
    sentinel_email = f"deleted-{user_id_str}@{_DELETED_DOMAIN}"

    user.email = sentinel_email
    user.clerk_id = f"deleted:{user_id_str}"
    user.first_name = None
    user.display_name = None
    user.last_login_ip = None
    user.last_login_user_agent = None
    user.stripe_customer_id = None
    user.stripe_subscription_id = None

    # Deactivate all user signals
    db.execute(
        update(Signal)
        .where(Signal.user_id == user.id, Signal.status != "deleted")
        .values(status="deleted")
    )

    # Scrub to_email in email_log
    db.execute(
        update(EmailLog)
        .where(EmailLog.user_id == user.id)
        .values(to_email=sentinel_email)
    )

    # Scrub to_email in notifications_outbox (no user_id column, match by email)
    db.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.to_email == original_email)
        .values(to_email=sentinel_email)
    )

    try:
        db.commit()
        logger.info(
            "[%s] delete_account phase 2: %s PII scrubbed",
            initiated_by.upper(), user_id_str,
        )
    except Exception as e:
        db.rollback()
        logger.error("Phase 2 (PII scrub) failed for %s: %s", user_id_str, e)
        # Phase 1 already committed — user is marked deleted (safe).
        # PII scrub can be retried via admin.
        return DeleteResult(
            ok=True, stripe_canceled=stripe_canceled, email_sent=email_sent,
            error=f"PII scrub failed (user is deleted but PII retained): {e}",
        )

    # ── Step 5: Delete Clerk user (best-effort, after DB commit) ──────
    clerk_deleted = _delete_clerk_user(original_clerk_id)

    return DeleteResult(
        ok=True,
        stripe_canceled=stripe_canceled,
        email_sent=email_sent,
        clerk_deleted=clerk_deleted,
    )


# ── Clerk user deletion ──────────────────────────────────────────────

def _delete_clerk_user(clerk_id: str) -> bool:
    """Delete user from Clerk via REST API. Returns True on success."""
    secret = settings.CLERK_SECRET_KEY
    if not secret:
        logger.warning("CLERK_SECRET_KEY not set — skipping Clerk user deletion")
        return False
    if not clerk_id or clerk_id.startswith("deleted:"):
        return False
    try:
        resp = http_requests.delete(
            f"https://api.clerk.com/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10,
        )
        if resp.status_code in (200, 404):
            logger.info("Clerk user %s deleted (status=%d)", clerk_id, resp.status_code)
            return True
        logger.error(
            "Clerk deletion failed for %s: %d %s",
            clerk_id, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as e:
        logger.error("Clerk deletion error for %s: %s", clerk_id, e)
        return False


# ── Restore (undelete) ────────────────────────────────────────────────

@dataclass
class RestoreResult:
    ok: bool
    not_deleted: bool = False
    error: str | None = None


def restore_account(
    *,
    db: Session,
    user: User,
) -> RestoreResult:
    """Restore a soft-deleted user account (admin-only).

    Clears deletion metadata, restores plan_status to active,
    re-enables email delivery.  Does NOT touch Stripe or signals.

    Cannot restore if PII has already been scrubbed.
    """
    if user.deleted_at is None:
        logger.info("restore_account: user %s is not deleted, skipping", user.id)
        return RestoreResult(ok=True, not_deleted=True)

    if user.email.endswith(f"@{_DELETED_DOMAIN}"):
        logger.warning("restore_account: user %s PII already scrubbed, cannot restore", user.id)
        return RestoreResult(
            ok=False,
            error="Cannot restore — PII has been scrubbed. User must re-register.",
        )

    user.deleted_at = None
    user.deleted_by = None
    user.deleted_reason = None
    user.deleted_reason_other = None
    user.plan_status = "active"
    user.email_opt_out = False

    try:
        db.commit()
        db.refresh(user)
        logger.info("[ADMIN] restore_account: %s restored", user.email)
    except Exception as e:
        db.rollback()
        logger.error("DB commit failed during restore for %s: %s", user.id, e)
        return RestoreResult(ok=False, error=f"Database error: {e}")

    return RestoreResult(ok=True)


