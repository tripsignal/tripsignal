"""Reusable email sending via Resend API."""
from __future__ import annotations

import logging
import os
import threading
import time

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = "Trip Signal <hello@tripsignal.ca>"

# Rate limiter: Resend allows 2 requests/sec. We enforce 0.6s between calls.
_send_lock = threading.Lock()
_last_send_time = 0.0
_MIN_INTERVAL = 0.6  # seconds between API calls


def send_email(to: str, subject: str, html: str) -> str | None:
    """Send a transactional email via Resend.

    Returns the provider message_id string on success, None on failure.
    Truthiness is preserved: ``if send_email(...):`` still works.

    When EMAIL_DRY_RUN is enabled, skips the actual API call but returns
    a synthetic dry-run ID so callers can proceed normally.
    """
    global _last_send_time

    # ── Dry-run mode: skip provider, log what would be sent ──────────
    if settings.EMAIL_DRY_RUN:
        logger.info(
            "[DRY_RUN] Would send email to %s — subject=%s (body %d chars)",
            to, subject, len(html),
        )
        return "dry_run"

    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email to %s", to)
        return None

    # Throttle to stay under Resend's 2 req/s limit
    with _send_lock:
        elapsed = time.monotonic() - _last_send_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_send_time = time.monotonic()

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            message_id = ""
            try:
                message_id = resp.json().get("id", "")
            except Exception:
                pass
            logger.info("Email sent to %s — subject=%s id=%s", to, subject, message_id)
            return message_id or "sent"
        else:
            logger.error(
                "Resend API error %s for %s: %s",
                resp.status_code, to, resp.text[:300],
            )
            return None
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return None
