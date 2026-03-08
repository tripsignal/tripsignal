"""Resend webhook handler for email open/click/delivery tracking."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from app.core.config import settings
from app.db.models.email_log import EmailLog
from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _lookup_user_from_message_id(db, message_id: str) -> tuple[EmailLog | None, User | None]:
    """Find the email_log entry and associated user from Resend message_id."""
    log_entry = db.execute(
        select(EmailLog).where(EmailLog.provider_message_id == message_id)
    ).scalar_one_or_none()

    if not log_entry or not log_entry.user_id:
        return log_entry, None

    user = db.execute(
        select(User).where(User.id == log_entry.user_id)
    ).scalar_one_or_none()

    return log_entry, user


@router.post("/resend")
async def resend_webhook(
    request: Request,
    db: Session = Depends(get_db),
    svix_id: str | None = Header(None, alias="svix-id"),
    svix_timestamp: str | None = Header(None, alias="svix-timestamp"),
    svix_signature: str | None = Header(None, alias="svix-signature"),
):
    """Handle Resend webhook events (opens, clicks, deliveries, bounces)."""
    if not settings.RESEND_WEBHOOK_SECRET:
        logger.error("SECURITY | resend_webhook_secret_missing | rejecting — RESEND_WEBHOOK_SECRET not configured")
        return JSONResponse(status_code=500, content={"error": "webhook not configured"})

    body = await request.body()

    # Verify signature using svix library (same approach as Clerk webhook)
    headers = {
        "svix-id": svix_id or "",
        "svix-timestamp": svix_timestamp or "",
        "svix-signature": svix_signature or "",
    }
    try:
        wh = Webhook(settings.RESEND_WEBHOOK_SECRET)
        event = wh.verify(body, headers)
    except WebhookVerificationError:
        logger.warning(
            "SECURITY | resend_webhook_sig_failed | ip=%s | svix_id=%s",
            request.client.host if request.client else "unknown",
            svix_id,
        )
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    event_type = event.get("type", "")
    data = event.get("data", {})

    message_id = data.get("email_id", "")

    if not message_id:
        logger.warning("Resend webhook missing email_id: type=%s", event_type)
        return {"ok": True}

    log_entry, user = _lookup_user_from_message_id(db, message_id)

    if not log_entry:
        logger.debug("No email_log found for message_id=%s", message_id)
        return {"ok": True}

    now = datetime.now(timezone.utc)

    if event_type == "email.opened":
        if user:
            user.last_email_opened_at = now
        meta = log_entry.metadata_json or {}
        opens = meta.get("opens", [])
        opens.append(now.isoformat())
        meta["opens"] = opens
        log_entry.metadata_json = meta
        logger.info("Email opened: user=%s type=%s", log_entry.user_id, log_entry.email_type)

    elif event_type == "email.clicked":
        if user:
            user.last_email_clicked_at = now
            user.email_mode = "active"
        meta = log_entry.metadata_json or {}
        clicks = meta.get("clicks", [])
        click_url = data.get("click", {}).get("link", "")
        clicks.append({"at": now.isoformat(), "url": click_url})
        meta["clicks"] = clicks
        log_entry.metadata_json = meta
        logger.info("Email clicked: user=%s type=%s", log_entry.user_id, log_entry.email_type)

    elif event_type == "email.delivered":
        if log_entry.status == "sent":
            log_entry.status = "delivered"

    elif event_type == "email.bounced":
        log_entry.status = "bounced"
        logger.warning("Email bounced: user=%s email=%s", log_entry.user_id, log_entry.to_email)

    elif event_type == "email.complained":
        log_entry.status = "complained"
        if user:
            user.email_opt_out = True
        logger.warning("Spam complaint: user=%s email=%s", log_entry.user_id, log_entry.to_email)

    db.commit()

    return {"ok": True}
