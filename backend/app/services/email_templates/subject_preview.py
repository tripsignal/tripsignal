"""Subject line and preview (preheader) builders for all email types.

──────────────────────────────────────────────────────────────────────
LOCKED MATCH ALERT SUBJECT PRIORITY:
  1. new_low                        → "New low: {route}"
  2. pct_drop >= alert_threshold    → "Price drop: {route}"
  3. deal_count == 1                → "New deal: {route}"
  4. else                           → "New deals found ({deal_count}): {route}"

LOCKED MATCH ALERT PREVIEW PRIORITY:
  1. new_low                        → "All-time low {price}/person at {hotel}"
  2. pct_drop >= alert_threshold    → "Down {pct}% — {price}/person at {hotel}"
  3. single                         → "{price}/person · {hotel} · {nights} nights"
  4. multi                          → "{count} deals from {price}/person"

alert_threshold defaults to 10 (%) and is configurable per user.
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.email_orchestrator import EmailType


# ── Match alert (locked priority) ────────────────────────────────────────────

def build_match_subject(context: dict) -> str:
    """Build subject line for MATCH_ALERT using locked priority."""
    route = context.get("route", "your signal")
    new_low = context.get("new_low", False)
    pct_drop = context.get("pct_drop", 0)
    deal_count = context.get("deal_count", 1)
    threshold = context.get("alert_threshold", 10)

    # Priority 1: All-time low
    if new_low:
        return f"New low: {route}"

    # Priority 2: Significant price drop (≥ user threshold, default 10%)
    if pct_drop and pct_drop >= threshold:
        return f"Price drop: {route}"

    # Priority 3: Single deal
    if deal_count == 1:
        return f"New deal: {route}"

    # Priority 4: Multiple deals
    return f"New deals found ({deal_count}): {route}"


def build_match_preview(context: dict) -> str:
    """Build preview/preheader for MATCH_ALERT using locked priority."""
    new_low = context.get("new_low", False)
    pct_drop = context.get("pct_drop", 0)
    deal_count = context.get("deal_count", 1)
    deals = context.get("deals", [])
    threshold = context.get("alert_threshold", 10)

    best = deals[0] if deals else {}
    price = _fmt_price(best.get("price_cents"))
    hotel = best.get("hotel_name", "")
    nights = best.get("duration_nights", 7)

    # Priority 1: All-time low
    if new_low:
        return f"All-time low {price}/person at {hotel}" if hotel else f"All-time low {price}/person"

    # Priority 2: Significant price drop (≥ user threshold)
    if pct_drop and pct_drop >= threshold:
        base = f"Down {pct_drop}% \u2014 {price}/person"
        return f"{base} at {hotel}" if hotel else base

    # Priority 3: Single deal
    if deal_count == 1:
        parts = [f"{price}/person"]
        if hotel:
            parts.append(hotel)
        parts.append(f"{nights} nights")
        return " \u00b7 ".join(parts)

    # Priority 4: Multiple deals
    return f"{deal_count} deals from {price}/person"


# ── Generic builders (all email types) ───────────────────────────────────────

def build_subject(email_type: "EmailType", context: dict) -> str:
    """Build the subject line for any email type.

    For MATCH_ALERT, delegates to the locked priority logic.
    """
    from app.services.email_orchestrator import EmailType as ET

    if email_type == ET.MATCH_ALERT:
        return build_match_subject(context)

    # Special: trial expiring needs plural logic
    if email_type == ET.TRIAL_EXPIRING_SOON:
        days = context.get("days_left", 3)
        s = "" if days == 1 else "s"
        return f"Your Trip Signal trial ends in {days} day{s}"

    _STATIC: dict[ET, str] = {
        ET.WELCOME: "Welcome to Trip Signal",
        ET.NO_SIGNAL_REMINDER: "Don\u2019t forget to create your first signal",
        ET.TRIAL_EXPIRED_UPSELL: "Your Trip Signal trial has ended \u2014 keep your signals running",
        ET.PRO_ACTIVATED: "Welcome to Trip Signal Pro",
        ET.PAYMENT_FAILED: "Action needed: your Trip Signal payment failed",
        ET.PAYMENT_FAILED_REMINDER: "Reminder: your Trip Signal payment still needs attention",
        ET.ACCOUNT_DELETED_FREE: "Your Trip Signal account has been deleted",
        ET.ACCOUNT_DELETED_PRO: "Account deleted \u2014 subscription canceled",
        ET.INACTIVE_REENGAGEMENT: "Your Trip Signal signals are still running",
    }
    if email_type in _STATIC:
        return _STATIC[email_type]

    # Parameterized subjects
    if email_type == ET.FIRST_SIGNAL:
        name = context.get("signal_name", "your signal")
        return f'Your signal "{name}" is now active'

    if email_type == ET.MAJOR_DROP_ALERT:
        drop = context.get("drop_amount", "")
        name = context.get("signal_name", "your signal")
        return f"Price dropped {drop} on {name}"

    if email_type == ET.SUBSCRIPTION_CANCELED:
        return "Your Trip Signal Pro subscription has been canceled"

    if email_type == ET.NO_MATCH_UPDATE:
        name = context.get("signal_name", "your signal")
        return f"Update on {name} \u2014 no matches yet"

    # Fallback
    return email_type.value


def build_preview(email_type: "EmailType", context: dict) -> str:
    """Build the preheader/preview text for any email type.

    For MATCH_ALERT, delegates to the locked priority logic.
    """
    from app.services.email_orchestrator import EmailType as ET

    if email_type == ET.MATCH_ALERT:
        return build_match_preview(context)

    _TEMPLATES: dict[ET, str] = {
        ET.WELCOME: "Start monitoring vacation deals",
        ET.FIRST_SIGNAL: "Monitoring started for {signal_name}",
        ET.NO_SIGNAL_REMINDER: "Set up deal monitoring in 30 seconds",
        ET.MAJOR_DROP_ALERT: "{drop_amount} price drop",
        ET.TRIAL_EXPIRING_SOON: "Trial ends in {days_left} days",
        ET.TRIAL_EXPIRED_UPSELL: "Your signals are paused",
        ET.PRO_ACTIVATED: "Pro features are now active",
        ET.PAYMENT_FAILED: "Please update your payment method",
        ET.PAYMENT_FAILED_REMINDER: "Payment update needed",
        ET.SUBSCRIPTION_CANCELED: "Pro canceled",
        ET.ACCOUNT_DELETED_FREE: "Account deleted",
        ET.ACCOUNT_DELETED_PRO: "Account deleted, subscription canceled",
        ET.NO_MATCH_UPDATE: "No matches after {days_active} days",
        ET.INACTIVE_REENGAGEMENT: "We\u2019re still watching prices for you",
    }

    template = _TEMPLATES.get(email_type, "")
    if not template:
        return ""

    # Safe interpolation: missing keys become empty string
    safe = defaultdict(str, {k: str(v) for k, v in context.items() if v is not None})
    return template.format_map(safe)


# ── Private ──────────────────────────────────────────────────────────────────

def _fmt_price(price_cents: int | None) -> str:
    if price_cents is None:
        return ""
    return f"${price_cents // 100:,}"
