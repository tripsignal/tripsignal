"""
API dependencies (auth, shared DI).

Clerk-based auth:
- Reads X-User-Id header sent from Next.js frontend
- Validates dev token for security
- Returns or creates user based on Clerk ID
"""

from typing import Optional
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.subscription import Subscription
from app.db.models.plan import Plan


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
) -> User:
    """
    Clerk auth bridge.
    
    - Validates dev token from Authorization header
    - Reads Clerk user ID from X-User-Id header
    - Returns user from database (creates if doesn't exist)
    """
    print(f"[AUTH DEBUG] Authorization: {authorization}")
    print(f"[AUTH DEBUG] X-User-Id: {x_user_id}")
    
    from app.core.config import settings

    dev_token = getattr(settings, "DEV_API_TOKEN", None)

    # Validate dev token
    if not dev_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth not configured",
        )
    
    if not authorization or authorization.strip() != f"Bearer {dev_token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization token",
        )
    
    # Require X-User-Id header
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header required",
        )
 # Look up user by clerk_id
    user = db.execute(
        select(User).where(User.clerk_id == x_user_id)
    ).scalar_one_or_none()
    
    if not user:
        # Auto-create user if doesn't exist
        print(f"[AUTH DEBUG] Creating new user for clerk_id: {x_user_id}")
        user = User(
            id=uuid4(),
            clerk_id=x_user_id,
            email=f"{x_user_id}@clerk.temp",
        )
        db.add(user)
        db.flush()  # Flush to get the user ID
    
    # Ensure user has an active subscription (for both new and existing users)
    existing_subscription = db.execute(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status == "active"
        )
    ).scalar_one_or_none()
    
    if not existing_subscription:
        print(f"[AUTH DEBUG] No active subscription found for user {user.id}, creating one")
        # Create a free plan subscription
        free_plan = db.execute(
            select(Plan).where(Plan.name == "Free")
        ).scalar_one_or_none()
        
        if free_plan:
            subscription = Subscription(
                id=uuid4(),
                user_id=user.id,
                plan_id=free_plan.id,
                status="active"
            )
            db.add(subscription)
            print(f"[AUTH DEBUG] Created subscription {subscription.id} for user {user.id}")
        else:
            print(f"[AUTH DEBUG] WARNING: Free plan not found!")
    else:
        print(f"[AUTH DEBUG] User {user.id} already has active subscription {existing_subscription.id}")
    
    db.commit()
    db.refresh(user)
    
    print(f"[AUTH DEBUG] User found/created: {user.id}")
    
    return user
