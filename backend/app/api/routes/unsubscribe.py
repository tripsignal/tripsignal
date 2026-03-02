"""Unsubscribe / email-preferences endpoints (token-based, no auth required)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.signal import Signal
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
    """Return masked email, opt-out status, and per-signal email settings."""
    user = _get_user_from_token(token, db)

    # Fetch active signals for this user
    signals = db.execute(
        select(Signal).where(Signal.user_id == user.id, Signal.status == "active")
    ).scalars().all()

    signal_list = []
    for sig in signals:
        notif = sig.config.get("notifications", {}) if sig.config else {}
        signal_list.append({
            "id": str(sig.id),
            "name": sig.name,
            "email_enabled": notif.get("email_enabled", True),
        })

    return {
        "email": _mask_email(user.email),
        "email_opt_out": user.email_opt_out,
        "plan_type": user.plan_type,
        "notification_delivery_speed": user.notification_delivery_speed,
        "signals": signal_list,
    }


# ── POST /api/unsubscribe ──────────────────────────────────────────────────

class UnsubscribeRequest(BaseModel):
    token: str
    action: str  # "opt_out" | "pause_all" | "update_signals"
    signal_updates: Optional[list[dict]] = None  # [{"id": "...", "email_enabled": bool}]


@router.post("")
def update_preferences(body: UnsubscribeRequest, db: Session = Depends(get_db)):
    """Update email preferences based on the chosen action."""
    user = _get_user_from_token(body.token, db)

    if body.action == "opt_out":
        user.email_opt_out = True
        db.commit()
        return {"ok": True, "message": "You have been unsubscribed from all emails."}

    elif body.action == "resubscribe":
        user.email_opt_out = False
        db.commit()
        return {"ok": True, "message": "Email notifications re-enabled."}

    elif body.action == "change_speed":
        user.notification_delivery_speed = "daily"
        db.commit()
        return {"ok": True, "message": "Delivery frequency changed to daily summary."}

    elif body.action == "pause_all":
        # Disable email on every signal (reversible from dashboard)
        signals = db.execute(
            select(Signal).where(Signal.user_id == user.id, Signal.status == "active")
        ).scalars().all()
        for sig in signals:
            config = dict(sig.config) if sig.config else {}
            notif = dict(config.get("notifications", {}))
            notif["email_enabled"] = False
            config["notifications"] = notif
            sig.config = config
        db.commit()
        return {"ok": True, "message": "Email notifications paused for all signals."}

    elif body.action == "update_signals":
        if not body.signal_updates:
            raise HTTPException(status_code=400, detail="signal_updates required")
        # Update specific signals
        sig_map = {str(s.id): s for s in db.execute(
            select(Signal).where(Signal.user_id == user.id)
        ).scalars().all()}
        for update in body.signal_updates:
            sig = sig_map.get(update.get("id"))
            if not sig:
                continue
            config = dict(sig.config) if sig.config else {}
            notif = dict(config.get("notifications", {}))
            notif["email_enabled"] = bool(update.get("email_enabled", True))
            config["notifications"] = notif
            sig.config = config
        db.commit()
        return {"ok": True, "message": "Signal preferences updated."}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
