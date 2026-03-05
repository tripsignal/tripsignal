"""Clerk JWT verification via JWKS."""
import logging

import jwt
from jwt import PyJWKClient

from app.core.config import settings

logger = logging.getLogger("tripsignal.security")

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not settings.CLERK_JWKS_URL:
            raise RuntimeError("CLERK_JWKS_URL not configured")
        _jwks_client = PyJWKClient(
            settings.CLERK_JWKS_URL,
            cache_keys=True,
            lifespan=3600,
        )
    return _jwks_client


def verify_clerk_token(token: str) -> str:
    """Verify a Clerk JWT and return the user ID (sub claim).

    Raises ValueError on any verification failure.
    """
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)

    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        options={"verify_exp": True, "verify_aud": False},
    )

    sub = payload.get("sub")
    if not sub:
        raise ValueError("JWT missing 'sub' claim")

    return sub
