import logging
import os
import re
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
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

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin)])



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
):
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
):
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
):

    total_users = db.execute(select(func.count()).select_from(User).where(User.is_test_user.is_(False))).scalar()
    free_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "free", User.is_test_user.is_(False))).scalar()
    pro_users = db.execute(select(func.count()).select_from(User).where(User.plan_type == "pro", User.is_test_user.is_(False))).scalar()
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

    from app.db.models.email_log import EmailLog
    emails_24h = db.execute(
        select(func.count()).select_from(EmailLog).where(
            EmailLog.status.in_(["sent", "dry_run"]),
            EmailLog.created_at > text("NOW() - INTERVAL '24 hours'")
        )
    ).scalar()
    sms_24h = 0  # SMS not implemented
    failures_24h = db.execute(
        select(func.count()).select_from(EmailLog).where(
            EmailLog.status.in_(["failed", "suppressed"]),
            EmailLog.created_at > text("NOW() - INTERVAL '24 hours'")
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
):
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
):
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    query = select(User)
    count_query = select(func.count()).select_from(User)
    if not include_test_users:
        query = query.where(User.is_test_user.is_(False))
        count_query = count_query.where(User.is_test_user.is_(False))
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(User.email.ilike(f"%{escaped}%"))
        count_query = count_query.where(User.email.ilike(f"%{escaped}%"))

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
):

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_test_user = not user.is_test_user
    db.commit()
    db.refresh(user)

    logger.info("[ADMIN] toggle_test_user: %s → is_test_user=%s", user.email, user.is_test_user)

    return {
        "id": str(user.id),
        "email": user.email,
        "is_test_user": user.is_test_user,
    }



@router.patch("/users/{user_id}/display-name")
def admin_set_display_name(
    user_id: str,
    display_name: str = "",
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    name = display_name.strip()[:100]
    if name:
        from app.services.email_templates.base import _name_is_clean
        if not _name_is_clean(name):
            raise HTTPException(status_code=400, detail="Display name contains disallowed content")
    user.display_name = name if name else None
    db.commit()

    logger.info("[ADMIN] set_display_name: %s → display_name=%s", user.email, user.display_name)

    return {"id": str(user.id), "email": user.email, "display_name": user.display_name}


@router.patch("/users/{user_id}/set-plan")
def set_user_plan(
    user_id: str,
    plan: str,
    db: Session = Depends(get_db),
):
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
    logger.info("[ADMIN] set_plan: %s → plan_type=%s", user.email, user.plan_type)
    return {"id": str(user.id), "email": user.email, "plan_type": user.plan_type, "plan_status": user.plan_status}


@router.patch("/users/{user_id}/set-status")
def set_user_status(
    user_id: str,
    status: str,
    db: Session = Depends(get_db),
):
    allowed = ("active", "expired", "unsubscribed", "subscribed", "disabled")
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(allowed)}")
    # "subscribed" is a user-friendly alias for "active"
    if status == "subscribed":
        status = "active"
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.plan_status = status
    db.commit()
    db.refresh(user)
    logger.info("[ADMIN] set_status: %s → plan_status=%s", user.email, user.plan_status)
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
):

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
):

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
@limiter.limit("10/minute")
def admin_hard_delete_user(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
):
    """Permanently remove a soft-deleted user and all associated data.

    CASCADE FKs handle: signals → deal_matches, signal_runs.
    SET NULL FKs handle: email_log.user_id, notifications_outbox.signal_id.
    """

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
        raise HTTPException(status_code=500, detail="Hard delete failed")

    return {"ok": True, "hard_deleted": True, "user_id": user_id}


# ── PATCH /admin/users/{user_id}/extend-trial ──────────────────────────
@router.patch("/users/{user_id}/extend-trial")
def extend_trial(
    user_id: str,
    days: int = 7,
    db: Session = Depends(get_db),
):
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
):

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
):

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
        "unsubscribe_reason": user.unsubscribe_reason,
    }


