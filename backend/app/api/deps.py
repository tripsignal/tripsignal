"""Shared FastAPI dependencies for authentication and authorization."""
import hmac
import logging
import os

from fastapi import Header, HTTPException

from app.core.clerk_auth import verify_clerk_token

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
) -> str:
    """Extract and verify the Clerk user ID from a JWT Bearer token.

    Requires a valid, cryptographically signed JWT. No fallbacks.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization[7:]
    try:
        clerk_id = verify_clerk_token(token)
        logger.debug("jwt_auth_ok | clerk_id=%s", clerk_id)
        return clerk_id
    except Exception as e:
        logger.warning("SECURITY | jwt_verification_failed | error=%s", str(e))
        raise HTTPException(status_code=401, detail="Invalid or expired token")
