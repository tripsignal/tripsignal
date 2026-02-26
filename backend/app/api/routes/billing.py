"""Billing routes for Stripe integration."""
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.db.models.user import User

router = APIRouter(prefix="/api/billing", tags=["billing"])

stripe.api_key = settings.STRIPE_SECRET_KEY


def get_current_user(x_clerk_user_id: str = Header(...), db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.clerk_id == x_clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/checkout")
async def create_checkout_session(
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
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        clerk_id = session["metadata"]["clerk_id"]
        user = db.query(User).filter(User.clerk_id == clerk_id).first()
        if user:
            user.plan_type = "pro"
            user.plan_status = "active"
            user.stripe_subscription_id = session.get("subscription")
            db.commit()

    elif event["type"] in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription = event["data"]["object"]
        user = db.query(User).filter(User.stripe_subscription_id == subscription["id"]).first()
        if user:
            if subscription["status"] == "active":
                user.plan_type = "pro"
                user.plan_status = "active"
            else:
                user.plan_type = "free"
                user.plan_status = "active"
            user.stripe_subscription_status = subscription["status"]
            from datetime import datetime, timezone
            user.subscription_current_period_end = datetime.fromtimestamp(
                subscription["current_period_end"], tz=timezone.utc
            )
            db.commit()

    return {"status": "ok"}