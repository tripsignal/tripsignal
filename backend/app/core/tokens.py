"""HMAC-based unsubscribe token generation and validation.

V2 tokens embed a Unix timestamp for expiry enforcement.
Format: base64(user_id:timestamp:hmac_hex)

V1 tokens (user_id:hmac_hex, no timestamp) are accepted during
a transition period to avoid breaking links in existing emails.
"""
import base64
import hashlib
import hmac
import time

from app.core.config import settings

UNSUB_SECRET = settings.UNSUB_SECRET
if not UNSUB_SECRET:
    raise RuntimeError("UNSUB_SECRET must be set in environment — see .env.example")

# Token validity window: 90 days
TOKEN_MAX_AGE_SECONDS = 90 * 24 * 3600

# Transition: accept V1 (no-timestamp) tokens until this date.
# After this, only V2 tokens are valid. Set to ~90 days from deployment.
_V1_GRACE_PERIOD_END = 1781330400  # 2026-06-13T00:00:00Z — 90 days from deploy


def generate_unsub_token(user_id: str) -> str:
    """Generate a V2 HMAC-signed token with embedded timestamp."""
    ts = str(int(time.time()))
    payload = f"{user_id}:{ts}"
    sig = hmac.new(UNSUB_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(f"{payload}:{sig.hex()}".encode()).decode()


def validate_unsub_token(token: str) -> str | None:
    """Validate an unsubscribe token. Returns user_id if valid, None otherwise.

    Accepts both V2 (timestamped) and V1 (legacy) tokens during the
    grace period.
    """
    try:
        decoded = base64.urlsafe_b64decode(token).decode()
        parts = decoded.split(":")

        if len(parts) == 3:
            # V2 format: user_id:timestamp:hmac_hex
            user_id, ts_str, sig_hex = parts
            payload = f"{user_id}:{ts_str}"
            expected = hmac.new(
                UNSUB_SECRET.encode(), payload.encode(), hashlib.sha256,
            ).digest().hex()
            if not hmac.compare_digest(sig_hex, expected):
                return None
            # Check expiry
            token_time = int(ts_str)
            if time.time() - token_time > TOKEN_MAX_AGE_SECONDS:
                return None
            return user_id

        elif len(parts) == 2:
            # V1 format: user_id:hmac_hex (legacy, no timestamp)
            user_id, sig_hex = parts
            expected = hmac.new(
                UNSUB_SECRET.encode(), user_id.encode(), hashlib.sha256,
            ).digest().hex()
            if not hmac.compare_digest(sig_hex, expected):
                return None
            # Accept V1 only during grace period
            if time.time() > _V1_GRACE_PERIOD_END:
                return None
            return user_id

    except Exception:
        pass
    return None
