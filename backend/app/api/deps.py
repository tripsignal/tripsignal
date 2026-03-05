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
