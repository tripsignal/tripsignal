import logging
import os
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import verify_admin
from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.hotel_link import HotelLink
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.scrape_run import ScrapeRun
from app.db.models.signal import Signal
from app.db.models.signal_run import SignalRun
from app.db.models.user import User
from app.db.session import get_db
from app.services.account import delete_account, restore_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])



class TestEmailIn(BaseModel):
    signal_id: UUID
    match_id: UUID | None = None
    to_email: EmailStr
    subject: str
    body_text: str


@router.post("/test-email", status_code=201)
@limiter.limit("10/minute")
def enqueue_test_email(
    request: Request,
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

    total_users = db.execute(select(func.count()).select_from(User).where(not User.is_test_user)).scalar()
    free_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "free", not User.is_test_user)).scalar()
    pro_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "pro", not User.is_test_user)).scalar()
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

    hotels_missing_review_url = db.execute(
        select(func.count()).select_from(HotelLink).where(
            HotelLink.tripadvisor_url == None  # noqa: E711
        )
    ).scalar()

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
        "hotels_missing_review_url": hotels_missing_review_url,
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
        query = query.where(not User.is_test_user)
        count_query = count_query.where(not User.is_test_user)
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


# ── DELETE /admin/users/{user_id} ──────────────────────────────────────
class DeleteUserRequest(BaseModel):
    reason: str | None = None
    reason_other: str | None = None


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: str,
    body: DeleteUserRequest | None = None,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    reason_code = body.reason if body else None
    reason_other = body.reason_other if body else None

    result = delete_account(
        db=db,
        user=user,
        initiated_by="admin",
        reason_code=reason_code,
        reason_other=reason_other,
    )

    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "Delete failed")

    return {
        "ok": True,
        "already_deleted": result.already_deleted,
        "stripe_canceled": result.stripe_canceled,
        "email_sent": result.email_sent,
    }


# ── POST /admin/users/{user_id}/undelete ──────────────────────────────
@router.post("/users/{user_id}/undelete")
def admin_undelete_user(
    user_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = restore_account(db=db, user=user)

    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "Restore failed")

    return {
        "ok": True,
        "not_deleted": result.not_deleted,
        "email": user.email,
        "plan_status": user.plan_status,
        "plan_type": user.plan_type,
    }


