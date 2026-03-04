"""User email mode classification and transitions.

Modes:
  active  — opened or clicked in last 14 days  → instant alerts
  passive — no open/click 15–45 days, account < 90 days → weekly digest
  dormant — no open/click 45+ days, or account > 90 days with no click → re-engagement
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User

logger = logging.getLogger(__name__)

_ACTIVE_WINDOW = timedelta(days=14)
_PASSIVE_UPPER = timedelta(days=45)
_ACCOUNT_AGE_LIMIT = timedelta(days=90)


def classify_user_mode(user: User) -> str:
    """Classify a user's email mode based on engagement timestamps."""
    now = datetime.now(timezone.utc)

    last_opened = user.last_email_opened_at
    last_clicked = user.last_email_clicked_at

    # Most recent engagement event
    latest = None
    if last_opened and last_clicked:
        latest = max(last_opened, last_clicked)
    elif last_opened:
        latest = last_opened
    elif last_clicked:
        latest = last_clicked

    # No engagement data yet — default to active for new users
    if latest is None:
        account_age = now - user.created_at if user.created_at else timedelta(0)
        if account_age > _ACCOUNT_AGE_LIMIT:
            return "dormant"
        return "active"

    time_since = now - latest
    account_age = now - user.created_at if user.created_at else timedelta(0)

    # Active: engaged within 14 days
    if time_since <= _ACTIVE_WINDOW:
        return "active"

    # Dormant: no engagement for 45+ days, or old account with no click
    if time_since > _PASSIVE_UPPER:
        return "dormant"
    if account_age > _ACCOUNT_AGE_LIMIT and not last_clicked:
        return "dormant"

    # Passive: between 15–45 days without engagement
    return "passive"


def refresh_user_mode(db: Session, user: User) -> str:
    """Classify and update user.email_mode if changed. Returns new mode."""
    new_mode = classify_user_mode(user)
    if user.email_mode != new_mode:
        old_mode = user.email_mode
        user.email_mode = new_mode
        db.flush()
        logger.info(
            "User %s mode transition: %s -> %s",
            user.id, old_mode, new_mode,
        )
    return new_mode


def refresh_all_user_modes(db: Session) -> dict:
    """Batch refresh modes for users who might have changed.

    Only checks users who:
    - Are currently active but last engagement > 14 days ago
    - Are currently passive but last engagement > 45 days ago or < 14 days
    - Have no engagement timestamps set (new users becoming dormant)
    """
    now = datetime.now(timezone.utc)
    active_cutoff = now - _ACTIVE_WINDOW
    dormant_cutoff = now - _PASSIVE_UPPER

    counts = {"unchanged": 0, "active_to_passive": 0, "active_to_dormant": 0,
              "passive_to_active": 0, "passive_to_dormant": 0,
              "dormant_to_active": 0, "dormant_to_passive": 0}

    # Users who need checking: those whose mode might be stale
    candidates = db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.email_opt_out == False,  # noqa: E712
        )
    ).scalars().all()

    for user in candidates:
        old_mode = user.email_mode
        new_mode = classify_user_mode(user)
        if old_mode != new_mode:
            user.email_mode = new_mode
            key = f"{old_mode}_to_{new_mode}"
            counts[key] = counts.get(key, 0) + 1
        else:
            counts["unchanged"] += 1

    db.commit()
    transitions = {k: v for k, v in counts.items() if k != "unchanged" and v > 0}
    if transitions:
        logger.info("User mode refresh: %s (unchanged: %d)", transitions, counts["unchanged"])
    return counts
