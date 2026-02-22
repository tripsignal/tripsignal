"""User lookup endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/by-clerk-id/{clerk_id}")
def get_user_by_clerk_id(
    clerk_id: str,
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.clerk_id == clerk_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(user.id),
        "email": user.email,
        "clerk_id": user.clerk_id,
        "plan_type": user.plan_type,
        "plan_status": user.plan_status,
        "trial_ends_at": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        "subscription_current_period_end": user.subscription_current_period_end.isoformat() if user.subscription_current_period_end else None,
    }