# ── DELETE /admin/users/{user_id}/hard-delete ─────────────────────────
@router.delete("/users/{user_id}/hard-delete")
def admin_hard_delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Permanently remove a soft-deleted user and all associated data.

    CASCADE FKs handle: signals → deal_matches, signal_runs.
    SET NULL FKs handle: email_log.user_id, notifications_outbox.signal_id.
    """
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.deleted_at is None:
        raise HTTPException(
            status_code=400,
            detail="User must be soft-deleted first. Use DELETE /admin/users/{id}.",
        )

    try:
        db.delete(user)
        db.commit()
        logger.info("[ADMIN] hard_delete: user %s permanently removed", user_id)
    except Exception as e:
        db.rollback()
        logger.error("Hard delete failed for %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail=f"Hard delete failed: {e}")

    return {"ok": True, "hard_deleted": True, "user_id": user_id}


# ── PATCH /admin/users/{user_id}/extend-trial ──────────────────────────
@router.patch("/users/{user_id}/extend-trial")
def extend_trial(
    user_id: str,
    days: int = 7,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="Days must be 1-90")

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    base = user.trial_ends_at if user.trial_ends_at and user.trial_ends_at > now else now
    user.trial_ends_at = base + timedelta(days=days)
    if user.plan_status in ("expired", "deleted"):
        user.plan_status = "active"
    db.commit()
    db.refresh(user)

    logger.info("[ADMIN] extend_trial: %s → +%d days (ends %s)", user.email, days, user.trial_ends_at)
    return {
        "id": str(user.id),
        "email": user.email,
        "trial_ends_at": user.trial_ends_at.isoformat(),
        "plan_status": user.plan_status,
    }


# ── PATCH /admin/users/{user_id}/reset-trial ───────────────────────────
@router.patch("/users/{user_id}/reset-trial")
def reset_trial(
    user_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
    if user.plan_status in ("expired", "deleted"):
        user.plan_status = "active"
    db.commit()
    db.refresh(user)

    logger.info("[ADMIN] reset_trial: %s → 14 days (ends %s)", user.email, user.trial_ends_at)
    return {
        "id": str(user.id),
        "email": user.email,
        "trial_ends_at": user.trial_ends_at.isoformat(),
        "plan_status": user.plan_status,
    }


# ── GET /admin/users/{user_id}/feedback ────────────────────────────────
@router.get("/users/{user_id}/feedback")
def get_user_feedback(
    user_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": str(user.id),
        "email": user.email,
        "deleted_at": user.deleted_at.isoformat() if user.deleted_at else None,
        "deleted_by": user.deleted_by,
        "deleted_reason": user.deleted_reason,
        "deleted_reason_other": user.deleted_reason_other,
    }


# ── POST /admin/run-trial-expiry ──────────────────────────────────────
@router.post("/run-trial-expiry")
def run_trial_expiry(
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Find users whose trial has expired and send them an upsell email.
    Safe to call repeatedly — each user only gets one email (guarded by
    trial_expired_email_sent_at).
    """

    verify_admin(x_admin_token)
    now = datetime.now(timezone.utc)

    # Users with expired trials who:
    # - are on free plan
    # - are active (not deleted)
    # - have never received the trial-expired email
    expired_users = db.execute(
        select(User).where(
            User.trial_ends_at < now,
            User.plan_type == "free",
            User.deleted_at.is_(None),
            User.trial_expired_email_sent_at.is_(None),
            not User.email_opt_out,
            User.email != "",
        )
    ).scalars().all()

    sent = 0
    failed = 0
    for user in expired_users:
        from app.services.email_orchestrator import EmailType
        from app.services.email_orchestrator import trigger as email_trigger
        result = email_trigger(
            db=db,
            email_type=EmailType.TRIAL_EXPIRED_UPSELL,
            user_id=str(user.id),
            context={"period": now.strftime("%Y-%m-%d")},
        )
        ok = result.get("status") in ("sent", "dry_run")
        if ok:
            user.trial_expired_email_sent_at = now
            sent += 1
        else:
            failed += 1

    db.commit()
    logger.info("[ADMIN] run_trial_expiry: sent=%d failed=%d total=%d", sent, failed, len(expired_users))
    return {"ok": True, "sent": sent, "failed": failed, "total": len(expired_users)}


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

    )
    count_query = (
        select(func.count())
        .select_from(NotificationOutbox)
        .join(Signal, NotificationOutbox.signal_id == Signal.id)

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
                "opened_at": n.opened_at.isoformat() if n.opened_at else None,
                "open_count": n.open_count or 0,
                "subject": n.subject,
                "body_text": n.body_text,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            }
            for n, user_email in rows
        ],
        "total": total,
    }


