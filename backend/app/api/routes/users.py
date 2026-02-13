"""User endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/by-clerk-id/{clerk_id}")
async def get_user_by_clerk_id(
    clerk_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Get user by Clerk ID."""
    user = db.execute(
        select(User).where(User.clerk_id == clerk_id)
    ).scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with clerk_id {clerk_id} not found"
        )
    
    return {
        "id": str(user.id),
        "email": user.email,
        "clerk_id": user.clerk_id
    }
