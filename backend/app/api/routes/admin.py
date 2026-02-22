from datetime import datetime, timezone
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.user import User
from app.db.models.signal import Signal
from app.db.models.signal_run import SignalRun
from app.db.models.deal import Deal

router = APIRouter(prefix="/admin", tags=["admin"])


def verify_admin(x_admin_token: str | None):
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured")
    if not x_admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


class TestEmailIn(BaseModel):
    signal_id: UUID
    match_id: UUID | None = None
    to_email: EmailStr
    subject: str
    body_text: str


@router.post("/test-email", status_code=201)
def enqueue_test_email(
    payload: TestEmailIn,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    email_enabled = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "false").lower() == "true"
    channel = "email" if email_enabled else "log"
    to_email = payload.to_email if email_enabled else "log"
    row = NotificationOutbox(
        status="pending",
        channel=channel,
        signal_id=payload.signal_id,
        match_id=payload.match_id,
        to_email=to_email,
        subject=payload.subject,
        body_text=payload.body_text,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return {"id": str(row.id), "channel": channel, "to_email": to_email}


@router.get("/debug/outbox")
def debug_outbox(
    limit: int = 20,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    limit = max(1, min(limit, 100))
    rows = db.execute(
        select(NotificationOutbox).order_by(NotificationOutbox.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "channel": r.channel,
            "status": r.status,
            "attempts": r.attempts,
            "to_email": r.to_email,
            "subject": r.subject,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "next_attempt_at": r.next_attempt_at.isoformat() if r.next_attempt_at else None,
            "last_error": r.last_error,
            "signal_id": str(r.signal_id) if r.signal_id else None,
            "match_id": str(r.match_id) if r.match_id else None,
        }
        for r in rows
    ]


@router.get("/health")
def system_health(
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    total_users = db.execute(select(func.count()).select_from(User).where(User.is_test_user == False)).scalar()
    free_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "free", User.is_test_user == False)).scalar()
    pro_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "pro", User.is_test_user == False)).scalar()
    active_signals = db.execute(select(func.count()).select_from(Signal).where(Signal.status == "active")).scalar()
    runs_24h = db.execute(
        select(func.count()).select_from(SignalRun).where(
            SignalRun.started_at > text("NOW() - INTERVAL '24 hours'")
        )
    ).scalar()

    try:
        total_deals = db.execute(select(func.count()).select_from(Deal)).scalar()
        last_scrape = db.execute(select(func.max(Deal.found_at))).scalar()
    except Exception:
        total_deals = 0
        last_scrape = None

    emails_24h = db.execute(
        select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.channel == "email",
            NotificationOutbox.created_at > text("NOW() - INTERVAL '24 hours'")
        )
    ).scalar()
    sms_24h = db.execute(
        select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.channel == "sms",
            NotificationOutbox.created_at > text("NOW() - INTERVAL '24 hours'")
        )
    ).scalar()
    failures_24h = db.execute(
        select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.status == "dead",
            NotificationOutbox.created_at > text("NOW() - INTERVAL '24 hours'")
        )
    ).scalar()
    last_signal_run = db.execute(select(func.max(SignalRun.started_at))).scalar()

    return {
        "total_users": total_users,
        "free_users": free_users,
        "pro_users": pro_users,
        "active_signals": active_signals,
        "signal_runs_24h": runs_24h,
        "total_deals": total_deals,
        "emails_24h": emails_24h,
        "sms_24h": sms_24h,
        "notification_failures_24h": failures_24h,
        "last_scrape": last_scrape.isoformat() if last_scrape else None,
        "last_signal_run": last_signal_run.isoformat() if last_signal_run else None,
    }


