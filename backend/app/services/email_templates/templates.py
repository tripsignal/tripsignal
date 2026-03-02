"""
All 15 lifecycle email templates.

Each function signature: (user: User, context: dict) -> (subject: str, html: str)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.email_templates.base import (
    wrap, button, para, heading, info_box,
    stars_html, format_price, pricing_disclaimer, new_low_banner, price_drop_banner,
)

if TYPE_CHECKING:
    from app.db.models.user import User


def _unsub(context: dict) -> str:
    """Extract the unsubscribe URL from context."""
    return context.get("_unsub_url", "")


# ═══════════════════════════════════════════════════════════════════════════════
# A) ONBOARDING
# ═══════════════════════════════════════════════════════════════════════════════

def welcome(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Welcome to Trip Signal"
    body = (
        heading("Welcome to Trip Signal")
        + para(
            "Thanks for signing up. Trip Signal monitors all-inclusive vacation prices "
            "across Canadian travel sites so you don't have to."
        )
        + para(
            "Create your first signal to tell us what you're looking for — we'll check "
            "prices multiple times a day and email you when deals match."
        )
        + button("Create your first signal", "https://tripsignal.ca/signals/new")
        + para("No rush — your account is ready whenever you are.")
    )
    return subject, wrap(body, preheader="Start monitoring vacation deals", unsub_url=_unsub(context))


def first_signal(*, user: "User", context: dict) -> tuple[str, str]:
    signal_name = context.get("signal_name", "your signal")
    subject = f'Your signal "{signal_name}" is now active'
    body = (
        heading("Your signal is live")
        + para(
            f"We've started monitoring deals for <strong>{signal_name}</strong>. "
            "Prices are checked multiple times a day across our travel provider network."
        )
        + para(
            "When a deal matches your criteria, you'll receive an alert with pricing "
            "details and a direct link to book."
        )
        + button("View your signal", "https://tripsignal.ca/signals")
        + para(
            "Most users see their first match within 24–48 hours, depending on destination "
            "and travel dates."
        )
    )
    return subject, wrap(body, preheader=f"Monitoring started for {signal_name}", unsub_url=_unsub(context))


def no_signal_reminder(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Don't forget to create your first signal"
    body = (
        heading("Your account is set up — one step to go")
        + para(
            "You signed up for Trip Signal but haven't created a signal yet. "
            "A signal tells us what kind of vacation you're looking for so we can "
            "watch prices for you."
        )
        + para("It takes about 30 seconds:")
        + info_box(
            '<p style="margin:0;font-size:14px;color:#333;">'
            "1. Pick your departure airport<br>"
            "2. Choose a destination region<br>"
            "3. Set your budget and dates<br>"
            "</p>"
        )
        + button("Create a signal", "https://tripsignal.ca/signals/new")
    )
    return subject, wrap(
        body,
        preheader="Set up deal monitoring in 30 seconds",
        footer_note=(
            'You\'re receiving this because you signed up for Trip Signal.<br>'
            '<a href="https://tripsignal.ca" style="color:#999;">tripsignal.ca</a>'
        ),
        unsub_url=_unsub(context),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# B) ALERTS
# ═══════════════════════════════════════════════════════════════════════════════

def match_alert(*, user: "User", context: dict) -> tuple[str, str]:
    """Match alert — single or multi-deal rendering.

    Context fields:
        signal_name: str
        route: str — e.g. "Regina (YQR) → Cancun"
        deal_count: int
        new_low: bool — all-time low for this signal
        pct_drop: int — percentage drop from previous, 0 if none
        deals: list[dict] — each with hotel_name, star_rating, price_cents,
            duration_nights, depart_date, deeplink_url
    """
    from app.services.email_templates.subject_preview import (
        build_match_subject, build_match_preview,
    )

    signal_name = context.get("signal_name", "your signal")
    deal_count = context.get("deal_count", 1)
    is_new_low = context.get("new_low", False)
    pct_drop = context.get("pct_drop", 0)
    deals = context.get("deals", [])

    subject = build_match_subject(context)
    preview = build_match_preview(context)

    parts: list[str] = []

    # Conditional banners
    if is_new_low:
        parts.append(new_low_banner())
    elif pct_drop and pct_drop >= 10:
        parts.append(price_drop_banner(pct_drop))

    if deal_count == 1 and deals:
        # ── Single deal: prominent card ──
        deal = deals[0]
        parts.append(heading(f"New deal for {signal_name}"))
        parts.append(_single_deal_card(deal, context.get("route", "")))
        deeplink = deal.get("deeplink_url", "https://tripsignal.ca/signals")
        parts.append(button("View deal →", deeplink))
    else:
        # ── Multi-deal: list of rows ──
        s = "s" if deal_count != 1 else ""
        parts.append(heading(f"{deal_count} new deal{s} for {signal_name}"))
        if deals:
            parts.append(para(
                f"We found {deal_count} deals matching your signal. "
                "Here are the best prices:"
            ))
            parts.append(_multi_deal_list(deals))
        else:
            # Fallback for legacy context without deal objects
            best_price = context.get("best_price", "")
            price_text = f" The best price is <strong>{best_price}</strong>." if best_price else ""
            parts.append(para(f"We found {deal_count} deals matching your signal.{price_text}"))
        parts.append(button("View all deals →", "https://tripsignal.ca/signals"))

    parts.append(para(
        '<span style="font-size:13px;color:#666;">'
        "Prices can change quickly — check availability soon."
        "</span>"
    ))
    parts.append(pricing_disclaimer())

    body = "".join(parts)
    return subject, wrap(body, preheader=preview, unsub_url=_unsub(context))


def major_drop_alert(*, user: "User", context: dict) -> tuple[str, str]:
    """Major price drop on a deal matching a signal.

    Context fields:
        signal_name, route, hotel_name, star_rating, drop_amount (str),
        drop_pct (int), new_price_cents, duration_nights, depart_date,
        deeplink_url
    """
    signal_name = context.get("signal_name", "your signal")
    route = context.get("route", signal_name)
    hotel_name = context.get("hotel_name", "a deal")
    star_rating = context.get("star_rating")
    drop_amount = context.get("drop_amount", "")
    drop_pct = context.get("drop_pct", 0)
    new_price_cents = context.get("new_price_cents")
    duration = context.get("duration_nights", 7)
    depart = context.get("depart_date", "")
    deeplink = context.get("deeplink_url", "https://tripsignal.ca/signals")

    subject = f"Price dropped {drop_amount} on {signal_name}"

    stars = stars_html(star_rating)
    price = format_price(new_price_cents)

    dates_info = f"{duration} nights"
    if depart:
        dates_info += f" · {depart}"

    # Deal card
    card_inner = (
        f'<p style="margin:0 0 4px;font-size:16px;font-weight:600;color:#111;">'
        f'{hotel_name}{stars}</p>'
        f'<p style="margin:0 0 16px;font-size:13px;color:#666;">'
        f'{route} · {dates_info}</p>'
    )
    if price:
        card_inner += (
            f'<p style="margin:0 0 4px;font-size:28px;font-weight:700;color:#111;">'
            f'{price}</p>'
            '<p style="margin:0;font-size:12px;color:#666;">'
            'per person (based on double occupancy)</p>'
        )
    deal_card = (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        f'<div style="padding:20px;">{card_inner}</div></div>'
    )

    drop_info = f" — down <strong>{drop_amount}</strong>"
    if drop_pct:
        drop_info += f" ({drop_pct}%)"

    body = (
        (price_drop_banner(drop_pct) if drop_pct and drop_pct >= 10 else "")
        + heading("Significant price drop")
        + para(
            f"<strong>{hotel_name}</strong> on your {signal_name} signal "
            f"just dropped{drop_info}."
        )
        + deal_card
        + button("View deal →", deeplink)
        + para(
            '<span style="font-size:13px;color:#666;">'
            "Large drops like this don&#39;t last long at most providers."
            "</span>"
        )
        + pricing_disclaimer()
    )
    return subject, wrap(body, preheader=f"{drop_amount} price drop", unsub_url=_unsub(context))


# ═══════════════════════════════════════════════════════════════════════════════
# C) TRIAL
# ═══════════════════════════════════════════════════════════════════════════════

def trial_expiring_soon(*, user: "User", context: dict) -> tuple[str, str]:
    days_left = context.get("days_left", 3)
    subject = f"Your Trip Signal trial ends in {days_left} day{'s' if days_left != 1 else ''}"
    body = (
        heading(f"Your trial ends in {days_left} day{'s' if days_left != 1 else ''}")
        + para(
            "When your trial ends, your signals will pause and you'll stop receiving "
            "deal alerts. Your settings and history will be saved."
        )
        + para(
            "Upgrade to Pro to keep monitoring — for less than a cup of coffee a month."
        )
        + info_box(
            '<p style="margin:0 0 8px;font-size:14px;font-weight:600;color:#1D4ED8;">Trip Signal Pro includes:</p>'
            '<ul style="margin:0;padding-left:20px;font-size:14px;color:#333;">'
            "<li>Up to 10 active signals</li>"
            "<li>Prices checked multiple times a day</li>"
            "<li>Email + SMS alerts</li>"
            "<li>Price drop tracking &amp; history</li>"
            "</ul>"
        )
        + button("Upgrade to Pro", "https://tripsignal.ca/signals")
    )
    return subject, wrap(
        body,
        preheader=f"Trial ends in {days_left} days",
        footer_note=(
            'You\'re receiving this because your Trip Signal free trial is ending soon.<br>'
            '<a href="https://tripsignal.ca" style="color:#999;">tripsignal.ca</a>'
        ),
        unsub_url=_unsub(context),
    )


def trial_expired_upsell(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Your Trip Signal trial has ended — keep your signals running"
    body = (
        heading("Your free trial has ended")
        + para(
            "Your signals have been paused, but your saved settings are still here. "
            "Upgrade to Pro to keep monitoring — for less than a cup of coffee a month."
        )
        + info_box(
            '<p style="margin:0 0 8px;font-size:14px;font-weight:600;color:#1D4ED8;">Trip Signal Pro includes:</p>'
            '<ul style="margin:0;padding-left:20px;font-size:14px;color:#333;">'
            "<li>Up to 10 active signals</li>"
            "<li>Prices checked multiple times a day</li>"
            "<li>Email + SMS alerts</li>"
            "<li>Price drop tracking &amp; history</li>"
            "</ul>"
        )
        + button("Upgrade to Pro →", "https://tripsignal.ca/signals")
    )
    return subject, wrap(
        body,
        preheader="Your signals are paused",
        footer_note=(
            'You\'re receiving this because your Trip Signal free trial ended.<br>'
            '<a href="https://tripsignal.ca" style="color:#999;">tripsignal.ca</a>'
        ),
        unsub_url=_unsub(context),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# D) BILLING
# ═══════════════════════════════════════════════════════════════════════════════

def pro_activated(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Welcome to Trip Signal Pro"
    body = (
        heading("You're on Pro")
        + para(
            "Thanks for upgrading. Your signals are now running with full Pro features:"
        )
        + info_box(
            '<ul style="margin:0;padding-left:20px;font-size:14px;color:#333;">'
            "<li>Up to 10 active signals</li>"
            "<li>Prices checked multiple times a day</li>"
            "<li>Email + SMS alerts</li>"
            "<li>Full price drop tracking &amp; history</li>"
            "</ul>"
        )
        + button("View your signals", "https://tripsignal.ca/signals")
        + para(
            "Manage your subscription anytime from "
            '<a href="https://tripsignal.ca/account/settings" style="color:#1D4ED8;">Account Settings</a>.'
        )
    )
    return subject, wrap(body, preheader="Pro features are now active", unsub_url=_unsub(context))


def payment_failed(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Action needed: your Trip Signal payment failed"
    body = (
        heading("Payment failed")
        + para(
            "We weren't able to process your latest payment. Your Pro access will "
            "continue for now, but please update your payment method to avoid interruption."
        )
        + button("Update payment method", "https://tripsignal.ca/account/settings")
        + para("If you've already resolved this, you can ignore this email.")
    )
    return subject, wrap(body, preheader="Please update your payment method", unsub_url=_unsub(context))


def payment_failed_reminder(*, user: "User", context: dict) -> tuple[str, str]:
    reminder_num = context.get("reminder_num", 1)
    subject = "Reminder: your Trip Signal payment still needs attention"
    body = (
        heading("Payment still outstanding")
        + para(
            "We still haven't been able to process your payment. If your payment method "
            "isn't updated soon, your Pro access may be suspended and your signals will pause."
        )
        + button("Update payment method", "https://tripsignal.ca/account/settings")
        + para("Need help? Reply to this email and we'll sort it out.")
    )
    return subject, wrap(body, preheader="Payment update needed", unsub_url=_unsub(context))


def subscription_canceled(*, user: "User", context: dict) -> tuple[str, str]:
    period_end = context.get("period_end", "")
    subject = "Your Trip Signal Pro subscription has been canceled"
    period_note = (
        f" You'll keep Pro access until <strong>{period_end}</strong>."
        if period_end else ""
    )
    body = (
        heading("Subscription canceled")
        + para(
            f"Your Pro subscription has been canceled.{period_note} "
            "After that, your signals will pause and your plan will revert to Free."
        )
        + para(
            "Your signal settings and deal history will be saved if you decide "
            "to resubscribe later."
        )
        + button("Resubscribe", "https://tripsignal.ca/signals")
        + para(
            "If this was a mistake, you can resubscribe anytime from your "
            '<a href="https://tripsignal.ca/account/settings" style="color:#1D4ED8;">Account Settings</a>.'
        )
    )
    return subject, wrap(body, preheader="Pro canceled" + (f" — access until {period_end}" if period_end else ""), unsub_url=_unsub(context))


# ═══════════════════════════════════════════════════════════════════════════════
# E) EXIT
# ═══════════════════════════════════════════════════════════════════════════════

def account_deleted_free(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Your Trip Signal account has been deleted"
    body = (
        heading("Your account has been deleted")
        + para(
            "Thank you for trying Trip Signal. Your account and all associated data "
            "have been removed. You will no longer receive deal alerts or notifications from us."
        )
        + para(
            "If this was a mistake or you'd like to come back, you can always create "
            'a new account at <a href="https://tripsignal.ca" style="color:#1D4ED8;">tripsignal.ca</a>.'
        )
    )
    return subject, wrap(body, preheader="Account deleted", unsub_url=_unsub(context))


def account_deleted_pro(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "Account deleted — subscription canceled"
    body = (
        heading("Your account has been deleted")
        + para(
            "Thank you for trying Trip Signal. Your account and all associated data "
            "have been removed. You will no longer receive deal alerts or notifications from us."
        )
        + para(
            "Your Pro subscription has been canceled and you will not be charged again."
        )
        + para(
            "If this was a mistake or you'd like to come back, you can always create "
            'a new account at <a href="https://tripsignal.ca" style="color:#1D4ED8;">tripsignal.ca</a>.'
        )
    )
    return subject, wrap(body, preheader="Account deleted, subscription canceled", unsub_url=_unsub(context))


# ═══════════════════════════════════════════════════════════════════════════════
# F) ENGAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def no_match_update(*, user: "User", context: dict) -> tuple[str, str]:
    signal_name = context.get("signal_name", "your signal")
    days_active = context.get("days_active", 14)
    subject = f"Update on {signal_name} — no matches yet"
    body = (
        heading(f"No matches yet for {signal_name}")
        + para(
            f"Your signal has been active for {days_active} days but hasn't matched "
            "any deals yet. This can happen with narrow criteria or destinations that "
            "aren't heavily promoted right now."
        )
        + para("A few things you can try:")
        + info_box(
            '<ul style="margin:0;padding-left:20px;font-size:14px;color:#333;">'
            "<li>Widen your budget range</li>"
            "<li>Add more departure airports</li>"
            "<li>Extend your travel date window</li>"
            "<li>Try a broader destination region</li>"
            "</ul>"
        )
        + button("Edit your signal", "https://tripsignal.ca/signals")
        + para("We're still checking throughout the day — if a deal appears, you'll hear from us.")
    )
    return subject, wrap(
        body,
        preheader=f"No matches after {days_active} days",
        footer_note=(
            'You\'re receiving this because you have an active signal on Trip Signal.<br>'
            '<a href="https://tripsignal.ca" style="color:#999;">tripsignal.ca</a>'
        ),
        unsub_url=_unsub(context),
    )


def inactive_reengagement(*, user: "User", context: dict) -> tuple[str, str]:
    days_inactive = context.get("days_inactive", 21)
    subject = "Your Trip Signal signals are still running"
    body = (
        heading("Your signals are still active")
        + para(
            f"It's been {days_inactive} days since you last checked in. Your signals "
            "are still running and we're monitoring prices on your behalf."
        )
        + para(
            "If your travel plans have changed, you can update or archive your signals "
            "anytime."
        )
        + button("Check your signals", "https://tripsignal.ca/signals")
    )
    return subject, wrap(
        body,
        preheader="We're still watching prices for you",
        footer_note=(
            'You\'re receiving this because you have active signals on Trip Signal.<br>'
            '<a href="https://tripsignal.ca" style="color:#999;">tripsignal.ca</a>'
        ),
        unsub_url=_unsub(context),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE: Deal card renderers (used by match_alert, major_drop_alert)
# ═══════════════════════════════════════════════════════════════════════════════

def _single_deal_card(deal: dict, route: str) -> str:
    """Render a prominent single-deal card with star rating and price."""
    hotel = deal.get("hotel_name", "Hotel")
    rating = deal.get("star_rating")
    price = format_price(deal.get("price_cents"))
    duration = deal.get("duration_nights", 7)
    depart = deal.get("depart_date", "")

    stars = stars_html(rating)

    dates_info = f"{duration} nights"
    if depart:
        dates_info += f" · {depart}"

    route_line = f"{route} · {dates_info}" if route else dates_info

    card_inner = (
        f'<p style="margin:0 0 4px;font-size:16px;font-weight:600;color:#111;">'
        f'{hotel}{stars}</p>'
        f'<p style="margin:0 0 16px;font-size:13px;color:#666;">{route_line}</p>'
    )
    if price:
        card_inner += (
            f'<p style="margin:0 0 4px;font-size:28px;font-weight:700;color:#111;">'
            f'{price}</p>'
            '<p style="margin:0;font-size:12px;color:#666;">'
            'per person (based on double occupancy)</p>'
        )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        f'<div style="padding:20px;">{card_inner}</div></div>'
    )


def _multi_deal_list(deals: list[dict]) -> str:
    """Render a stacked list of deal rows."""
    rows: list[str] = []
    for i, deal in enumerate(deals):
        hotel = deal.get("hotel_name", "Hotel")
        rating = deal.get("star_rating")
        price = format_price(deal.get("price_cents"))
        duration = deal.get("duration_nights", 7)
        depart = deal.get("depart_date", "")

        stars = stars_html(rating)

        dates_info = f"{duration} nights"
        if depart:
            dates_info += f" · {depart}"

        is_last = i == len(deals) - 1
        border = "" if is_last else "border-bottom:1px solid #f3f4f6;"

        rows.append(
            f'<div style="padding:14px 20px;{border}">'
            f'<p style="margin:0 0 2px;font-size:14px;font-weight:600;color:#111;">'
            f'{hotel}{stars}</p>'
            f'<p style="margin:0;font-size:13px;color:#666;">{dates_info} · '
            f'<strong style="color:#111;">{price}/person</strong></p>'
            '</div>'
        )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        + ''.join(rows)
        + '</div>'
    )
