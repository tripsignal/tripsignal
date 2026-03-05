"""HMAC-based unsubscribe token generation and validation."""
import base64
import hashlib
import hmac

from app.core.config import settings

UNSUB_SECRET = settings.UNSUB_SECRET
if not UNSUB_SECRET:
    raise RuntimeError("UNSUB_SECRET must be set in environment — see .env.example")


def generate_unsub_token(user_id: str) -> str:
    """Generate an HMAC-signed token encoding a user ID for unsubscribe links."""
    sig = hmac.new(UNSUB_SECRET.encode(), user_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(f"{user_id}:{sig.hex()}".encode()).decode()


def validate_unsub_token(token: str) -> str | None:
    """Validate an unsubscribe token. Returns user_id (str UUID) if valid, None otherwise."""
    try:
        decoded = base64.urlsafe_b64decode(token).decode()
        user_id, sig_hex = decoded.rsplit(":", 1)
        expected = hmac.new(UNSUB_SECRET.encode(), user_id.encode(), hashlib.sha256).digest().hex()
        if hmac.compare_digest(sig_hex, expected):
            return user_id
    except Exception:
        pass
    return None
