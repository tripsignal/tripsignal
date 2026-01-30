"""
API dependencies (auth, shared DI).

DEV-ONLY AUTH:
- If no Authorization header is present, this returns a fixed test user.
- This is ONLY for local/dev testing so you can verify plan enforcement.
"""

from typing import Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.user import User


# DEV: set this to the user you want to impersonate during testing
DEV_USER_ID = UUID("9b2bb98a-0c15-4726-9c20-de3b81e5172f")


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> User:
    """
    DEV-only current user dependency.

    If Authorization header is missing:
      - returns DEV_USER_ID user from DB

    If Authorization header is present:
      - for now, reject (since you said you don't have a token yet)
    """
    if not authorization:
        user = db.get(User, DEV_USER_ID)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Dev user not found in DB",
            )
        return user

    # If you later implement real JWT auth, replace this section.
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Auth token provided but JWT auth is not implemented in deps.py yet",
    )
