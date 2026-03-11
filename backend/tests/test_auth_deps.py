"""
Regression tests for get_clerk_user_id dependency in deps.py.

Verifies that the backend rejects requests that lack a valid JWT Bearer token
and never falls back to trusting a plain header claim.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_auth_deps.py -v
"""
import pytest
from fastapi import HTTPException

from app.api.deps import get_clerk_user_id


def test_missing_authorization_header_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        get_clerk_user_id(authorization=None)
    assert exc_info.value.status_code == 401


def test_empty_authorization_header_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        get_clerk_user_id(authorization="")
    assert exc_info.value.status_code == 401


def test_missing_bearer_prefix_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        get_clerk_user_id(authorization="some-token-without-bearer-prefix")
    assert exc_info.value.status_code == 401


def test_malformed_jwt_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        get_clerk_user_id(authorization="Bearer this.is.not.a.valid.jwt")
    assert exc_info.value.status_code == 401


def test_plaintext_user_id_header_is_rejected():
    """Regression: the old x-clerk-user-id header pattern must not be trusted.
    Passing a bare user ID string (no JWT structure) must be rejected.
    """
    with pytest.raises(HTTPException) as exc_info:
        get_clerk_user_id(authorization="Bearer user_2abc123fakeClerkId")
    assert exc_info.value.status_code == 401