# ── POST /admin/run-trial-expiry ──────────────────────────────────────
@router.post("/run-trial-expiry")
def run_trial_expiry(
    db: Session = Depends(get_db),
):
    """
    Find users whose trial has expired and send them an upsell email.
    Safe to call repeatedly — each user only gets one email (guarded by
    trial_expired_email_sent_at).
    """

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
            User.email_opt_out.is_(False),
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
):
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    from app.db.models.email_log import EmailLog

    query = select(EmailLog)
    count_query = select(func.count()).select_from(EmailLog)

    if status:
        query = query.where(EmailLog.status == status)
        count_query = count_query.where(EmailLog.status == status)
    if email:
        escaped_email = email.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(EmailLog.to_email.ilike(f"%{escaped_email}%"))
        count_query = count_query.where(EmailLog.to_email.ilike(f"%{escaped_email}%"))

    rows = db.execute(
        query.order_by(EmailLog.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    total = db.execute(count_query).scalar()

    return {
        "notifications": [
            {
                "id": str(n.id),
                "created_at": n.created_at.isoformat(),
                "user_email": n.to_email,
                "signal_id": None,
                "type": n.email_type,
                "status": n.status,
                "error_message": n.suppressed_reason,
                "to_email": n.to_email,
                "opened_at": None,
                "open_count": 0,
                "subject": n.subject,
                "body_text": None,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            }
            for n in rows
        ],
        "total": total,
    }


@router.get("/scrape-runs")
def list_scrape_runs(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    total = db.execute(select(func.count()).select_from(ScrapeRun)).scalar()

    runs = db.execute(
        select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    # Check if a scraper is actively holding the advisory lock (key 8675309).
    # Uses pg_try_advisory_lock to test, then immediately releases if acquired.
    # TOCTOU note: scraper_is_running is a point-in-time snapshot; a scraper could
    # start or stop between this check and the response. This is acceptable for
    # dashboard display purposes — the next auto-refresh will correct it.
    _lock_acquired = db.execute(text("SELECT pg_try_advisory_lock(8675309)")).scalar()
    if _lock_acquired:
        db.execute(text("SELECT pg_advisory_unlock(8675309)"))
    scraper_is_running = not _lock_acquired

    # Batch new_deals counts in a single query to avoid N+1 per row.
    # Build a map of run_id -> (new_deals, deals_seen) using window ranges.
    run_ids = [r.id for r in runs]
    new_deals_map: dict[int, int] = {}
    deals_seen_map: dict[int, int] = {}
    # Identify the single live run: the most recent with status running/stale
    # when the advisory lock is held. Only ONE run can be live.
    live_run_id = None
    if scraper_is_running:
        for run in runs:  # runs are ordered desc, so first match is most recent
            if run.status in ("running", "stale"):
                live_run_id = run.id
                break

    # Batch: compute new_deals for all runs in a single query using LATERAL join.
    # Each run's window: [started_at, completed_at) or [started_at, NOW()) for live.
    new_deals_rows = db.execute(text("""
        SELECT sr.id,
               (SELECT COUNT(*) FROM deals d WHERE d.found_at >= sr.started_at
                AND d.found_at < COALESCE(
                    CASE WHEN sr.id = :live_id THEN NULL ELSE sr.completed_at END,
                    NOW()
                )) AS new_deals,
               CASE WHEN sr.id = :live_id THEN
                   (SELECT COUNT(*) FROM deals d WHERE d.last_seen_at >= sr.started_at)
               ELSE NULL END AS deals_seen
        FROM scrape_runs sr
        WHERE sr.id = ANY(:ids)
    """), {"ids": run_ids, "live_id": live_run_id or -1}).fetchall()
    for row in new_deals_rows:
        new_deals_map[row[0]] = row[1]
        if row[2] is not None:
            deals_seen_map[row[0]] = row[2]

    results = []
    prev_total = None
    for run in reversed(runs):
        is_live = run.id == live_run_id
        effective_status = run.status
        if is_live:
            effective_status = "running"

        # Use live deals_seen for delta computation when live
        display_total = deals_seen_map.get(run.id, run.total_deals) if is_live else run.total_deals
        delta = (display_total - prev_total) if prev_total is not None else None
        prev_total = display_total

        if is_live:
            duration_sec = db.execute(
                text("SELECT EXTRACT(EPOCH FROM NOW() - :started)::int"),
                {"started": run.started_at},
            ).scalar()
        elif run.completed_at and run.started_at:
            duration_sec = int((run.completed_at - run.started_at).total_seconds())
        else:
            duration_sec = None

        results.append({
            "id": run.id,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "total_deals": display_total,
            "total_matches": run.total_matches,
            "error_count": run.error_count,
            "error_log": run.error_log,
            "deals_deactivated": run.deals_deactivated,
            "status": effective_status,
            "is_live": is_live,
            "duration_sec": duration_sec,
            "deal_delta": delta,
            "new_deals": new_deals_map.get(run.id, 0),
            "proxy_ip": run.proxy_ip,
            "proxy_geo": run.proxy_geo,
        })

    results.reverse()
    return {"runs": results, "total": total, "scraper_active": scraper_is_running}


@router.get("/deals")
def list_deals(
    page: int = 1,
    limit: int = 50,
    scrape_run_id: int | None = None,
    view: str = "active",
    db: Session = Depends(get_db),
):
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
            Deal.is_active.is_(False),
            Deal.deactivated_at.isnot(None),
        )
        count_query = select(func.count()).select_from(Deal).where(
            Deal.is_active.is_(False),
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
                "provider": d.provider,
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
):
    offset = (page - 1) * limit
    limit = max(1, min(limit, 100))

    query = select(User)
    count_query = select(func.count()).select_from(User)

    # New status_filter takes priority over include_test_users
    if status_filter == "active":
        query = query.where(User.deleted_at.is_(None), User.is_test_user.is_(False))
        count_query = count_query.where(User.deleted_at.is_(None), User.is_test_user.is_(False))
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
        query = query.where(User.is_test_user.is_(False), User.deleted_at.is_(None))
        count_query = count_query.where(User.is_test_user.is_(False), User.deleted_at.is_(None))
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(User.email.ilike(f"%{escaped}%"))
        count_query = count_query.where(User.email.ilike(f"%{escaped}%"))

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

        # Email history for this user (from email_log, most recent 50)
        from app.db.models.email_log import EmailLog
        notifs = db.execute(
            select(EmailLog)
            .where(EmailLog.to_email == u.email)
            .order_by(EmailLog.created_at.desc())
            .limit(50)
        ).scalars().all()

        notification_count = db.execute(
            select(func.count()).select_from(EmailLog)
            .where(EmailLog.to_email == u.email)
        ).scalar()

        notification_list = []
        for n in notifs:
            notification_list.append({
                "id": str(n.id),
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "type": n.email_type,
                "status": n.status,
                "opened_at": None,
                "open_count": 0,
                "error": n.suppressed_reason,
                "subject": n.subject,
            })

        last_open = None

        results.append({
            "id": str(u.id),
            "clerk_id": u.clerk_id,
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
            "notification_weekly_summary": u.notification_weekly_summary,
            "timezone": u.timezone,
            "email_mode": u.email_mode,
            "display_name": u.display_name,
            # Soft-delete fields
            "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
            "deleted_by": u.deleted_by,
            "deleted_reason": u.deleted_reason,
            "deleted_reason_other": u.deleted_reason_other,
            "unsubscribe_reason": u.unsubscribe_reason,
        })

    return {"users": results, "total": total}


@router.post("/hotels/sync")
def sync_hotels(db: Session = Depends(get_db)):
    """Sync new hotels from deals and auto-match against TripAdvisor seed data.

    Called once when the admin Hotels tab loads. Separated from GET to keep
    reads fast and side-effect-free.
    """
    # Insert any new hotels from deals that aren't in hotel_links yet
    result = db.execute(text("""
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
    new_count = result.rowcount
    db.commit()

    return {"synced": new_count}


@router.get("/hotels")
def list_hotels(
    search: str = "",
    db: Session = Depends(get_db),
):
    """List all hotels with their TripAdvisor links (read-only)."""

    query = select(HotelLink).order_by(HotelLink.hotel_name)
    if search:
        safe_search = search.replace("%", "\\%").replace("_", "\\_")
        query = query.where(HotelLink.hotel_name.ilike(f"%{safe_search}%"))

    rows = db.execute(query).scalars().all()

    # Count active deals per hotel in a single query
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
                "tripadvisor_url": h.tripadvisor_url,
                "active_deals": deal_counts.get(h.hotel_id, 0),
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in rows
        ],
        "total": len(rows),
    }


class UpdateHotelLinkIn(BaseModel):
    tripadvisor_url: str | None = None


@router.put("/hotels/{hotel_id}")
def update_hotel_link(
    hotel_id: str,
    payload: UpdateHotelLinkIn,
    db: Session = Depends(get_db),
):
    """Update the TripAdvisor URL for a hotel."""

    hotel = db.execute(
        select(HotelLink).where(HotelLink.hotel_id == hotel_id)
    ).scalar_one_or_none()

    if not hotel:
        raise HTTPException(status_code=404, detail="Hotel not found")

    url = (payload.tripadvisor_url or "").strip()
    if url and not url.startswith(("https://www.tripadvisor.", "https://tripadvisor.")):
        raise HTTPException(status_code=400, detail="URL must be a TripAdvisor link")
    hotel.tripadvisor_url = url if url else None
    hotel.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"ok": True, "hotel_id": hotel_id, "tripadvisor_url": hotel.tripadvisor_url}


# ── TripAdvisor URL Finder ───────────────────────────────────────────────────

import re as _re
import threading
import time
import random

_ta_url_finder_lock = threading.Lock()
_ta_url_finder_status: dict = {"running": False, "found": 0, "not_found": 0, "total": 0, "processed": 0}

_TA_URL_RE = _re.compile(
    r'(https?://(?:www\.)?tripadvisor\.(?:com|ca)/Hotel_Review-[^\s"\'<>&?#]+)',
    _re.IGNORECASE,
)


def _search_startpage(query: str, use_proxy: bool = True) -> str | None:
    """Search Startpage for a TripAdvisor Hotel_Review URL."""
    import requests as _requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    proxies = None
    if use_proxy:
        pu = os.environ.get("PROXY_USER", "")
        pp = os.environ.get("PROXY_PASS", "")
        if pu and pp:
            proxy_url = f"http://{pu}__cr.ca:{pp}@gw.dataimpulse.com:823"
            proxies = {"http": proxy_url, "https": proxy_url}

    try:
        resp = _requests.post(
            "https://www.startpage.com/sp/search",
            data={"query": query},
            headers=headers,
            proxies=proxies,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        matches = _TA_URL_RE.findall(resp.text)
        if matches:
            url = matches[0]
            url = _re.sub(r"tripadvisor\.ca", "tripadvisor.com", url)
            return url
    except Exception:
        pass
    return None


def _run_url_finder():
    """Background task: find TripAdvisor URLs for all hotels missing them."""
    global _ta_url_finder_status
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        hotels = db.execute(
            select(HotelLink)
            .where(HotelLink.tripadvisor_url.is_(None))
            .order_by(HotelLink.hotel_name)
        ).scalars().all()

        _ta_url_finder_status["total"] = len(hotels)
        _ta_url_finder_status["processed"] = 0
        _ta_url_finder_status["found"] = 0
        _ta_url_finder_status["not_found"] = 0

        for hotel in hotels:
            query = f"tripadvisor {hotel.hotel_name} {hotel.destination or ''} hotel".strip()
            url = _search_startpage(query)

            if url:
                hotel.tripadvisor_url = url
                hotel.updated_at = datetime.now(timezone.utc)
                db.commit()
                _ta_url_finder_status["found"] += 1
            else:
                _ta_url_finder_status["not_found"] += 1

            _ta_url_finder_status["processed"] += 1
            time.sleep(2 + random.random() * 2)

    except Exception as e:
        logger.error(f"URL finder error: {e}")
        db.rollback()
    finally:
        db.close()
        _ta_url_finder_status["running"] = False


@router.post("/hotels/find-urls")
def start_url_finder():
    """Start background task to find TripAdvisor URLs for hotels missing them."""
    global _ta_url_finder_status

    if _ta_url_finder_status["running"]:
        return _ta_url_finder_status

    with _ta_url_finder_lock:
        if _ta_url_finder_status["running"]:
            return _ta_url_finder_status
        _ta_url_finder_status = {"running": True, "found": 0, "not_found": 0, "total": 0, "processed": 0}
        thread = threading.Thread(target=_run_url_finder, daemon=True)
        thread.start()

    return _ta_url_finder_status


@router.get("/hotels/find-urls/status")
def url_finder_status():
    """Get the current status of the URL finder background task."""
    return _ta_url_finder_status


# ── Email Testing ────────────────────────────────────────────────────────────

class SendTestEmailIn(BaseModel):
    email_type: str
    to_email: EmailStr


@router.get("/email-types")
def list_email_types(
):
    """Return all available email types with their categories."""
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
@limiter.limit("10/minute")
def send_test_email(
    request: Request,
    payload: SendTestEmailIn,
    db: Session = Depends(get_db),
):
    """Render a template with a fake user and send it to the provided address."""
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
        logger.error("Template render failed for type %s: %s", payload.email_type, e)
        raise HTTPException(status_code=500, detail="Template render failed")

    subject = f"[TEST] {subject}"

    sent = send_email(payload.to_email, subject, html)

    # Log to email_log for admin visibility
    from app.db.models.email_log import EmailLog
    log = EmailLog(
        user_id=real_user.id if real_user else None,
        email_type=f"test_{email_type.value}",
        category="transactional",
        idempotency_key=f"test_{email_type.value}_{payload.to_email}_{datetime.now(timezone.utc).isoformat()}",
        to_email=payload.to_email,
        subject=subject,
        provider_message_id=sent if sent else None,
        status="sent" if sent else "failed",
        sent_at=datetime.now(timezone.utc) if sent else None,
    )
    db.add(log)
    db.commit()

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
):
    """Render a template and return the HTML without sending."""
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
        logger.error("Template render failed for preview type %s: %s", payload.email_type, e)
        raise HTTPException(status_code=500, detail="Template render failed")

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
):
    """List all email types with their override status and available variables."""
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
):
    """Get the default template and any DB override for a specific email type."""
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
):
    """Create or update a template override."""
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
):
    """Delete a template override, reverting to the Python default."""
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


# ── Email Queue ──────────────────────────────────────────────────────────────

@router.get("/email-queue/stats")
def email_queue_stats(
    db: Session = Depends(get_db),
):
    from app.services.email_queue import get_queue_stats
    return get_queue_stats(db)


@router.get("/email-queue/items")
def email_queue_items(
    limit: int = 50,
    status: str = "",
    search: str = "",
    db: Session = Depends(get_db),
):
    from app.services.email_queue import get_recent_queue_items
    limit = max(1, min(limit, 100))
    return {"items": get_recent_queue_items(db, limit=limit, status=status, search=search)}


@router.get("/email-queue/preview/{item_id}")
def email_queue_preview(
    item_id: str,
    db: Session = Depends(get_db),
):
    """Return the rendered HTML body of a queue item for preview."""
    from app.db.models.email_queue import EmailQueue
    try:
        uid = UUID(item_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid ID"})
    row = db.execute(
        select(EmailQueue.html_body, EmailQueue.subject, EmailQueue.to_email)
        .where(EmailQueue.id == uid)
    ).one_or_none()
    if not row:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return {"subject": row.subject, "to_email": row.to_email, "html": row.html_body}


@router.get("/email-queue/daily-volume")
def email_queue_daily_volume(
    db: Session = Depends(get_db),
):
    """Return daily email volume for the last 14 days, broken down by sent vs failed/dead."""
    from app.db.models.email_queue import EmailQueue

    fourteen_days_ago = datetime.now(timezone.utc) - timedelta(days=14)
    rows = db.execute(
        select(
            func.date(EmailQueue.created_at).label("date"),
            EmailQueue.status,
            func.count().label("count"),
        )
        .where(EmailQueue.created_at >= fourteen_days_ago)
        .group_by(func.date(EmailQueue.created_at), EmailQueue.status)
        .order_by(func.date(EmailQueue.created_at))
    ).all()

    # Aggregate into {date: {sent, failed, dead}} structure
    from collections import defaultdict
    by_date: dict[str, dict[str, int]] = defaultdict(lambda: {"sent": 0, "failed": 0, "dead": 0})
    for row in rows:
        d = str(row.date)
        if row.status == "sent":
            by_date[d]["sent"] += row.count
        elif row.status == "failed":
            by_date[d]["failed"] += row.count
        elif row.status == "dead":
            by_date[d]["dead"] += row.count

    # Fill in missing dates with zeros
    result = []
    for i in range(14):
        d = (datetime.now(timezone.utc) - timedelta(days=13 - i)).strftime("%Y-%m-%d")
        entry = by_date.get(d, {"sent": 0, "failed": 0, "dead": 0})
        result.append({"date": d, **entry})

    return result


@router.post("/email-queue/retry-dead")
def email_queue_retry_dead(
    db: Session = Depends(get_db),
):
    from app.services.email_queue import retry_dead
    count = retry_dead(db)
    return {"ok": True, "retried": count}


class RetrySelectedIn(BaseModel):
    ids: list[str]


@router.post("/email-queue/retry-selected")
def email_queue_retry_selected(
    request_body: RetrySelectedIn,
    db: Session = Depends(get_db),
):
    if not request_body.ids:
        return {"ok": False, "error": "No IDs provided", "retried": 0}
    from app.services.email_queue import retry_by_ids
    count = retry_by_ids(db, request_body.ids[:100])  # cap at 100
    return {"ok": True, "retried": count}


@router.post("/email-queue/pause")
def email_queue_pause(
    db: Session = Depends(get_db),
):
    from app.services.email_queue import pause_queue
    count = pause_queue(db)
    return {"ok": True, "paused": count}


@router.post("/email-queue/resume")
def email_queue_resume(
    db: Session = Depends(get_db),
):
    from app.services.email_queue import resume_queue
    count = resume_queue(db)
    return {"ok": True, "resumed": count}


@router.post("/email-queue/flush")
@limiter.limit("5/minute")
def email_queue_flush(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.services.email_queue import flush_queue
    count = flush_queue(db)
    return {"ok": True, "flushed": count}


@router.post("/email-queue/drain")
@limiter.limit("5/minute")
def email_queue_drain_now(
    request: Request,
    db: Session = Depends(get_db),
):
    """Manually trigger a queue drain cycle from admin."""
    from app.services.email_queue import drain
    stats = drain(db)
    return {"ok": True, **stats}


@router.post("/backfill-value-labels")
def backfill_value_labels(
    db: Session = Depends(get_db),
):
    """Backfill value_label on existing deal_matches using market scoring."""
    from app.db.models.deal_match import DealMatch
    from app.services.market_intel import score_deal_for_match

    matches = (
        db.query(DealMatch)
        .join(Deal)
        .filter(DealMatch.value_label.is_(None), Deal.is_active == True)
        .all()
    )

    stats_cache: dict = {}
    updated = 0
    for match in matches:
        try:
            label = score_deal_for_match(db, match.deal, stats_cache=stats_cache)
            match.value_label = label  # None if not positive
            updated += 1
        except Exception as e:
            logger.warning("Backfill error for match %s: %s", match.id, e)

    db.commit()
    return {"ok": True, "matches_processed": len(matches), "updated": updated}
