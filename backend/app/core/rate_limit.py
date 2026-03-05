"""Shared rate limiter instance (slowapi)."""
from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_key(request):
    """Extract rate-limit key: prefer x-clerk-user-id, fall back to IP."""
    user_id = (
        request.headers.get("x-clerk-user-id")
        or request.headers.get("x-user-id")
    )
    if user_id:
        return user_id
    return get_remote_address(request)


limiter = Limiter(key_func=_get_key)
