"""User lookup and preference endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.db.models.signal import Signal
from app.db.session import get_db
from app.services.account import delete_account as _delete_account
from app.services.email_orchestrator import trigger as email_trigger, EmailType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_user_by_clerk(clerk_id: str, db: Session) -> User:
    user = db.execute(select(User).where(User.clerk_id == clerk_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── GET /users/by-clerk-id/{clerk_id} ───────────────────────────────────────

@router.get("/by-clerk-id/{clerk_id}")
def get_user_by_clerk_id(clerk_id: str, db: Session = Depends(get_db)):
    user = _get_user_by_clerk(clerk_id, db)
    return {
        "id": str(user.id),
        "email": user.email,
        "clerk_id": user.clerk_id,
        "role": user.role,
        "plan_type": user.plan_type,
        "plan_status": user.plan_status,
        "trial_ends_at": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        "subscription_current_period_end": (
            user.subscription_current_period_end.isoformat()
            if user.subscription_current_period_end
            else None
        ),
        "pro_activation_completed_at": (
            user.pro_activation_completed_at.isoformat()
            if user.pro_activation_completed_at
            else None
        ),
    }


# ── POST /users/sync ────────────────────────────────────────────────────────

@router.post("/sync")
def sync_user(
    db: Session = Depends(get_db),
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
    x_forwarded_for: str | None = Header(None, alias="x-forwarded-for"),
    user_agent: str | None = Header(None, alias="user-agent"),
):
    """Ensure user row exists for the given Clerk ID. Called on sign-in."""
    # Extract first IP from X-Forwarded-For (client IP before proxies)
    client_ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else None

    user = db.execute(
        select(User).where(User.clerk_id == x_clerk_user_id)
    ).scalar_one_or_none()

    if user:
        user.last_login_at = datetime.now(timezone.utc)
        user.login_count = (user.login_count or 0) + 1
        user.last_login_ip = client_ip
        user.last_login_user_agent = user_agent
        db.commit()
        return {"id": str(user.id), "synced": True, "created": False}

    # User doesn't exist — create with defaults
    new_user = User(
        clerk_id=x_clerk_user_id,
        email="",  # Will be updated by webhook
        login_count=1,
        last_login_ip=client_ip,
        last_login_user_agent=user_agent,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": str(new_user.id), "synced": True, "created": True}


# ── GET /users/terms-status ─────────────────────────────────────────────────

@router.get("/terms-status")
def get_terms_status(clerk_id: str, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.clerk_id == clerk_id)
    ).scalar_one_or_none()
    if not user:
        return {"terms_accepted": True}  # Don't block unknown users
    return {"terms_accepted": user.terms_accepted_at is not None}


# ── POST /users/accept-terms ────────────────────────────────────────────────

class AcceptTermsRequest(BaseModel):
    clerk_id: str
    terms_version: str = "1.0"
    privacy_version: str = "1.0"


@router.post("/accept-terms")
def accept_terms(body: AcceptTermsRequest, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.clerk_id == body.clerk_id)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    user.terms_accepted_at = now
    user.terms_version = body.terms_version
    user.privacy_accepted_at = now
    user.privacy_version = body.privacy_version

    # Start 14-day free trial on first terms acceptance
    if user.plan_type == "free" and user.trial_ends_at is None:
        user.trial_ends_at = now + timedelta(days=14)

    db.commit()

    # Trigger welcome email (idempotent — won't resend if already sent)
    if user.email and not user.welcome_email_sent_at:
        try:
            email_trigger(
                db=db,
                email_type=EmailType.WELCOME,
                user_id=str(user.id),
            )
        except Exception:
            logger.exception("Failed to trigger welcome email for %s", user.email)

    return {"ok": True}


# ── GET /users/prefs ────────────────────────────────────────────────────────

@router.get("/prefs")
def get_prefs(
    db: Session = Depends(get_db),
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
):
    user = _get_user_by_clerk(x_clerk_user_id, db)
    return {
        "plan_type": user.plan_type,
        "plan_status": user.plan_status,
        "pro_activation_completed_at": (
            user.pro_activation_completed_at.isoformat()
            if user.pro_activation_completed_at
            else None
        ),
        "notification_delivery_speed": user.notification_delivery_speed,
        "email_enabled": user.email_enabled,
        "sms_enabled": user.sms_enabled,
        "email_opt_out": user.email_opt_out,
    }


# ── PUT /users/prefs ────────────────────────────────────────────────────────

class UpdatePrefsRequest(BaseModel):
    notification_delivery_speed: str | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    complete_activation: bool = False


@router.put("/prefs")
def update_prefs(
    body: UpdatePrefsRequest,
    db: Session = Depends(get_db),
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
):
    user = _get_user_by_clerk(x_clerk_user_id, db)

    if body.notification_delivery_speed is not None:
        user.notification_delivery_speed = body.notification_delivery_speed
    if body.email_enabled is not None:
        user.email_enabled = body.email_enabled
    if body.sms_enabled is not None:
        user.sms_enabled = body.sms_enabled

    if body.complete_activation and user.pro_activation_completed_at is None:
        user.pro_activation_completed_at = datetime.now(timezone.utc)

    db.commit()
    return {"ok": True}


# ── DELETE /users/me ─────────────────────────────────────────────────────────

class DeleteMeRequest(BaseModel):
    reason: str | None = None
    reason_other: str | None = None


@router.delete("/me")
def delete_user(
    body: DeleteMeRequest | None = None,
    db: Session = Depends(get_db),
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
):
    user = _get_user_by_clerk(x_clerk_user_id, db)
    had_pro = user.plan_type == "pro" or user.stripe_subscription_id is not None
    result = _delete_account(
        db=db,
        user=user,
        initiated_by="user",
        reason_code=body.reason if body else None,
        reason_other=body.reason_other if body else None,
    )
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "Delete failed")

    # Trigger account deletion email via orchestrator (idempotent, logged)
    if result.ok and not result.already_deleted:
        try:
            email_type = EmailType.ACCOUNT_DELETED_PRO if had_pro else EmailType.ACCOUNT_DELETED_FREE
            email_trigger(db=db, email_type=email_type, user_id=str(user.id))
        except Exception:
            logger.exception("Failed to trigger deletion email for %s", user.email)

    return {
        "ok": True,
        "deleted": True,
        "already_deleted": result.already_deleted,
        "stripe_canceled": result.stripe_canceled,
        "email_sent": result.email_sent,
    }


# ── POST /users/cancel-subscription ─────────────────────────────────────────

@router.post("/cancel-subscription")
def cancel_subscription(
    db: Session = Depends(get_db),
    x_clerk_user_id: str = Header(..., alias="x-clerk-user-id"),
):
    user = _get_user_by_clerk(x_clerk_user_id, db)
    # Just mark locally — Stripe webhook handles the actual cancellation
    user.plan_status = "cancelled"
    db.commit()
    return {"ok": True}
