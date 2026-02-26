docker exec -it tripsignal-api python3 -c "
content = '''\"\"\"User lookup endpoints.\"\"\"
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.db.models.signal import Signal
from app.db.session import get_db

router = APIRouter(prefix=\"/users\", tags=[\"users\"])


def _check_and_expire_trial(user: User, db: Session) -> None:
    \"\"\"If free user trial has expired, set plan_status to expired and archive their signals.\"\"\"
    if (
        user.plan_type == \"free\"
        and user.plan_status == \"active\"
        and user.trial_ends_at
        and datetime.now(timezone.utc) > user.trial_ends_at
    ):
        user.plan_status = \"expired\"
        # Archive all active signals
        signals = db.execute(
            select(Signal).where(Signal.user_id == user.id, Signal.status == \"active\")
        ).scalars().all()
        for signal in signals:
            signal.status = \"archived\"
        db.commit()


def _user_response(user: User) -> dict:
    return {
        \"id\": str(user.id),
        \"email\": user.email,
        \"clerk_id\": user.clerk_id,
        \"plan_type\": user.plan_type,
        \"plan_status\": user.plan_status,
        \"trial_ends_at\": user.trial_ends_at.isoformat() if user.trial_ends_at else None,
        \"subscription_current_period_end\": user.subscription_current_period_end.isoformat() if user.subscription_current_period_end else None,
    }


@router.get(\"/me\")
def get_me(
    x_clerk_user_id: str = Header(...),
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.clerk_id == x_clerk_user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=\"User not found\")
    _check_and_expire_trial(user, db)
    return _user_response(user)


@router.get(\"/by-clerk-id/{clerk_id}\")
def get_user_by_clerk_id(
    clerk_id: str,
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.clerk_id == clerk_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=\"User not found\")
    _check_and_expire_trial(user, db)
    return _user_response(user)


@router.post(\"/sync\")
def sync_user(
    x_clerk_user_id: str = Header(...),
    db: Session = Depends(get_db),
):
    \"\"\"Create user if not exists, called after Clerk signup.\"\"\"
    user = db.execute(select(User).where(User.clerk_id == x_clerk_user_id)).scalar_one_or_none()
    if user:
        _check_and_expire_trial(user, db)
        return {\"id\": str(user.id), \"created\": False}

    user = User(
        clerk_id=x_clerk_user_id,
        email=\"\",
        plan_type=\"free\",
        plan_status=\"active\",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {\"id\": str(user.id), \"created\": True}
'''
open('/app/backend/app/api/routes/users.py', 'w').write(content)
print('Done')
"