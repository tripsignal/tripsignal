"""Shared rate limiter instance (slowapi)."""
from starlette.requests import Request

from slowapi import Limiter


def _get_real_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For (set by Caddy), fall back to socket IP.

    Takes the rightmost IP (last entry) because Caddy appends the real client IP.
    A spoofed XFF header like "fake, real" means Caddy produces "fake, real, actual"
    — the last value is always the one Caddy added from the TCP connection.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Rightmost IP is the one added by our trusted proxy (Caddy)
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
