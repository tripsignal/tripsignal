"""Subject line and preview (preheader) builders for all email types.

MATCH ALERT SUBJECT PRIORITY (from Email Intelligence Spec):
  1. Module 1: top 25%    -> "$879 to Mexico. Cheapest we've seen in 6 weeks."
  2. Delta > 8%           -> "$879 — down $168 since yesterday."
  3. Module 2: vs trend   -> "Prices rising for 3 weeks. This one bucks the trend."
  4. Default instant      -> "Your Mexico signal — prices are moving."

WEEKLY DIGEST:
  "7 deals this week. Here's the one worth looking at."

RE-ENGAGEMENT:
  "While you were away, your signal found 23 deals."

Max 60 characters enforced.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.email_orchestrator import EmailType


def _truncate(s: str, max_len: int = 60) -> str:
    """Truncate subject to max_len characters."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "\u2026"


# ── Match alert (intelligence-driven priority) ─────────────────────────────

def build_match_subject(context: dict) -> str:
    """Build subject line for MATCH_ALERT using intelligence priority.

    For consolidated multi-signal emails, adapts subject to show the most
    compelling event across all signals.
    """
    signals_with_activity = context.get("signals_with_activity")
    activity_count = context.get("signals_with_activity_count", 1)

    # Multi-signal subject — lead with the primary signal's best stat
    # but mention multi-signal context
    destination = context.get("destination", "your destination")
    is_new_low = context.get("new_low", False)
    is_top_25 = context.get("is_top_25", False)
    pct_drop = context.get("pct_drop", 0)
    best_price_cents = context.get("best_price_cents")
    best_price_delta = context.get("best_price_delta", 0)
    trend_direction = context.get("trend_direction", "stable")
    trend_weeks = context.get("trend_weeks", 0)
    days_monitoring = context.get("days_monitoring", 0)

    price = _fmt_price(best_price_cents)

    # ── Notable event subjects (priority order) ──

    # New all-time low
    if is_new_low and price:
        return _truncate(f"New low: {price} to {destination}")

    # Significant price drop (>= 10%)
    if pct_drop and pct_drop >= 10 and price:
        if best_price_delta:
            delta_str = _fmt_price(abs(best_price_delta))
            return _truncate(f"{price} to {destination} \u2014 down {delta_str}")
        return _truncate(f"{price} to {destination} \u2014 down {pct_drop}%")

    # Top 25% cheapest
    if is_top_25 and price and days_monitoring > 7:
        weeks = max(1, days_monitoring // 7)
        if weeks == 1:
            return _truncate(f"{price} to {destination}. Lowest this week.")
        return _truncate(f"{price} to {destination}. Lowest in {weeks} weeks.")

    # Against-trend deal
    if trend_direction == "up" and trend_weeks >= 2 and pct_drop and pct_drop > 0:
        return _truncate(f"Prices rising for {trend_weeks} weeks. This one bucks the trend.")

    # ── Multi-signal default ──
    if activity_count > 1 and price:
        return _truncate(f"New deals on {activity_count} signals \u2014 from {price}")

    # ── Single-signal default ──
    if price:
        return _truncate(f"New deal: {price} to {destination}")

    # Fallback
    return _truncate(f"New deals found on your {destination} signal")


def build_match_preview(context: dict) -> str:
    """Build preview/preheader for MATCH_ALERT."""
    deals = context.get("deals", [])
    best = deals[0] if deals else {}
    price = _fmt_price(best.get("price_cents"))
    hotel = best.get("hotel_name", "")
    nights = best.get("duration_nights", 7)
    depart = best.get("depart_date", "")
    intel_sentence = context.get("intel_sentence", "")

    parts = []
    if hotel:
        parts.append(hotel)
    if depart:
        parts.append(f"{'Mar' if not depart else depart}")
    if nights:
        parts.append(f"{nights} nights")

    detail = " \u00b7 ".join(parts) if parts else ""

    if intel_sentence:
        if detail:
            return f"{detail}. {intel_sentence}"
        return intel_sentence

    if price and hotel:
        return f"{price}/person at {hotel} \u00b7 {nights} nights"
    if price:
        return f"{price}/person \u00b7 {nights} nights"
    return detail or "New deals found"


# ── Weekly digest subject/preview ──────────────────────────────────────────

def build_digest_subject(context: dict) -> str:
    """Build subject for WEEKLY_DIGEST."""
    deal_count = context.get("deal_count", 0)
    destination = context.get("destination", "your signal")

    if deal_count and deal_count > 1:
        return _truncate(f"{deal_count} deals this week. Here's the one worth looking at.")

    if deal_count == 1:
        return _truncate(f"Your {destination} signal this week \u2014 1 deal found.")

    return _truncate(f"Your {destination} signal this week \u2014 prices are moving.")


def build_digest_preview(context: dict) -> str:
    """Build preview for WEEKLY_DIGEST."""
    best_price_cents = context.get("best_price_cents")
    trend_direction = context.get("trend_direction", "stable")
    trend_weeks = context.get("trend_weeks", 0)

    price = _fmt_price(best_price_cents)
    parts = []
    if price:
        parts.append(f"Best deal: {price}")
    if trend_direction == "down" and trend_weeks >= 2:
        parts.append(f"Prices dropped for the {_ordinal(trend_weeks)} week in a row")
    elif trend_direction == "up" and trend_weeks >= 2:
        parts.append(f"Prices rising for {trend_weeks} weeks")

    return ". ".join(parts) if parts else "This week's deals"


# ── Re-engagement subject/preview ─────────────────────────────────────────

def build_reengagement_subject(context: dict) -> str:
    """Build subject for RE_ENGAGEMENT / INACTIVE_REENGAGEMENT."""
    total_deals = context.get("total_deals_found", 0)
    if total_deals and total_deals > 0:
        return _truncate(f"While you were away, your signal found {total_deals} deals.")
    return "Your Trip Signal signals are still running"


def build_reengagement_preview(context: dict) -> str:
    """Build preview for RE_ENGAGEMENT."""
    best_missed_price = context.get("best_missed_price_cents")
    if best_missed_price:
        price = _fmt_price(best_missed_price)
        return f"The cheapest was {price} \u2014 here's what it looked like."
    return "Your signals are still monitoring prices for you."


# ── Generic builders (all email types) ───────────────────────────────────

def build_subject(email_type: "EmailType", context: dict) -> str:
    """Build the subject line for any email type."""
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
    """Build the preheader/preview text for any email type."""
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


def _ordinal(n: int) -> str:
    """Return ordinal string: 1st, 2nd, 3rd, etc."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd'][min(n % 10, 4)] if n % 10 < 4 else 'th'}"
