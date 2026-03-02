"""Clerk webhook handler — syncs user email on user.created / user.updated."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["clerk"])

CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET", "")


@router.post("/clerk/webhook")
async def clerk_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle Clerk webhook events (user.created, user.updated)."""
    if not CLERK_WEBHOOK_SECRET:
        logger.error("CLERK_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    # Read raw body for signature verification
    body = await request.body()

    # Verify Svix signature
    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        payload = wh.verify(body, headers)
    except WebhookVerificationError:
        logger.warning("Clerk webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = payload.get("type", "")
    data = payload.get("data", {})

    if event_type in ("user.created", "user.updated"):
        clerk_id = data.get("id")
        if not clerk_id:
            return {"ok": True, "skipped": "no clerk_id"}

        # Extract primary email
        email_addresses = data.get("email_addresses", [])
        primary_email_id = data.get("primary_email_address_id")
        email = ""
        for addr in email_addresses:
            if addr.get("id") == primary_email_id:
                email = addr.get("email_address", "")
                break
        if not email and email_addresses:
            email = email_addresses[0].get("email_address", "")

        # Update user in DB
        user = db.execute(
            select(User).where(User.clerk_id == clerk_id)
        ).scalar_one_or_none()

        if user:
            if email and email != user.email:
                logger.info("Clerk webhook: updating email for %s: %s -> %s", clerk_id, user.email, email)
                user.email = email
                db.commit()
            return {"ok": True, "action": "updated"}
        else:
            # user.created — create the user row if it doesn't exist yet
            if event_type == "user.created":
                new_user = User(clerk_id=clerk_id, email=email)
                db.add(new_user)
                db.commit()
                logger.info("Clerk webhook: created user %s with email %s", clerk_id, email)
                return {"ok": True, "action": "created"}
            return {"ok": True, "skipped": "user not found"}

    # Ignore other event types
    return {"ok": True, "skipped": event_type}
