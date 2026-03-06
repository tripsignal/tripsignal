"""
Email template registry.

Each template is a function(user, context) -> (subject, html).
The render_template() dispatcher calls the right one, but checks the DB
for admin overrides first.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.user import User
    from sqlalchemy.orm import Session

from app.services.email_orchestrator import EmailType
from app.services.email_templates.base import wrap
from app.services.email_templates.subject_preview import (  # noqa: F401
    build_subject,
    build_preview,
    build_match_subject,
    build_match_preview,
)
from app.services.email_templates.templates import (
    welcome,
    first_signal,
    no_signal_reminder,
    match_alert,
    major_drop_alert,
    trial_expiring_soon,
    trial_expired_upsell,
    pro_activated,
    payment_failed,
    payment_failed_reminder,
    subscription_canceled,
    account_deleted_free,
    account_deleted_pro,
    no_match_update,
    inactive_reengagement,
    weekly_digest,
)

logger = logging.getLogger(__name__)

_REGISTRY: dict[EmailType, callable] = {
    EmailType.WELCOME: welcome,
    EmailType.FIRST_SIGNAL: first_signal,
    EmailType.NO_SIGNAL_REMINDER: no_signal_reminder,
    EmailType.MATCH_ALERT: match_alert,
    EmailType.MAJOR_DROP_ALERT: major_drop_alert,
    EmailType.TRIAL_EXPIRING_SOON: trial_expiring_soon,
    EmailType.TRIAL_EXPIRED_UPSELL: trial_expired_upsell,
    EmailType.PRO_ACTIVATED: pro_activated,
    EmailType.PAYMENT_FAILED: payment_failed,
    EmailType.PAYMENT_FAILED_REMINDER: payment_failed_reminder,
    EmailType.SUBSCRIPTION_CANCELED: subscription_canceled,
    EmailType.ACCOUNT_DELETED_FREE: account_deleted_free,
    EmailType.ACCOUNT_DELETED_PRO: account_deleted_pro,
    EmailType.NO_MATCH_UPDATE: no_match_update,
    EmailType.INACTIVE_REENGAGEMENT: inactive_reengagement,
    EmailType.WEEKLY_DIGEST: weekly_digest,
}

# Variables available per email type (for admin UI hints + interpolation)
TEMPLATE_VARIABLES: dict[EmailType, list[str]] = {
    EmailType.WELCOME: [],
    EmailType.FIRST_SIGNAL: ["signal_name", "signal_id"],
    EmailType.NO_SIGNAL_REMINDER: [],
    EmailType.MATCH_ALERT: [
        "signal_name", "route", "deal_count", "new_low", "pct_drop",
        "deals", "intel_sentence", "days_monitoring", "is_top_25",
        "percentile_rank", "trend_direction", "trend_weeks",
        "best_price_delta", "best_price_cents", "destination",
        # Multi-signal consolidated fields
        "active_signal_count", "signals_with_activity_count",
        "quiet_signal_count", "signals_with_activity", "quiet_signals",
    ],
    EmailType.MAJOR_DROP_ALERT: [
        "signal_name", "route", "hotel_name", "star_rating", "drop_amount",
        "drop_pct", "new_price_cents", "duration_nights", "depart_date", "deeplink_url",
    ],
    EmailType.TRIAL_EXPIRING_SOON: ["days_left"],
    EmailType.TRIAL_EXPIRED_UPSELL: [],
    EmailType.PRO_ACTIVATED: [],
    EmailType.PAYMENT_FAILED: ["invoice_id"],
    EmailType.PAYMENT_FAILED_REMINDER: ["reminder_num", "invoice_id"],
    EmailType.SUBSCRIPTION_CANCELED: ["period_end", "subscription_id"],
    EmailType.ACCOUNT_DELETED_FREE: [],
    EmailType.ACCOUNT_DELETED_PRO: [],
    EmailType.NO_MATCH_UPDATE: ["signal_name", "signal_id", "days_active"],
    EmailType.INACTIVE_REENGAGEMENT: [
        "days_inactive", "total_deals_found", "best_missed_deal",
        "best_missed_price_cents", "min_price_ever_cents", "max_price_ever_cents",
        "trend_direction", "current_best_deal",
    ],
    EmailType.WEEKLY_DIGEST: [
        "deal_count", "deals", "trend_direction", "trend_weeks",
        "best_value_nights", "best_value_pct_saving", "total_matches",
        "days_monitoring", "signal_name", "route", "destination", "best_price_cents",
    ],
}


def render_template(
    email_type: EmailType,
    *,
    user: "User",
    context: dict,
    db: "Session | None" = None,
) -> tuple[str, str]:
    """Return (subject, html) for the given email type.

    If a DB override exists (checked via *db*), use it.
    Otherwise fall back to the Python default template.
    """
    # Try DB override first
    if db is not None:
        try:
            override = _get_override(db, email_type)
            if override and (override.subject or override.body_html):
                subject, body = _render_override(override, email_type, user, context)
                return subject, wrap(body, unsub_url=context.get("_unsub_url", ""), user_email=getattr(user, "email", "") or "")
        except Exception:
            logger.exception("Error loading template override for %s, falling back to default", email_type.value)

    # Fall back to Python default
    fn = _REGISTRY.get(email_type)
    if not fn:
        raise ValueError(f"No template registered for {email_type}")
    return fn(user=user, context=context)


def get_default_body(email_type: EmailType) -> tuple[str, str]:
    """Return (default_subject, default_body_html) from the Python template.

    The body is the inner HTML only (without the wrap shell), suitable for
    loading into the admin editor as a starting point.
    """

    class _FakeUser:
        email = "user@example.com"
        plan_type = "pro"
        plan_status = "active"
        clerk_id = "preview"

    # Get sample context
    sample_ctx = _sample_context_for_type(email_type)
    fn = _REGISTRY.get(email_type)
    if not fn:
        raise ValueError(f"No template registered for {email_type}")

    subject, full_html = fn(user=_FakeUser(), context=sample_ctx)

    # Extract body between the logo div and the footer hr
    body = _extract_body(full_html)
    return subject, body


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_override(db: "Session", email_type: EmailType):
    """Load an override row from the DB, or None."""
    from sqlalchemy import select
    from app.db.models.email_template_override import EmailTemplateOverride

    return db.execute(
        select(EmailTemplateOverride).where(
            EmailTemplateOverride.email_type == email_type.value
        )
    ).scalar_one_or_none()


def _render_override(override, email_type: EmailType, user: "User", context: dict) -> tuple[str, str]:
    """Render a DB override, interpolating variables. Returns (subject, body_html)."""
    # Get default subject as fallback
    default_subject, _ = get_default_body(email_type)

    subject = override.subject if override.subject else default_subject
    subject = _interpolate(subject, user, context)

    if override.body_html:
        body = _interpolate(override.body_html, user, context)
    else:
        # No body override — use Python default
        fn = _REGISTRY.get(email_type)
        if fn:
            _, full_html = fn(user=user, context=context)
            body = _extract_body(full_html)
        else:
            body = ""

    return subject, body


def _interpolate(template_str: str, user: "User", context: dict) -> str:
    """Safely interpolate {variable_name} placeholders in a template string.

    Supports user fields (email, plan_type) and all context variables.
    Unknown variables are left as-is (e.g. {unknown} stays as {unknown}).
    """
    # Build variable map from user + context
    var_map = defaultdict(lambda: "")
    # User fields
    for attr in ("email", "plan_type", "plan_status"):
        if hasattr(user, attr):
            var_map[attr] = getattr(user, attr, "") or ""
    # Context variables
    for k, v in context.items():
        var_map[k] = str(v) if v is not None else ""

    # Replace {key} patterns, leaving unknown ones alone
    def replacer(match):
        key = match.group(1)
        if key in var_map:
            return var_map[key]
        return match.group(0)  # leave {unknown} as-is

    return re.sub(r"\{(\w+)\}", replacer, template_str)


def _extract_body(full_html: str) -> str:
    """Extract the inner body content from a fully wrapped email HTML.

    Strips the <html>/<head>/<body> tags, the logo header div, preheader,
    and the footer, returning just the editable content between.
    """
    body = full_html

    # Find the logo div by its unique style attribute and strip everything
    # up to and including its closing </div>
    logo_start = body.find('<div style="margin-bottom:24px;">')
    if logo_start != -1:
        logo_close = body.find("</div>", logo_start)
        if logo_close != -1:
            body = body[logo_close + len("</div>"):]
    else:
        # Fallback: strip everything before the first </div>
        first_close = body.find("</div>")
        if first_close != -1:
            body = body[first_close + len("</div>"):]

    # Remove everything from the <hr> footer onward
    hr_pos = body.find("<hr")
    if hr_pos != -1:
        body = body[:hr_pos]

    # Remove closing body/html tags if somehow still present
    body = body.replace("</body>", "").replace("</html>", "")

    return body.strip()


def _sample_context_for_type(email_type: EmailType) -> dict:
    """Sample context for rendering defaults in the admin editor."""
    samples = {
        EmailType.WELCOME: {},
        EmailType.FIRST_SIGNAL: {"signal_name": "Mexico Beach Getaway", "signal_id": "test-signal-123"},
        EmailType.NO_SIGNAL_REMINDER: {},
        EmailType.MATCH_ALERT: {"signal_name": "Caribbean Winter Escape", "match_count": 3, "best_price": "$899"},
        EmailType.MAJOR_DROP_ALERT: {
            "signal_name": "Caribbean Winter Escape", "drop_amount": "$250",
            "hotel_name": "Riu Palace Riviera Maya", "new_price": "$749",
        },
        EmailType.TRIAL_EXPIRING_SOON: {"days_left": 3},
        EmailType.TRIAL_EXPIRED_UPSELL: {},
        EmailType.PRO_ACTIVATED: {},
        EmailType.PAYMENT_FAILED: {"invoice_id": "inv_test_123"},
        EmailType.PAYMENT_FAILED_REMINDER: {"reminder_num": 1, "invoice_id": "inv_test_123"},
        EmailType.SUBSCRIPTION_CANCELED: {"period_end": "March 15, 2026", "subscription_id": "sub_test_123"},
        EmailType.ACCOUNT_DELETED_FREE: {},
        EmailType.ACCOUNT_DELETED_PRO: {},
        EmailType.NO_MATCH_UPDATE: {"signal_name": "Europe Summer Trip", "signal_id": "test-signal-456", "days_active": 14},
        EmailType.INACTIVE_REENGAGEMENT: {
            "days_inactive": 21, "total_deals_found": 23,
            "best_missed_deal": {"price_cents": 87900, "hotel_name": "Riu Palace", "duration_nights": 7, "depart_date": "2026-03-15"},
            "best_missed_price_cents": 87900,
            "min_price_ever_cents": 79900, "max_price_ever_cents": 149900,
            "trend_direction": "down", "current_best_deal": {"price_cents": 92900, "hotel_name": "Sandos Caracol", "duration_nights": 7},
        },
        EmailType.WEEKLY_DIGEST: {
            "deal_count": 7, "signal_name": "Caribbean Winter Escape", "destination": "Mexico",
            "deals": [
                {"hotel_name": "Riu Palace", "star_rating": 4.5, "price_cents": 87900, "duration_nights": 7, "depart_date": "2026-03-20"},
            ],
            "trend_direction": "down", "trend_weeks": 3,
            "best_value_nights": 7, "best_value_pct_saving": 15,
            "total_matches": 42, "days_monitoring": 30, "route": "YQR → Cancun",
            "best_price_cents": 87900,
        },
    }
    return samples.get(email_type, {})
