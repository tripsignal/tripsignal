"""Shared rate limiter instance (slowapi)."""
from starlette.requests import Request

from slowapi import Limiter


def _get_real_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For (set by Caddy), fall back to socket IP."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # First IP in the chain is the original client
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
