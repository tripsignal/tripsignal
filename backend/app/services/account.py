"""Account lifecycle operations (delete, etc.)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass

import stripe
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.user import User
from app.services.email import send_email

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# Valid reason codes
VALID_REASONS = {"price", "not_needed", "technical", "found_better", "privacy", "other"}


@dataclass
class DeleteResult:
    ok: bool
    already_deleted: bool = False
    stripe_canceled: bool = False
    email_sent: bool = False
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
    Soft-delete a user account.

    1. Cancel Stripe subscription if active (idempotent).
    2. Mark user as deleted with reason fields.
    3. Send confirmation email (best-effort, logged on failure).

    Safe to call multiple times — returns early if already deleted.
    """
    # Already deleted → no-op
    if user.deleted_at is not None:
        logger.info("delete_account: user %s already deleted, skipping", user.email)
        return DeleteResult(ok=True, already_deleted=True)

    # Validate reason
    if reason_code and reason_code not in VALID_REASONS:
        reason_code = "other"

    stripe_canceled = False

    # ── Step 1: Cancel Stripe subscription ──────────────────────────────
    if user.stripe_subscription_id:
        try:
            sub = stripe.Subscription.retrieve(user.stripe_subscription_id)
            if sub.status not in ("canceled", "incomplete_expired"):
                stripe.Subscription.cancel(user.stripe_subscription_id)
                logger.info(
                    "Stripe subscription %s canceled for %s",
                    user.stripe_subscription_id, user.email,
                )
            else:
                logger.info(
                    "Stripe subscription %s already %s for %s",
                    user.stripe_subscription_id, sub.status, user.email,
                )
            stripe_canceled = True
        except stripe.error.InvalidRequestError as e:
            # Subscription doesn't exist in Stripe — treat as already canceled
            if "No such subscription" in str(e):
                logger.warning(
                    "Stripe subscription %s not found — treating as canceled for %s",
                    user.stripe_subscription_id, user.email,
                )
                stripe_canceled = True
            else:
                logger.error(
                    "Stripe cancellation failed for %s: %s",
                    user.email, e,
                )
                return DeleteResult(
                    ok=False,
                    error=f"Stripe cancellation failed: {e}",
                )
        except Exception as e:
            logger.error(
                "Stripe cancellation error for %s: %s",
                user.email, e,
            )
            return DeleteResult(
                ok=False,
                error=f"Stripe error: {e}",
            )

    # ── Step 2: Soft-delete the user ────────────────────────────────────
    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.deleted_by = initiated_by
    user.deleted_reason = reason_code
    user.deleted_reason_other = reason_other
    user.plan_status = "deleted"
    user.email_opt_out = True  # Stop all notifications

    if stripe_canceled:
        user.stripe_canceled_at = now
        user.stripe_subscription_status = "canceled"

    try:
        db.commit()
        db.refresh(user)
        logger.info(
            "[%s] delete_account: %s soft-deleted (reason=%s)",
            initiated_by.upper(), user.email, reason_code,
        )
    except Exception as e:
        db.rollback()
        logger.error("DB commit failed during delete for %s: %s", user.email, e)
        return DeleteResult(ok=False, error=f"Database error: {e}")

    # ── Step 3: Send confirmation email (best-effort) ───────────────────
    # When V2 is enabled, the orchestrator handles deletion emails —
    # skip the legacy direct send to avoid duplicates.
    had_subscription = stripe_canceled or user.plan_type == "pro"
    email_sent = False
    if not settings.EMAIL_V2_ENABLED:
        email_sent = _send_deletion_email(user.email, had_subscription)

    return DeleteResult(
        ok=True,
        stripe_canceled=stripe_canceled,
        email_sent=email_sent,
    )


def _send_deletion_email(to: str, had_subscription: bool) -> bool:
    """Send account deletion confirmation email."""
    if had_subscription:
        subject = "Account deleted — subscription canceled"
        extra_line = (
            '<p style="margin: 0 0 16px; font-size: 14px; color: #333;">'
            "Your Pro subscription has been canceled and you will not be charged again."
            "</p>"
        )
    else:
        subject = "Your TripSignal account has been deleted"
        extra_line = ""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; background: #fff; max-width: 560px; margin: 0 auto; padding: 40px 24px;">

  <div style="margin-bottom: 24px;">
    <span style="font-size: 20px; font-weight: 600; letter-spacing: -0.3px;">Trip Signal</span>
  </div>

  <h1 style="font-size: 22px; font-weight: 600; margin: 0 0 16px;">Your account has been deleted</h1>

  <p style="margin: 0 0 16px; font-size: 14px; color: #333;">
    Thank you for trying TripSignal. Your account and all associated data have been removed.
    You will no longer receive deal alerts or notifications from us.
  </p>

  {extra_line}

  <p style="margin: 0 0 16px; font-size: 14px; color: #333;">
    If this was a mistake or you'd like to come back, you can always create a new account at
    <a href="https://tripsignal.ca" style="color: #1D4ED8;">tripsignal.ca</a>.
  </p>

  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">

  <p style="font-size: 12px; color: #999; margin: 0;">
    TripSignal &middot; Vacation deal monitoring for Canadians
  </p>

</body>
</html>"""

    return bool(send_email(to, subject, html))


def send_trial_expired_email(to: str) -> bool:
    """Send trial expiration upsell email."""
    subject = "Your TripSignal trial has ended — keep your signals running"

    html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; background: #fff; max-width: 560px; margin: 0 auto; padding: 40px 24px;">

  <div style="margin-bottom: 24px;">
    <span style="font-size: 20px; font-weight: 600; letter-spacing: -0.3px;">Trip Signal</span>
  </div>

  <h1 style="font-size: 22px; font-weight: 600; margin: 0 0 16px;">Your free trial has ended</h1>

  <p style="margin: 0 0 16px; font-size: 14px; color: #333;">
    Your signals have been paused, but your saved settings are still here.
    Upgrade to Pro to keep monitoring — for less than a cup of coffee a month.
  </p>

  <div style="background: #f0f7ff; border: 1px solid #dbeafe; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;">
    <p style="margin: 0 0 8px; font-size: 14px; font-weight: 600; color: #1D4ED8;">TripSignal Pro includes:</p>
    <ul style="margin: 0; padding-left: 20px; font-size: 14px; color: #333;">
      <li>Up to 10 active signals</li>
      <li>Prices checked every 6 hours</li>
      <li>Email + SMS alerts</li>
      <li>Price drop tracking &amp; history</li>
    </ul>
  </div>

  <a href="https://tripsignal.ca/signals" style="display: inline-block; background: #F97316; color: #fff; text-decoration: none; padding: 14px 28px; border-radius: 24px; font-size: 14px; font-weight: 600; margin-bottom: 32px;">
    Upgrade to Pro &rarr;
  </a>

  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">

  <p style="font-size: 12px; color: #999; margin: 0;">
    You're receiving this because your TripSignal free trial ended.<br>
    <a href="https://tripsignal.ca" style="color: #999;">tripsignal.ca</a>
  </p>

</body>
</html>"""

    return bool(send_email(to, subject, html))