@router.get("/signals")
def list_signals(
    page: int = 1,
    limit: int = 25,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    rows = db.execute(
        select(Signal, User.email, User.plan_type)
        .join(User, Signal.user_id == User.id)
        .order_by(Signal.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    total = db.execute(select(func.count()).select_from(Signal)).scalar()

    results = []
    for signal, email, plan_type in rows:
        last_run = db.execute(
            select(SignalRun)
            .where(SignalRun.signal_id == signal.id)
            .order_by(SignalRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        results.append({
            "id": str(signal.id),
            "name": signal.name,
            "status": signal.status,
            "user_email": email,
            "plan": plan_type,
            "departure_airports": signal.departure_airports,
            "destination_regions": signal.destination_regions,
            "config": signal.config,
            "created_at": signal.created_at.isoformat(),
            "last_run_at": last_run.started_at.isoformat() if last_run else None,
            "last_match_count": last_run.matches_created_count if last_run else 0,
        })

    return {"signals": results, "total": total}



@router.get("/users/by-clerk-id/{clerk_id}")
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

@router.get("/users")
def list_users(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    include_test_users: bool = False,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    query = select(User)
    count_query = select(func.count()).select_from(User)
    if not include_test_users:
        query = query.where(User.is_test_user == False)
        count_query = count_query.where(User.is_test_user == False)
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
        count_query = count_query.where(User.email.ilike(f"%{search}%"))

    users = db.execute(query.order_by(User.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    total = db.execute(count_query).scalar()

    results = []
    for u in users:
        signal_count = 0
        results.append({
            "id": str(u.id),
            "email": u.email,
            "plan": u.plan_type,
            "plan_status": u.plan_status,
            "status": "active",
            "sub_status": u.stripe_subscription_status,
            "renewal_date": u.subscription_current_period_end.isoformat() if u.subscription_current_period_end else None,
            "trial_ends_at": u.trial_ends_at.isoformat() if u.trial_ends_at else None,
            "signal_count": signal_count,
            "created_at": u.created_at.isoformat(),
            "is_test_user": u.is_test_user,
        })

    return {"users": results, "total": total}

@router.patch("/users/{user_id}/toggle-test")
def toggle_test_user(
    user_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_test_user = not user.is_test_user
    db.commit()
    db.refresh(user)

    print(f"[ADMIN] toggle_test_user: {user.email} → is_test_user={user.is_test_user}")

    return {
        "id": str(user.id),
        "email": user.email,
        "is_test_user": user.is_test_user,
    }



@router.patch("/users/{user_id}/set-plan")
def set_user_plan(
    user_id: str,
    plan: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    if plan not in ("free", "pro"):
        raise HTTPException(status_code=400, detail="Invalid plan")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if plan == "free" and user.stripe_subscription_id and user.stripe_subscription_status == "active":
        raise HTTPException(
            status_code=400,
            detail="This user has an active Stripe subscription. Cancel it in Stripe first, then downgrade here."
        )
    user.plan_type = plan
    db.commit()
    db.refresh(user)
    print(f"[ADMIN] set_plan: {user.email} → plan_type={user.plan_type}")
    return {"id": str(user.id), "email": user.email, "plan_type": user.plan_type, "plan_status": user.plan_status}


@router.patch("/users/{user_id}/set-status")
def set_user_status(
    user_id: str,
    status: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="Invalid status")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.plan_status = status
    db.commit()
    db.refresh(user)
    print(f"[ADMIN] set_status: {user.email} → plan_status={user.plan_status}")
    return {"id": str(user.id), "email": user.email, "plan_type": user.plan_type, "plan_status": user.plan_status}

@router.get("/notifications")
def list_notifications(
    page: int = 1,
    limit: int = 50,
    status: str = "",
    email: str = "",
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    query = (
        select(NotificationOutbox, User.email)
        .join(Signal, NotificationOutbox.signal_id == Signal.id)
        .join(User, Signal.user_id == User.id)
    )
    count_query = (
        select(func.count())
        .select_from(NotificationOutbox)
        .join(Signal, NotificationOutbox.signal_id == Signal.id)
        .join(User, Signal.user_id == User.id)
    )

    if status:
        query = query.where(NotificationOutbox.status == status)
        count_query = count_query.where(NotificationOutbox.status == status)
    if email:
        query = query.where(User.email.ilike(f"%{email}%"))
        count_query = count_query.where(User.email.ilike(f"%{email}%"))

    rows = db.execute(
        query.order_by(NotificationOutbox.created_at.desc()).limit(limit).offset(offset)
    ).all()
    total = db.execute(count_query).scalar()

    return {
        "notifications": [
            {
                "id": str(n.id),
                "created_at": n.created_at.isoformat(),
                "user_email": user_email,
                "signal_id": str(n.signal_id) if n.signal_id else None,
                "type": n.channel,
                "status": n.status,
                "error_message": n.last_error,
                "to_email": n.to_email,
            }
            for n, user_email in rows
        ],
        "total": total,
    }
