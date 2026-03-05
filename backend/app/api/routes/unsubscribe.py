"""Unsubscribe / email-preferences endpoints (token-based, no auth required)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.db.session import get_db
from app.workers.selloff_scraper import validate_unsub_token

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


# ── GET /api/unsubscribe?token=xxx ──────────────────────────────────────────

@router.get("")
def get_preferences(token: str, db: Session = Depends(get_db)):
    """Return masked email, opt-out status, and preference settings."""
    user = _get_user_from_token(token, db)

    return {
        "email": _mask_email(user.email),
        "email_opt_out": user.email_opt_out,
        "email_enabled": user.email_enabled,
        "plan_type": user.plan_type,
        "notification_delivery_frequency": user.notification_delivery_frequency,
        "timezone": user.timezone,
    }


# ── POST /api/unsubscribe ──────────────────────────────────────────────────

_VALID_FREQUENCIES = {"all", "morning", "noon", "evening"}


class UnsubscribeRequest(BaseModel):
    token: str
    action: str  # "opt_out" | "resubscribe" | "change_frequency" | "update_prefs"
    email_enabled: Optional[bool] = None
    notification_delivery_frequency: Optional[str] = None


@router.post("")
def update_preferences(body: UnsubscribeRequest, db: Session = Depends(get_db)):
    """Update email preferences based on the chosen action."""
    user = _get_user_from_token(body.token, db)

    if body.action == "opt_out":
        user.email_opt_out = True
        db.commit()
        return {"ok": True, "message": "You have been unsubscribed from deal alert emails."}

    elif body.action == "resubscribe":
        user.email_opt_out = False
        db.commit()
        return {"ok": True, "message": "Deal alert emails re-enabled."}

    elif body.action in ("change_speed", "change_frequency"):
        user.notification_delivery_frequency = "morning"
        db.commit()
        return {"ok": True, "message": "Delivery changed to morning digest."}

    elif body.action == "update_prefs":
        if body.email_enabled is not None:
            user.email_enabled = body.email_enabled
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
        db.commit()
        return {"ok": True, "message": "Preferences saved."}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