@router.get("/scrape-runs")
def list_scrape_runs(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    total = db.execute(select(func.count()).select_from(ScrapeRun)).scalar()

    runs = db.execute(
        select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    results = []
    prev_total = None
    for run in reversed(runs):
        delta = (run.total_deals - prev_total) if prev_total is not None else None
        prev_total = run.total_deals

        duration_sec = None
        if run.completed_at and run.started_at:
            duration_sec = int((run.completed_at - run.started_at).total_seconds())

        new_deals = db.execute(
            select(func.count()).select_from(Deal).where(
                Deal.found_at >= run.started_at,
                Deal.found_at < (run.completed_at if run.completed_at else text("NOW()")),
            )
        ).scalar()

        results.append({
            "id": run.id,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "total_deals": run.total_deals,
            "total_matches": run.total_matches,
            "error_count": run.error_count,
            "error_log": run.error_log,
            "deals_deactivated": run.deals_deactivated,
            "status": run.status,
            "duration_sec": duration_sec,
            "deal_delta": delta,
            "new_deals": new_deals,
            "proxy_ip": run.proxy_ip,
            "proxy_geo": run.proxy_geo,
        })

    results.reverse()
    return {"runs": results, "total": total}


@router.get("/deals")
def list_deals(
    page: int = 1,
    limit: int = 50,
    scrape_run_id: int | None = None,
    view: str = "active",
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    if view == "new" and scrape_run_id:
        run = db.execute(
            select(ScrapeRun).where(ScrapeRun.id == scrape_run_id)
        ).scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Scrape run not found")

        end = run.completed_at if run.completed_at else datetime.now(timezone.utc)
        query = select(Deal).where(
            Deal.found_at >= run.started_at,
            Deal.found_at < end,
        )
        count_query = select(func.count()).select_from(Deal).where(
            Deal.found_at >= run.started_at,
            Deal.found_at < end,
        )
    elif view == "removed":
        query = select(Deal).where(
            not Deal.is_active,
            Deal.deactivated_at.isnot(None),
        )
        count_query = select(func.count()).select_from(Deal).where(
            not Deal.is_active,
            Deal.deactivated_at.isnot(None),
        )
    else:
        query = select(Deal).where(Deal.is_active)
        count_query = select(func.count()).select_from(Deal).where(Deal.is_active)

    order = Deal.deactivated_at.desc() if view == "removed" else Deal.price_cents.asc()
    deals = db.execute(
        query.order_by(order).limit(limit).offset(offset)
    ).scalars().all()
    total = db.execute(count_query).scalar()

    summary = db.execute(
        select(
            func.count().label("count"),
            func.count(func.distinct(Deal.origin)).label("origins"),
            func.count(func.distinct(Deal.destination)).label("destinations"),
            func.avg(Deal.discount_pct).label("avg_discount"),
            func.min(Deal.price_cents).label("min_price"),
        ).select_from(Deal).where(Deal.is_active)
    ).one()

    expired_active_count = db.execute(
        select(func.count()).select_from(Deal).where(
            Deal.is_active,
            Deal.depart_date < date_type.today(),
        )
    ).scalar()

    return {
        "deals": [
            {
                "id": str(d.id),
                "origin": d.origin,
                "destination": d.destination,
                "destination_str": d.destination_str,
                "hotel_name": d.hotel_name,
                "star_rating": float(d.star_rating) if d.star_rating else None,
                "depart_date": d.depart_date.isoformat(),
                "return_date": d.return_date.isoformat() if d.return_date else None,
                "price_cents": d.price_cents,
                "discount_pct": d.discount_pct,
                "is_active": d.is_active,
                "found_at": d.found_at.isoformat(),
                "deactivated_at": d.deactivated_at.isoformat() if d.deactivated_at else None,
                "deeplink_url": d.deeplink_url,
                "dedupe_key": d.dedupe_key,
            }
            for d in deals
        ],
        "total": total,
        "summary": {
            "total_active": summary.count,
            "unique_origins": summary.origins,
            "unique_destinations": summary.destinations,
            "avg_discount": round(float(summary.avg_discount), 1) if summary.avg_discount else 0,
            "min_price_cents": summary.min_price,
            "expired_active_count": expired_active_count,
        },
    }


@router.get("/users-unified")
def users_unified(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    include_test_users: bool = False,
    status_filter: str = "",
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    query = select(User)
    count_query = select(func.count()).select_from(User)

    # New status_filter takes priority over include_test_users
    if status_filter == "active":
        query = query.where(User.deleted_at.is_(None), not User.is_test_user)
        count_query = count_query.where(User.deleted_at.is_(None), not User.is_test_user)
    elif status_filter == "deleted":
        query = query.where(User.deleted_at.isnot(None))
        count_query = count_query.where(User.deleted_at.isnot(None))
    elif status_filter == "test":
        query = query.where(User.is_test_user)
        count_query = count_query.where(User.is_test_user)
    elif status_filter == "all":
        pass  # No filtering — show everyone
    elif not include_test_users:
        # Legacy: exclude test users and deleted users by default
        query = query.where(not User.is_test_user, User.deleted_at.is_(None))
        count_query = count_query.where(not User.is_test_user, User.deleted_at.is_(None))
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
        count_query = count_query.where(User.email.ilike(f"%{search}%"))

    users = db.execute(query.order_by(User.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    total = db.execute(count_query).scalar()

    results = []
    for u in users:
        # Signals for this user
        signals = db.execute(
            select(Signal).where(Signal.user_id == u.id).order_by(Signal.created_at.desc())
        ).scalars().all()

        signal_list = []
        for sig in signals:
            last_run = db.execute(
                select(SignalRun)
                .where(SignalRun.signal_id == sig.id)
                .order_by(SignalRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            dep_airports = sig.departure_airports or []
            dest_regions = sig.destination_regions or []
            config = sig.config or {}

            signal_list.append({
                "id": str(sig.id),
                "name": sig.name,
                "status": sig.status,
                "departure_airports": dep_airports,
                "destination_regions": dest_regions,
                "config": config,
                "last_run_at": last_run.started_at.isoformat() if last_run else None,
                "created_at": sig.created_at.isoformat(),
            })

        # Notifications for this user (by email match, most recent 50)
        notifs = db.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.to_email == u.email)
            .order_by(NotificationOutbox.created_at.desc())
            .limit(50)
        ).scalars().all()

        notification_count = db.execute(
            select(func.count()).select_from(NotificationOutbox)
            .where(NotificationOutbox.to_email == u.email)
        ).scalar()

        notification_list = []
        for n in notifs:
            notification_list.append({
                "id": str(n.id),
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "type": n.channel,
                "status": n.status,
                "opened_at": n.opened_at.isoformat() if n.opened_at else None,
                "open_count": n.open_count or 0,
                "error": n.last_error,
                "subject": n.subject,
            })

        # Last email opened
        last_open = db.execute(
            select(func.max(NotificationOutbox.opened_at))
            .where(NotificationOutbox.to_email == u.email)
        ).scalar()

        results.append({
            "id": str(u.id),
            "email": u.email,
            "plan": u.plan_type,
            "plan_status": u.plan_status,
            "sub_status": u.stripe_subscription_status,
            "renewal_date": u.subscription_current_period_end.isoformat() if u.subscription_current_period_end else None,
            "trial_ends_at": u.trial_ends_at.isoformat() if u.trial_ends_at else None,
            "created_at": u.created_at.isoformat(),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "login_count": u.login_count or 0,
            "last_login_ip": u.last_login_ip,
            "last_login_user_agent": u.last_login_user_agent,
            "is_test_user": u.is_test_user,
            "signal_count": len(signals),
            "signals": signal_list,
            "notification_count": notification_count,
            "notifications": notification_list,
            "last_email_opened_at": last_open.isoformat() if last_open else None,
            "notification_delivery_frequency": u.notification_delivery_frequency,
            "email_enabled": u.email_enabled,
            "email_opt_out": u.email_opt_out,
            "timezone": u.timezone,
            "email_mode": u.email_mode,
            # Soft-delete fields
            "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
            "deleted_by": u.deleted_by,
            "deleted_reason": u.deleted_reason,
            "deleted_reason_other": u.deleted_reason_other,
        })

    return {"users": results, "total": total}


@router.get("/hotels")
def list_hotels(
    search: str = "",
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """List all hotels with their TripAdvisor links."""
    verify_admin(x_admin_token)

    # Sync any new hotels from deals that aren't in hotel_links yet
    # Use the earliest found_at from deals as created_at (first time we ever saw this hotel)
    db.execute(text("""
        INSERT INTO hotel_links (hotel_id, hotel_name, destination, star_rating, created_at)
        SELECT d.hotel_id, d.hotel_name, d.destination_str, d.star_rating, earliest.first_seen
        FROM (
            SELECT DISTINCT ON (hotel_id) hotel_id, hotel_name, destination_str, star_rating
            FROM deals
            WHERE is_active = true AND hotel_id IS NOT NULL
              AND hotel_id NOT IN (SELECT hotel_id FROM hotel_links)
            ORDER BY hotel_id, found_at DESC
        ) d
        JOIN (
            SELECT hotel_id, MIN(found_at) AS first_seen
            FROM deals
            WHERE hotel_id IS NOT NULL
            GROUP BY hotel_id
        ) earliest ON d.hotel_id = earliest.hotel_id
    """))
    db.commit()

    query = select(HotelLink).order_by(HotelLink.hotel_name)
    if search:
        query = query.where(HotelLink.hotel_name.ilike(f"%{search}%"))

    rows = db.execute(query).scalars().all()

    # Count active deals per hotel
    deal_counts = dict(db.execute(
        select(Deal.hotel_id, func.count())
        .where(Deal.is_active)
        .group_by(Deal.hotel_id)
    ).all())

    return {
        "hotels": [
            {
                "hotel_id": h.hotel_id,
                "hotel_name": h.hotel_name,
                "destination": h.destination,
                "star_rating": float(h.star_rating) if h.star_rating else None,
                "tripadvisor_url": h.tripadvisor_url,
                "active_deals": deal_counts.get(h.hotel_id, 0),
                "created_at": h.created_at.isoformat() if h.created_at else None,
                "updated_at": h.updated_at.isoformat() if h.updated_at else None,
            }
            for h in rows
        ],
        "total": len(rows),
    }


@router.put("/hotels/{hotel_id}")
def update_hotel_link(
    hotel_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Update the TripAdvisor URL for a hotel."""
    verify_admin(x_admin_token)

    hotel = db.execute(
        select(HotelLink).where(HotelLink.hotel_id == hotel_id)
    ).scalar_one_or_none()

    if not hotel:
        raise HTTPException(status_code=404, detail="Hotel not found")

    url = (payload.get("tripadvisor_url") or "").strip()
    hotel.tripadvisor_url = url if url else None
    hotel.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True, "hotel_id": hotel_id, "tripadvisor_url": hotel.tripadvisor_url}


# ── Email Testing ────────────────────────────────────────────────────────────

class SendTestEmailIn(BaseModel):
    email_type: str
    to_email: EmailStr


@router.get("/email-types")
def list_email_types(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Return all available email types with their categories."""
    verify_admin(x_admin_token)
    from app.services.email_orchestrator import EMAIL_TYPE_CATEGORY, EmailType

    result = []
    for et in EmailType:
        cat = EMAIL_TYPE_CATEGORY.get(et)
        result.append({
            "value": et.value,
            "name": et.name,
            "category": cat.value if cat else "unknown",
        })
    return {"email_types": result}


@router.post("/send-test-email")
def send_test_email(
    payload: SendTestEmailIn,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Render a template with a fake user and send it to the provided address."""
    verify_admin(x_admin_token)
    from app.services.email import send_email
    from app.services.email_orchestrator import EmailType
    from app.services.email_templates import render_template

    # Validate email type
    try:
        email_type = EmailType(payload.email_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown email type: {payload.email_type}")

    # Try to find a real user by email for realistic test rendering
    real_user = db.execute(
        select(User).where(User.email == payload.to_email, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if real_user:
        fake_user = real_user
    else:
        class _FakeUser:
            id = "00000000-0000-0000-0000-000000000000"
            email = payload.to_email
            plan_type = "pro"
            plan_status = "active"
            clerk_id = "test_clerk_admin"
        fake_user = _FakeUser()

    # Build sample context per email type
    context = _sample_context(email_type)

    # Generate a real unsubscribe token if possible
    try:
        from app.core.tokens import generate_unsub_token
        token = generate_unsub_token(str(fake_user.id))
        context["_unsub_url"] = f"https://tripsignal.ca/unsubscribe?token={token}"
    except Exception:
        context["_unsub_url"] = ""

    try:
        subject, html = render_template(email_type, user=fake_user, context=context, db=db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template render failed: {e}")

    subject = f"[TEST] {subject}"

    sent = send_email(payload.to_email, subject, html)

    return {
        "ok": sent,
        "email_type": email_type.value,
        "to_email": payload.to_email,
        "subject": subject,
        "error": None if sent else "send_email failed",
    }


@router.post("/preview-email")
def preview_email(
    payload: SendTestEmailIn,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Render a template and return the HTML without sending."""
    verify_admin(x_admin_token)
    from app.services.email_orchestrator import EmailType
    from app.services.email_templates import render_template

    try:
        email_type = EmailType(payload.email_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown email type: {payload.email_type}")

    real_user = db.execute(
        select(User).where(User.email == payload.to_email, User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if real_user:
        preview_user = real_user
    else:
        class _FakeUser:
            id = "00000000-0000-0000-0000-000000000000"
            email = payload.to_email
            plan_type = "pro"
            plan_status = "active"
            clerk_id = "test_clerk_admin"
        preview_user = _FakeUser()

    context = _sample_context(email_type)

    try:
        from app.core.tokens import generate_unsub_token
        token = generate_unsub_token(str(preview_user.id))
        context["_unsub_url"] = f"https://tripsignal.ca/unsubscribe?token={token}"
    except Exception:
        context["_unsub_url"] = ""

    try:
        subject, html = render_template(email_type, user=preview_user, context=context, db=db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Template render failed: {e}")

    return {"subject": f"[TEST] {subject}", "html": html}


def _sample_context(email_type) -> dict:
    """Return realistic sample context data for template preview/test."""
    from app.services.email_orchestrator import EmailType

    samples = {
        EmailType.WELCOME: {},
        EmailType.FIRST_SIGNAL: {"signal_name": "Mexico Beach Getaway", "signal_id": "test-signal-123"},
        EmailType.NO_SIGNAL_REMINDER: {},
        EmailType.MATCH_ALERT: {"signal_name": "Caribbean Winter Escape", "match_count": 3, "best_price": "$899"},
        EmailType.MAJOR_DROP_ALERT: {
            "signal_name": "Caribbean Winter Escape",
            "drop_amount": "$250",
            "hotel_name": "Riu Palace Riviera Maya",
            "new_price": "$749",
        },
        EmailType.TRIAL_EXPIRING_SOON: {"days_left": 3},
        EmailType.TRIAL_EXPIRED_UPSELL: {},
        EmailType.PRO_ACTIVATED: {},
        EmailType.PAYMENT_FAILED: {"invoice_id": "inv_test_123"},
        EmailType.PAYMENT_FAILED_REMINDER: {"reminder_num": 1, "invoice_id": "inv_test_123"},
        EmailType.SUBSCRIPTION_CANCELED: {"period_end": "March 15, 2026", "subscription_id": "sub_test_123"},
        EmailType.ACCOUNT_DELETED_FREE: {},
        EmailType.ACCOUNT_DELETED_PRO: {},
        EmailType.NO_MATCH_UPDATE: {"signal_name": "Europe Summer Trip", "signal_id": "test-signal-456", "days_active": 14},
        EmailType.INACTIVE_REENGAGEMENT: {"days_inactive": 21},
    }
    return samples.get(email_type, {})


# ── Email Template CRUD ──────────────────────────────────────────────────────

class TemplateOverrideIn(BaseModel):
    subject: str | None = None
    body_html: str | None = None


@router.get("/email-templates")
def list_email_templates(
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """List all email types with their override status and available variables."""
    verify_admin(x_admin_token)
    from app.db.models.email_template_override import EmailTemplateOverride
    from app.services.email_orchestrator import EMAIL_TYPE_CATEGORY, EmailType
    from app.services.email_templates import TEMPLATE_VARIABLES

    overrides = {
        row.email_type: row
        for row in db.execute(select(EmailTemplateOverride)).scalars().all()
    }

    result = []
    for et in EmailType:
        cat = EMAIL_TYPE_CATEGORY.get(et)
        ovr = overrides.get(et.value)
        result.append({
            "email_type": et.value,
            "name": et.name,
            "category": cat.value if cat else "unknown",
            "variables": TEMPLATE_VARIABLES.get(et, []),
            "has_override": ovr is not None,
            "updated_at": ovr.updated_at.isoformat() if ovr and ovr.updated_at else None,
            "updated_by": ovr.updated_by if ovr else None,
        })
    return {"templates": result}


@router.get("/email-templates/{email_type}")
def get_email_template(
    email_type: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Get the default template and any DB override for a specific email type."""
    verify_admin(x_admin_token)
    from app.db.models.email_template_override import EmailTemplateOverride
    from app.services.email_orchestrator import EmailType
    from app.services.email_templates import TEMPLATE_VARIABLES, get_default_body

    try:
        et = EmailType(email_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown email type: {email_type}")

    default_subject, default_body = get_default_body(et)

    override = db.execute(
        select(EmailTemplateOverride).where(EmailTemplateOverride.email_type == et.value)
    ).scalar_one_or_none()

    return {
        "email_type": et.value,
        "variables": TEMPLATE_VARIABLES.get(et, []),
        "default_subject": default_subject,
        "default_body_html": default_body,
        "override_subject": override.subject if override else None,
        "override_body_html": override.body_html if override else None,
        "has_override": override is not None,
        "updated_at": override.updated_at.isoformat() if override and override.updated_at else None,
        "updated_by": override.updated_by if override else None,
    }


@router.put("/email-templates/{email_type}")
def upsert_email_template(
    email_type: str,
    payload: TemplateOverrideIn,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Create or update a template override."""
    verify_admin(x_admin_token)
    from app.db.models.email_template_override import EmailTemplateOverride
    from app.services.email_orchestrator import EmailType

    try:
        et = EmailType(email_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown email type: {email_type}")

    if not payload.subject and not payload.body_html:
        raise HTTPException(status_code=400, detail="Must provide at least subject or body_html")

    override = db.execute(
        select(EmailTemplateOverride).where(EmailTemplateOverride.email_type == et.value)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if override:
        override.subject = payload.subject
        override.body_html = payload.body_html
        override.updated_at = now
        override.updated_by = "admin"
    else:
        override = EmailTemplateOverride(
            email_type=et.value,
            subject=payload.subject,
            body_html=payload.body_html,
            updated_at=now,
            updated_by="admin",
        )
        db.add(override)

    db.commit()
    logger.info("[ADMIN] upsert_email_template: %s", et.value)

    return {
        "ok": True,
        "email_type": et.value,
        "has_override": True,
        "updated_at": override.updated_at.isoformat(),
    }


@router.delete("/email-templates/{email_type}")
def delete_email_template(
    email_type: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Delete a template override, reverting to the Python default."""
    verify_admin(x_admin_token)
    from app.db.models.email_template_override import EmailTemplateOverride
    from app.services.email_orchestrator import EmailType

    try:
        et = EmailType(email_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown email type: {email_type}")

    override = db.execute(
        select(EmailTemplateOverride).where(EmailTemplateOverride.email_type == et.value)
    ).scalar_one_or_none()

    if not override:
        raise HTTPException(status_code=404, detail="No override exists for this template")

    db.delete(override)
    db.commit()
    logger.info("[ADMIN] delete_email_template: %s (reverted to default)", et.value)

    return {"ok": True, "email_type": et.value, "has_override": False}
