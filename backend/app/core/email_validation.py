"""Email address validation for user creation."""
from __future__ import annotations

from email_validator import validate_email, EmailNotValidError


def is_valid_email(email: str) -> bool:
    """Return True if *email* is a deliverable-looking address (has a valid
    domain with DNS, not just syntactically correct).

    Uses the same ``email-validator`` library already used by Pydantic's
    ``EmailStr``.  Returns False (never raises) so callers can decide how
    to handle invalid addresses.
    """
    if not email or not email.strip():
        return False
    try:
        validate_email(email, check_deliverability=True)
        return True
    except EmailNotValidError:
        return False
