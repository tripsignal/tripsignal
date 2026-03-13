"""Unsubscribe / email-preferences endpoints (token-based, no auth required).

Security notes:
- opt_out, update_prefs, submit_feedback, pause: token-only (CASL/CAN-SPAM compliant)
- resubscribe: requires Clerk auth matching the token's user (prevents abuse of leaked tokens)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.clerk_auth import verify_clerk_token
from app.core.rate_limit import limiter
from app.core.tokens import validate_unsub_token
from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/unsubscribe", tags=["unsubscribe"])


def _mask_email(email: str) -> str:
    """Mask email for display: t***@gmail.com"""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def _get_user_from_token(token: str, db: Session) -> User:
    """Validate token and return user or raise 403."""
    user_id_str = validate_unsub_token(token)
    if not user_id_str:
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_optional_clerk_id(authorization: str | None) -> str | None:
    """Extract Clerk user ID from Authorization header if present. Returns None if missing/invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return verify_clerk_token(authorization[7:])
    except Exception:
        return None


# ── GET /api/unsubscribe?token=xxx ──────────────────────────────────────────

@router.get("")
@limiter.limit("10/minute")
def get_preferences(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    authorization: str | None = Header(None),
):
    """Return masked email, opt-out status, and preference settings.

    If an Authorization header is present, verifies the Clerk JWT and
    returns whether the signed-in user owns this token (is_own_account).
    No user identifiers are exposed in the response.
    """
    user = _get_user_from_token(token, db)

    # Determine account ownership without exposing any identifiers
    is_own_account = None
    caller_clerk_id = _get_optional_clerk_id(authorization)
    if caller_clerk_id is not None:
        is_own_account = caller_clerk_id == user.clerk_id

    return {
        "email": _mask_email(user.email),
        "email_opt_out": user.email_opt_out,
        "email_enabled": user.email_enabled,
        "plan_type": user.plan_type,
        "notification_delivery_frequency": user.notification_delivery_frequency,
        "notification_weekly_summary": user.notification_weekly_summary,
        "is_own_account": is_own_account,
    }


# ── POST /api/unsubscribe ──────────────────────────────────────────────────

_VALID_FREQUENCIES = {"all", "morning", "noon", "evening"}


class UnsubscribeRequest(BaseModel):
    token: str
    action: str  # "opt_out" | "submit_feedback" | "resubscribe" | "update_prefs" | "pause"
    email_enabled: Optional[bool] = None
    notification_delivery_frequency: Optional[str] = None
    notification_weekly_summary: Optional[bool] = None
    reason: Optional[str] = None  # feedback reason on opt_out (informational)


@router.post("")
@limiter.limit("10/minute")
def update_preferences(
    request: Request,
    body: UnsubscribeRequest,
    db: Session = Depends(get_db),
    authorization: str | None = Header(None),
):
    """Update email preferences based on the chosen action."""
    user = _get_user_from_token(body.token, db)

    if body.action == "opt_out":
        user.email_opt_out = True
        db.commit()
        logger.info("unsub_action=opt_out | user=%s | email=%s", user.id, _mask_email(user.email))
        return {"ok": True, "message": "You have been unsubscribed from deal alert emails."}

    elif body.action == "submit_feedback":
        if body.reason:
            safe_reason = body.reason[:200].replace("\n", " ").replace("\r", "")
            user.unsubscribe_reason = safe_reason
            db.commit()
            logger.info("unsub_action=feedback | user=%s | reason=%s", user.id, safe_reason)
        return {"ok": True, "message": "Thank you for your feedback."}

    elif body.action == "pause":
        user.email_enabled = False
        user.notification_weekly_summary = False
        db.commit()
        logger.info("unsub_action=pause | user=%s | email=%s", user.id, _mask_email(user.email))
        return {"ok": True, "message": "Deal emails paused."}

    elif body.action == "resubscribe":
        # Resubscribe requires Clerk auth matching the token's user.
        # This prevents leaked tokens from being used to re-enable
        # emails for users who explicitly opted out.
        caller_clerk_id = _get_optional_clerk_id(authorization)
        if not caller_clerk_id:
            logger.warning(
                "unsub_action=resubscribe_denied | user=%s | reason=no_auth",
                user.id,
            )
            raise HTTPException(
                status_code=401,
                detail="Sign in to re-enable deal alerts.",
            )
        if caller_clerk_id != user.clerk_id:
            logger.warning(
                "unsub_action=resubscribe_denied | user=%s | reason=clerk_mismatch | caller=%s",
                user.id,
                caller_clerk_id,
            )
            raise HTTPException(
                status_code=403,
                detail="You can only re-enable alerts for your own account.",
            )
        user.email_opt_out = False
        user.email_enabled = True
        db.commit()
        logger.info("unsub_action=resubscribe | user=%s | email=%s", user.id, _mask_email(user.email))
        return {"ok": True, "message": "Deal alert emails re-enabled."}

    elif body.action in ("change_speed", "change_frequency"):
        user.notification_delivery_frequency = "morning"
        db.commit()
        logger.info("unsub_action=change_frequency | user=%s", user.id)
        return {"ok": True, "message": "Delivery changed to morning digest."}

    elif body.action == "update_prefs":
        changes = []
        if body.email_enabled is not None:
            user.email_enabled = body.email_enabled
            changes.append(f"email_enabled={body.email_enabled}")
        if body.notification_delivery_frequency is not None:
            windows = [w.strip() for w in body.notification_delivery_frequency.split(",")]
            if not all(w in _VALID_FREQUENCIES for w in windows):
                raise HTTPException(
                    status_code=400,
                    detail=f"frequency values must be: {', '.join(sorted(_VALID_FREQUENCIES))}",
                )
            if "all" in windows and len(windows) > 1:
                raise HTTPException(status_code=400, detail="'all' cannot be combined with other windows")
            user.notification_delivery_frequency = body.notification_delivery_frequency
            changes.append(f"frequency={body.notification_delivery_frequency}")
        if body.notification_weekly_summary is not None:
            user.notification_weekly_summary = body.notification_weekly_summary
            changes.append(f"weekly_summary={body.notification_weekly_summary}")
        db.commit()
        logger.info("unsub_action=update_prefs | user=%s | changes=%s", user.id, ",".join(changes))
        return {"ok": True, "message": "Preferences saved."}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
