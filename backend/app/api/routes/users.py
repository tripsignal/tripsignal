"""User lookup and preference endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.core.email_validation import is_valid_email
from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db
from app.services.account import delete_account as _delete_account

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
def get_user_by_clerk_id(
    clerk_id: str,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    if clerk_user_id != clerk_id:
        raise HTTPException(status_code=403, detail="Forbidden")
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
        "email_enabled": user.email_enabled,
        "notification_delivery_frequency": user.notification_delivery_frequency,
        "notification_weekly_summary": user.notification_weekly_summary,
        "display_name": user.display_name,
        "name_prompt_dismissed": user.name_prompt_dismissed,
    }


# ── POST /users/sync ────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    email: str = ""


@router.post("/sync")
@limiter.limit("30/minute")
def sync_user(
    request: Request,
    body: SyncRequest | None = None,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
    x_forwarded_for: str | None = None,
    user_agent: str | None = None,
    x_timezone: str | None = None,
):
    """Ensure user row exists for the given Clerk ID. Called on sign-in."""
    # Extract headers that aren't part of auth
    x_forwarded_for = x_forwarded_for or request.headers.get("x-forwarded-for")
    user_agent = user_agent or request.headers.get("user-agent")
    x_timezone = x_timezone or request.headers.get("x-timezone")

    # Rightmost IP is the one Caddy appended from the TCP connection
    client_ip = x_forwarded_for.split(",")[-1].strip() if x_forwarded_for else None

    email = (body.email or "").strip() if body else ""
    if email and not is_valid_email(email):
        logger.warning("Sync: rejecting invalid email %s for clerk_id %s", email, clerk_user_id)
        email = ""

    user = db.execute(
        select(User).where(User.clerk_id == clerk_user_id)
    ).scalar_one_or_none()

    if user:
        user.last_login_at = datetime.now(timezone.utc)
        user.login_count = (user.login_count or 0) + 1
        user.last_login_ip = client_ip
        user.last_login_user_agent = user_agent
        # Auto-set timezone from browser if user hasn't manually chosen one
        if x_timezone and not user.timezone:
            user.timezone = x_timezone
        # Fill in email if it was missing (e.g. sync beat the webhook)
        if email and not user.email:
            user.email = email
        db.commit()
        return {"id": str(user.id), "synced": True, "created": False}

    # User doesn't exist — create with defaults
    new_user = User(
        clerk_id=clerk_user_id,
        email=email,
        login_count=1,
        last_login_ip=client_ip,
        last_login_user_agent=user_agent,
        timezone=x_timezone,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": str(new_user.id), "synced": True, "created": True}


# ── GET /users/prefs ────────────────────────────────────────────────────────

@router.get("/prefs")
def get_prefs(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)
    return {
        "plan_type": user.plan_type,
        "plan_status": user.plan_status,
        "pro_activation_completed_at": (
            user.pro_activation_completed_at.isoformat()
            if user.pro_activation_completed_at
            else None
        ),
        "notification_delivery_frequency": user.notification_delivery_frequency,
        "email_enabled": user.email_enabled,
        "sms_enabled": user.sms_enabled,
        "email_opt_out": user.email_opt_out,
        "notification_weekly_summary": user.notification_weekly_summary,
        "timezone": user.timezone,
    }


# ── PUT /users/prefs ────────────────────────────────────────────────────────

class UpdateDisplayNameRequest(BaseModel):
    display_name: str


class UpdatePrefsRequest(BaseModel):
    notification_delivery_frequency: str | None = None
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    notification_weekly_summary: bool | None = None
    timezone: str | None = None
    complete_activation: bool = False


_VALID_FREQUENCIES = {"all", "morning", "noon", "evening"}


@router.put("/prefs")
def update_prefs(
    body: UpdatePrefsRequest,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)

    if body.notification_delivery_frequency is not None:
        windows = [w.strip() for w in body.notification_delivery_frequency.split(",")]
        if not windows:
            raise HTTPException(status_code=400, detail="At least one frequency window is required")
        if not all(w in _VALID_FREQUENCIES for w in windows):
            raise HTTPException(
                status_code=400,
                detail=f"frequency values must be: {', '.join(sorted(_VALID_FREQUENCIES))}",
            )
        if "all" in windows and len(windows) > 1:
            raise HTTPException(status_code=400, detail="'all' cannot be combined with other windows")
        user.notification_delivery_frequency = body.notification_delivery_frequency
    if body.email_enabled is not None:
        user.email_enabled = body.email_enabled
    if body.sms_enabled is not None:
        user.sms_enabled = body.sms_enabled
    if body.notification_weekly_summary is not None:
        if body.notification_weekly_summary and user.plan_type != "pro":
            raise HTTPException(status_code=400, detail="Weekly summary is only available for Pro users")
        user.notification_weekly_summary = body.notification_weekly_summary
    if body.timezone is not None:
        user.timezone = body.timezone

    if body.complete_activation and user.pro_activation_completed_at is None:
        user.pro_activation_completed_at = datetime.now(timezone.utc)

    db.commit()
    return {"ok": True}


# ── PUT /users/display-name ─────────────────────────────────────────────────

@router.put("/display-name")
def update_display_name(
    body: UpdateDisplayNameRequest,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)
    name = body.display_name.strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="Display name cannot be empty")

    from app.services.email_templates.base import _name_is_clean
    if not _name_is_clean(name):
        raise HTTPException(
            status_code=400,
            detail="Easy there. Let's keep it friendly — try your actual name.",
        )

    user.display_name = name
    db.commit()
    return {"ok": True, "display_name": user.display_name}


# ── POST /users/dismiss-name-prompt ────────────────────────────────────────

@router.post("/dismiss-name-prompt")
def dismiss_name_prompt(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)
    user.name_prompt_dismissed = True
    db.commit()
    return {"ok": True}


# ── GET /users/me/export ─────────────────────────────────────────────────────

@router.get("/me/export")
@limiter.limit("5/hour")
def export_my_data(
    request: Request,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Export all user data as JSON (PIPEDA data portability)."""
    user = _get_user_by_clerk(clerk_user_id, db)

    # Gather signals
    signals = db.execute(
        select(Signal).where(Signal.user_id == user.id)
    ).scalars().all()

    signals_data = []
    for sig in signals:
        # Gather deal matches for this signal
        matches = db.execute(
            select(DealMatch, Deal)
            .join(Deal, DealMatch.deal_id == Deal.id)
            .where(DealMatch.signal_id == sig.id)
            .order_by(DealMatch.matched_at.desc())
            .limit(500)
        ).all()

        matches_data = [
            {
                "matched_at": dm.matched_at.isoformat() if dm.matched_at else None,
                "deal": {
                    "provider": deal.provider,
                    "origin": deal.origin,
                    "destination": deal.destination,
                    "hotel_name": deal.hotel_name,
                    "depart_date": str(deal.depart_date) if deal.depart_date else None,
                    "return_date": str(deal.return_date) if deal.return_date else None,
                    "price_cents": deal.price_cents,
                    "currency": deal.currency,
                    "star_rating": deal.star_rating,
                },
            }
            for dm, deal in matches
        ]

        signals_data.append({
            "id": str(sig.id),
            "name": sig.name,
            "status": sig.status,
            "departure_airports": sig.departure_airports,
            "destination_regions": sig.destination_regions,
            "config": sig.config,
            "created_at": sig.created_at.isoformat() if sig.created_at else None,
            "deal_matches": matches_data,
        })

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "plan_type": user.plan_type,
            "plan_status": user.plan_status,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "timezone": user.timezone,
            "notification_preferences": {
                "email_enabled": user.email_enabled,
                "delivery_frequency": user.notification_delivery_frequency,
                "weekly_summary": user.notification_weekly_summary,
            },
            "email_opt_out": user.email_opt_out,
            "unsubscribe_reason": user.unsubscribe_reason,
        },
        "signals": signals_data,
    }


# ── DELETE /users/me ─────────────────────────────────────────────────────────

class DeleteMeRequest(BaseModel):
    reason: str | None = None
    reason_other: str | None = None


@router.delete("/me")
def delete_user(
    body: DeleteMeRequest | None = None,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)
    result = _delete_account(
        db=db,
        user=user,
        initiated_by="user",
        reason_code=body.reason if body else None,
        reason_other=body.reason_other if body else None,
    )
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "Delete failed")

    logger.info(
        "SECURITY | account_deleted | clerk_id=%s | email=%s | reason=%s",
        clerk_user_id,
        user.email,
        body.reason if body else "none",
    )

    # Email sending is now handled inside delete_account() (between phase 1 and phase 2)
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
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    user = _get_user_by_clerk(clerk_user_id, db)
    # Just mark locally — Stripe webhook handles the actual cancellation
    user.plan_status = "cancelled"
    db.commit()
    return {"ok": True}
