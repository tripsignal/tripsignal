"""User lookup and preference endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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


def _normalize_email(email: str) -> str:
    """Normalize email for dedup: strip Gmail dots, strip +aliases from all providers."""
    if not email:
        return ""
    email = email.lower().strip()
    local, _, domain = email.partition("@")
    if not domain:
        return email
    # Strip +alias
    local = local.split("+")[0]
    # Gmail-specific: dots don't matter
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
    return f"{local}@{domain}"


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
    is_signup: bool = False


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
        if x_timezone and not user.timezone and "/" in x_timezone and len(x_timezone) <= 50:
            user.timezone = x_timezone
        # Fill in email if it was missing (e.g. sync beat the webhook)
        if email and not user.email:
            user.email = email
        db.commit()
        return {"id": str(user.id), "synced": True, "created": False}

    # No row for this clerk_id — only create if this is a sign-up flow.
    # Sign-in with an unrecognized clerk_id means the user picked the wrong
    # OAuth account; creating a new user here is the root cause of duplicate
    # accounts.
    is_signup = body.is_signup if body else False
    if not is_signup:
        logger.warning(
            "SECURITY | sync_no_user_sign_in | clerk_id=%s email=%s ip=%s",
            clerk_user_id, email, client_ip,
        )
        raise HTTPException(
            status_code=404,
            detail="No account found. Please sign up first.",
        )

    # Check if same email exists
    # (handles re-created Clerk accounts with the same email)
    if email:
        existing_by_email = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing_by_email:
            # Deleted account with same email — free the email so a new
            # account can be created.  The deleted row is audit/tombstone
            # data; anonymising the email is the right thing to do anyway.
            if existing_by_email.deleted_at is not None:
                logger.warning(
                    "SECURITY | sync_clear_deleted_email | old_clerk=%s new_clerk=%s email=%s",
                    existing_by_email.clerk_id, clerk_user_id, email,
                )
                existing_by_email.email = f"deleted-{existing_by_email.id}@deleted.tripsignal.ca"
                db.commit()
                # Fall through to create a fresh user below
            else:
                # Active account with this email exists under a different clerk_id.
                # Do NOT relink — this could be an account takeover attempt.
                # Create a fresh user below without the email to avoid conflicts.
                logger.warning(
                    "SECURITY | sync_relink_blocked | old_clerk=%s new_clerk=%s email=%s ip=%s",
                    existing_by_email.clerk_id, clerk_user_id, email, client_ip,
                )
                email = ""  # clear so new user creation doesn't hit unique constraint

    # User doesn't exist — create with defaults
    normalized = _normalize_email(email)

    # Check for existing account with same normalized email (active or deleted)
    skip_trial = False
    if normalized:
        existing = db.execute(
            select(User).where(User.signup_email_normalized == normalized)
        ).scalar_one_or_none()
        if existing:
            skip_trial = True
            logger.info(
                "SECURITY | trial_denied_duplicate_email | normalized=%s | clerk_id=%s | existing_user=%s",
                normalized, clerk_user_id, existing.clerk_id,
            )

    # Check for same-IP signup within 90 days (soft flag only)
    trial_flag = None
    if client_ip and not skip_trial:
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        same_ip_user = db.execute(
            select(User).where(
                User.last_login_ip == client_ip,
                User.created_at >= ninety_days_ago,
                User.clerk_id != clerk_user_id,
                User.deleted_at.is_(None),
            )
        ).first()
        if same_ip_user:
            trial_flag = f"same_ip:{client_ip}"
            logger.info(
                "SECURITY | trial_flagged_same_ip | ip=%s | clerk_id=%s",
                client_ip, clerk_user_id,
            )

    now = datetime.now(timezone.utc)
    new_user = User(
        clerk_id=clerk_user_id,
        email=email,
        signup_email_normalized=normalized if normalized else None,
        login_count=1,
        last_login_ip=client_ip,
        last_login_user_agent=user_agent,
        timezone=x_timezone,
        trial_ends_at=None if skip_trial else now + timedelta(days=7),
        plan_status="expired" if skip_trial else "active",
        trial_flagged_reason=trial_flag,
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
        if body.sms_enabled and user.plan_type != "pro":
            raise HTTPException(status_code=400, detail="SMS alerts are only available for Pro users")
        user.sms_enabled = body.sms_enabled
    if body.notification_weekly_summary is not None:
        if body.notification_weekly_summary and user.plan_type != "pro":
            raise HTTPException(status_code=400, detail="Weekly summary is only available for Pro users")
        user.notification_weekly_summary = body.notification_weekly_summary
    if body.timezone is not None:
        tz = body.timezone.strip()[:50]
        if not tz or "/" not in tz:
            raise HTTPException(status_code=400, detail="Invalid timezone format (expected e.g. America/Toronto)")
        user.timezone = tz

    if body.complete_activation and user.plan_type == "pro" and user.pro_activation_completed_at is None:
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

    # Gather signals (cap at 100 for response size safety)
    signals = db.execute(
        select(Signal).where(Signal.user_id == user.id).limit(100)
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
@limiter.limit("3/hour")
def delete_user(
    request: Request,
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
        logger.error("Account delete failed for clerk_id=%s: %s", clerk_user_id, result.error)
        raise HTTPException(status_code=500, detail="Delete failed")

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
