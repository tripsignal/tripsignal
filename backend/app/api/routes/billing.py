"""Billing routes for Stripe integration.

Webhook handlers update DB first, THEN call the orchestrator for emails.
No direct email sends — all go through EmailOrchestratorService.
"""
import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models.signal import Signal
from app.db.models.stripe_event import StripeEvent
from app.db.models.user import User
from app.db.session import get_db
from app.services.email_orchestrator import EmailType
from app.services.email_orchestrator import trigger as email_trigger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

stripe.api_key = settings.STRIPE_SECRET_KEY


def get_current_user(x_clerk_user_id: str = Header(...), db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.clerk_id == x_clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/checkout")
@limiter.limit("5/minute")
async def create_checkout_session(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Create or retrieve Stripe customer
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email, metadata={"clerk_id": user.clerk_id})
        user.stripe_customer_id = customer.id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": settings.STRIPE_PRO_PRICE_ID, "quantity": 1}],
        mode="subscription",
        success_url="https://tripsignal.ca/signals?upgraded=true",
        cancel_url="https://tripsignal.ca/signals",
        metadata={"clerk_id": user.clerk_id},
    )
    return {"url": session.url}


@router.post("/portal")
async def create_portal_session(user: User = Depends(get_current_user)):
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url="https://tripsignal.ca/signals",
    )
    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        logger.warning(
            "SECURITY | stripe_webhook_sig_failed | ip=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_id = event["id"]
    event_type = event["type"]

    # ── Store event for deduplication + audit ──────────────────────────────
    stmt = pg_insert(StripeEvent).values(
        stripe_event_id=event_id,
        event_type=event_type,
        payload=event["data"]["object"],
    ).on_conflict_do_nothing(index_elements=["stripe_event_id"])
    result = db.execute(stmt)
    db.flush()

    if result.rowcount == 0:
        logger.info("Stripe event %s already processed, skipping", event_id)
        return {"status": "duplicate"}

    # ── Process event ─────────────────────────────────────────────────────
    processing_error = None
    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(db, event)

        elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            _handle_subscription_change(db, event)

        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(db, event)

    except Exception as e:
        processing_error = str(e)[:2000]
        logger.exception("Error processing Stripe event %s: %s", event_id, e)

    # ── Mark event as processed ───────────────────────────────────────────
    evt = db.execute(
        select(StripeEvent).where(StripeEvent.stripe_event_id == event_id)
    ).scalar_one_or_none()
    if evt:
        evt.processed_at = datetime.now(timezone.utc)
        evt.processing_error = processing_error
    db.commit()

    return {"status": "ok"}


# ── Event handlers ────────────────────────────────────────────────────────────

def _handle_checkout_completed(db: Session, event: dict) -> None:
    """Handle checkout.session.completed — user upgraded to PRO."""
    session = event["data"]["object"]
    clerk_id = session["metadata"].get("clerk_id")
    if not clerk_id:
        return

    user = db.query(User).filter(User.clerk_id == clerk_id).first()
    if not user:
        return

    was_pro = user.plan_type == "pro"
    subscription_id = session.get("subscription", "")

    # ── 1. Update DB first ────────────────────────────────────────────────
    user.plan_type = "pro"
    user.plan_status = "active"
    user.stripe_subscription_id = subscription_id
    user.stripe_subscription_status = "active"
    db.flush()

    # Reactivate any payment-paused signals
    _reactivate_signals(db, user.id)
    db.flush()

    # ── 2. THEN call orchestrator ─────────────────────────────────────────
    if not was_pro:
        try:
            email_trigger(
                db=db,
                email_type=EmailType.PRO_ACTIVATED,
                user_id=str(user.id),
                context={"subscription_id": subscription_id},
            )
        except Exception:
            logger.exception("Failed to trigger pro_activated email for %s", user.email)


def _handle_subscription_change(db: Session, event: dict) -> None:
    """Handle customer.subscription.updated and customer.subscription.deleted."""
    subscription = event["data"]["object"]
    subscription_id = subscription["id"]

    user = db.query(User).filter(
        User.stripe_subscription_id == subscription_id
    ).first()
    if not user:
        return

    status = subscription["status"]

    # ── 1. Update DB first ────────────────────────────────────────────────
    user.stripe_subscription_status = status
    if subscription.get("current_period_end"):
        user.subscription_current_period_end = datetime.fromtimestamp(
            subscription["current_period_end"], tz=timezone.utc
        )

    if status == "active":
        user.plan_type = "pro"
        user.plan_status = "active"
        # Reactivate payment-paused signals on successful payment recovery
        _reactivate_signals(db, user.id)
    elif status in ("past_due", "unpaid"):
        # Keep plan_type as pro — subscription is still live, just having payment issues.
        # Signals were already paused by _handle_payment_failed.
        pass
    else:
        # canceled, incomplete_expired, etc.
        user.plan_type = "free"
        user.plan_status = "active"

    db.flush()

    # ── 2. THEN call orchestrator ─────────────────────────────────────────
    if event["type"] == "customer.subscription.deleted":
        try:
            period_end = ""
            if user.subscription_current_period_end:
                period_end = user.subscription_current_period_end.strftime("%B %d, %Y")
            email_trigger(
                db=db,
                email_type=EmailType.SUBSCRIPTION_CANCELED,
                user_id=str(user.id),
                context={
                    "period_end": period_end,
                    "subscription_id": subscription_id,
                },
            )
        except Exception:
            logger.exception("Failed to trigger canceled email for %s", user.email)


def _handle_payment_failed(db: Session, event: dict) -> None:
    """Handle invoice.payment_failed — pause signals and notify user."""
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
    if not user:
        return

    invoice_id = invoice.get("id", "")

    # ── 1. Immediately pause all active signals ───────────────────────────
    paused_count = _pause_signals(db, user.id)
    db.flush()
    if paused_count:
        logger.info("payment_failed: paused %d signals for %s", paused_count, user.email)

    # ── 2. THEN call orchestrator ─────────────────────────────────────────
    try:
        email_trigger(
            db=db,
            email_type=EmailType.PAYMENT_FAILED,
            user_id=str(user.id),
            context={"invoice_id": invoice_id},
        )
    except Exception:
        logger.exception("Failed to trigger payment_failed email for %s", user.email)


# ── Signal pause / reactivation ──────────────────────────────────────────────

def _pause_signals(db: Session, user_id) -> int:
    """Pause all active signals for a user due to payment failure.

    Sets status to 'payment_paused' (distinct from user-initiated 'paused')
    so we can selectively reactivate only payment-paused signals later.
    Returns the number of signals paused.
    """
    result = db.execute(
        update(Signal)
        .where(Signal.user_id == user_id, Signal.status == "active")
        .values(status="payment_paused")
    )
    return result.rowcount


def _reactivate_signals(db: Session, user_id) -> int:
    """Reactivate signals that were paused due to payment failure.

    Only reactivates 'payment_paused' signals — user-initiated 'paused'
    signals stay paused (user must reactivate those manually).
    Returns the number of signals reactivated.
    """
    result = db.execute(
        update(Signal)
        .where(Signal.user_id == user_id, Signal.status == "payment_paused")
        .values(status="active")
    )
    count = result.rowcount
    if count:
        logger.info("reactivated %d payment-paused signals for user %s", count, user_id)
    return count
