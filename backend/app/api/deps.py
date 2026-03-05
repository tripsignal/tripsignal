"""Shared FastAPI dependencies for authentication and authorization."""
import hmac
import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger("tripsignal.security")


def verify_admin(x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
    """Verify the admin token. Reusable across admin and scraper-lab routers."""
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if not admin_token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, admin_token):
        logger.warning("SECURITY | admin_auth_failed | token_present=%s", x_admin_token is not None)
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_clerk_user_id(
    authorization: str | None = Header(None),
    x_clerk_user_id: str | None = Header(None, alias="x-clerk-user-id"),
    x_user_id: str | None = Header(None, alias="x-user-id"),
) -> str:
    """Extract and verify the Clerk user ID.

    Priority:
    1. Authorization: Bearer <jwt> — verify JWT, extract sub
    2. x-clerk-user-id header — legacy fallback (TRANSITION ONLY)
    3. x-user-id header — legacy fallback (TRANSITION ONLY)
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            from app.core.clerk_auth import verify_clerk_token
            return verify_clerk_token(token)
        except Exception as e:
            logger.warning("SECURITY | jwt_verification_failed | error=%s", str(e))
            raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Legacy fallback — remove after frontend migration
    legacy_id = x_clerk_user_id or x_user_id
    if legacy_id:
        logger.debug("SECURITY | legacy_header_auth | clerk_id=%s", legacy_id)
        return legacy_id

    raise HTTPException(status_code=401, detail="Authentication required")
