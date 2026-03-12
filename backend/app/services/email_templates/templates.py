"""
All 15 lifecycle email templates.

Each function signature: (user: User, context: dict) -> (subject: str, html: str)
"""
from __future__ import annotations

from html import escape as esc
from typing import TYPE_CHECKING

from app.services.email_templates.base import (
    wrap, button, para, heading, info_box,
    stars_html, format_price, pricing_disclaimer, new_low_banner, price_drop_banner,
    destination_index_html, departure_heatmap_html, arbitrage_line,
    date_shift_line, budget_nudge_line,
)

if TYPE_CHECKING:
    from app.db.models.user import User


def _unsub(context: dict) -> str:
    """Extract the unsubscribe URL from context."""
    return context.get("_unsub_url", "")


def _email(user: "User") -> str:
    """Extract user email for footer."""
    return getattr(user, "email", "") or ""


def _is_instant(context: dict) -> bool:
    """Return True if the user is on 'all emails' (instant) delivery."""
    return context.get("_notification_frequency", "all") == "all"


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
    return subject, wrap(body, preheader="Start monitoring vacation deals", unsub_url=_unsub(context), user_email=_email(user))


def first_signal(*, user: "User", context: dict) -> tuple[str, str]:
    signal_name = context.get("signal_name", "your signal")
    subject = f'Your signal "{signal_name}" is now active'
    safe_name = esc(signal_name)
    body = (
        heading("Your signal is live")
        + para(
            f"We've started monitoring deals for <strong>{safe_name}</strong>. "
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
    return subject, wrap(body, preheader=f"Monitoring started for {safe_name}", unsub_url=_unsub(context), user_email=_email(user))


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
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# B) ALERTS
# ═══════════════════════════════════════════════════════════════════════════════

def match_alert(*, user: "User", context: dict) -> tuple[str, str]:
    """Consolidated per-user alert — one email covering all active signals.

    Structure:
      - For each signal with activity: heading → deals → intel sentence
      - "Still watching" section for quiet signals
      - Single CTA + disclaimer

    Context fields (consolidated):
        signals_with_activity (list of signal contexts), quiet_signals,
        active_signal_count, signals_with_activity_count, quiet_signal_count,
        + primary signal fields at top level for backward compat
    """
    from app.services.email_templates.subject_preview import (
        build_match_subject, build_match_preview,
    )

    subject = build_match_subject(context)
    preview = build_match_preview(context)

    parts: list[str] = []

    # ── Monitoring summary header ──
    active_count = context.get("active_signal_count", 1)
    activity_count = context.get("signals_with_activity_count", 1)

    summary_line = f"Watching {active_count} signal{'s' if active_count != 1 else ''}"
    if activity_count > 0:
        activity_line = (
            f"{activity_count} signal{'s' if activity_count != 1 else ''} "
            f"{'have' if activity_count != 1 else 'has'} new deals"
        )
    else:
        activity_line = "No new deals right now"

    parts.append(
        f'<p style="margin:0 0 4px;font-size:13px;color:#999;line-height:1.4;">'
        f'{summary_line}</p>'
        f'<p style="margin:0 0 28px;font-size:16px;font-weight:600;color:#111;line-height:1.4;">'
        f'{activity_line}</p>'
    )

    # ── Render each signal with activity ──
    signals = context.get("signals_with_activity") or [context]
    plan_type = context.get("plan_type", "free")

    for i, sig in enumerate(signals):
        route = sig.get("route", "")
        deals = sig.get("deals", [])
        is_new_low = sig.get("new_low", False)
        pct_drop = sig.get("pct_drop", 0)
        intel_sentence = esc(sig.get("intel_sentence", ""))
        days_monitoring = sig.get("days_monitoring", 0)

        # Cap at 2 deals per signal for clean presentation
        display_deals = deals[:2]

        # Separator between signals
        if i > 0:
            parts.append(
                '<div style="height:24px;"></div>'
            )

        # ── Signal card container ──
        parts.append(
            '<div style="border:1px solid #e5e7eb;border-radius:12px;'
            'overflow:hidden;margin-bottom:8px;">'
        )

        # Signal heading — route as the primary label
        heading_text = esc(route or sig.get("signal_name", "your signal"))
        parts.append(
            f'<div style="padding:16px 20px 0;">'
            f'<p style="margin:0 0 6px;font-size:15px;font-weight:600;color:#111;">'
            f'{heading_text}</p>'
        )

        # Summary value line (hero stat or banner-style summary)
        hero = _build_hero_stat(sig)
        if is_new_low:
            parts.append(
                '<p style="margin:0 0 4px;font-size:13px;font-weight:600;color:#92400E;">'
                'Lowest price we\u2019ve seen</p>'
            )
        elif pct_drop and pct_drop >= 10:
            parts.append(
                f'<p style="margin:0 0 4px;font-size:13px;font-weight:600;color:#166534;">'
                f'Price dropped {pct_drop}%</p>'
            )
        elif hero:
            parts.append(
                f'<p style="margin:0 0 4px;font-size:13px;color:#666;">'
                f'{hero}</p>'
            )

        parts.append('</div>')

        # ── Deal rows inside the card ──
        for j, deal in enumerate(display_deals):
            hotel = esc(deal.get("hotel_name", "Hotel"))
            rating = deal.get("star_rating")
            price = format_price(deal.get("price_cents"))
            duration = deal.get("duration_nights", 7)
            depart = esc(str(deal.get("depart_date", "")))

            stars = stars_html(rating)
            delta = _deal_delta_html(deal)
            value_label = esc(deal.get("value_label") or "")

            provider = deal.get("provider", "")
            via = ""
            if provider:
                label = "RedTag" if provider == "redtag" else "SellOff"
                via = f' <span style="color:#aaa;font-size:11px;">via {label}</span>'

            dates_info = f"{duration} nights"
            if depart:
                dates_info += f" · {depart}"

            # Value label badge (e.g. "Rare value", "Great value")
            value_badge = ""
            if value_label:
                value_badge = (
                    f'<div style="margin-top:6px;">'
                    f'<span style="display:inline-block;background:#dcfce7;color:#166534;'
                    f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;">'
                    f'{value_label}</span></div>'
                )

            border_top = "border-top:1px solid #f3f4f6;" if j == 0 else ""
            border_bottom = "border-bottom:1px solid #f3f4f6;" if j < len(display_deals) - 1 else ""

            # View Deal link — validate URL scheme to prevent injection
            raw_link = deal.get("deeplink_url") or ""
            if raw_link and raw_link.startswith(("https://", "http://")):
                deal_link = esc(raw_link, quote=True)
            else:
                deal_link = f"https://tripsignal.ca/deal/{esc(deal.get('deal_id', ''), quote=True)}"
            view_deal_html = (
                f'<div style="margin-top:8px;">'
                f'<a href="{deal_link}" style="color:#2563EB;font-size:13px;'
                f'font-weight:600;text-decoration:none;">View Deal \u2192</a>'
                f'</div>'
            )

            parts.append(
                f'<div style="padding:14px 20px;{border_top}{border_bottom}">'
                f'<div style="margin:0 0 4px;">'
                f'<span style="font-size:14px;font-weight:600;color:#111;">{hotel}</span>'
                f'{stars}{via}'
                f'</div>'
                f'<div style="font-size:13px;color:#666;">'
                f'{dates_info}'
                f'</div>'
                f'<div style="margin-top:6px;">'
                f'<span style="font-size:20px;font-weight:700;color:#111;">{price}</span>'
                f'{delta}'
                f'<span style="font-size:12px;color:#999;margin-left:6px;">per person</span>'
                f'</div>'
                f'{value_badge}'
                f'{view_deal_html}'
                f'</div>'
            )

        # More deals indicator
        remaining = len(deals) - len(display_deals)
        if remaining > 0:
            parts.append(
                f'<div style="padding:10px 20px;background:#f9fafb;text-align:center;">'
                f'<span style="font-size:13px;color:#666;">'
                f'+{remaining} more deal{"s" if remaining != 1 else ""}'
                f'</span></div>'
            )

        # Close signal card container
        parts.append('</div>')

        # Intel sentence below card
        if intel_sentence:
            parts.append(
                f'<p style="margin:6px 0 0;font-size:13px;color:#888;font-style:italic;'
                f'line-height:1.5;">'
                f'{intel_sentence}</p>'
            )

        # Pro-only insight lines (arbitrage, date shift, budget nudge)
        if plan_type == "pro":
            # Airport arbitrage insight
            arbitrage = sig.get("arbitrage")
            if arbitrage:
                parts.append(arbitrage_line(
                    arbitrage["arbitrage_airport"],
                    arbitrage["arbitrage_savings_cents"],
                ))

            # Date shift saving
            date_shift = sig.get("date_shift")
            if date_shift:
                parts.append(date_shift_line(date_shift))

            # Budget nudge
            budget_nudge = sig.get("budget_nudge")
            if budget_nudge:
                parts.append(budget_nudge_line(budget_nudge))

    # ── Still watching section ──
    quiet_signals = context.get("quiet_signals", [])
    if quiet_signals:
        parts.append(
            '<div style="margin-top:28px;padding:16px 20px;background:#f9fafb;'
            'border-radius:12px;">'
        )
        parts.append(
            '<p style="margin:0 0 10px;font-size:13px;font-weight:600;color:#999;'
            'letter-spacing:0.3px;">Still watching your signals</p>'
        )
        for qs in quiet_signals:
            qname = esc(qs.get("signal_name", "signal"))
            parts.append(
                f'<p style="margin:0 0 4px;font-size:14px;color:#666;line-height:1.5;">'
                f'{qname}</p>'
            )
        parts.append(
            '<p style="margin:8px 0 0;font-size:12px;color:#aaa;">'
            'No strong deals found yet.</p>'
        )
        parts.append('</div>')

    # ── Scout teaser / Trial conversion teaser ──
    if plan_type != "pro":
        # Trial/free users: conversion teaser — reference computed savings
        arb_saving = 0
        arbitrage = context.get("arbitrage")
        if arbitrage and isinstance(arbitrage, dict):
            arb_saving = arbitrage.get("arbitrage_savings_cents", 0) / 100

        ds_saving = 0
        date_shift = context.get("date_shift")
        if date_shift:
            ds_saving = date_shift.get("saving_cents", 0) / 100

        biggest_saving = max(arb_saving, ds_saving)

        if biggest_saving >= 50:
            teaser_text = f"We found a way to save ${biggest_saving:,.0f} on this trip."
            teaser_cta = "Upgrade to Pro to see how \u2192"
        else:
            teaser_text = (
                "Pro members get airport savings tips, date flex insights, "
                "and budget recommendations."
            )
            teaser_cta = "Learn more \u2192"

        parts.append(
            '<div style="padding:16px 20px 8px 20px;text-align:center;'
            'background-color:#F8F6F0;border-radius:6px;margin-bottom:16px;">'
            f'<p style="font-size:13px;color:#3D3929;margin:0;font-weight:600;">'
            f'{teaser_text}</p>'
            f'<p style="margin:6px 0 0;">'
            f'<a href="https://tripsignal.ca/pricing" style="color:#2563EB;'
            f'font-size:13px;text-decoration:none;font-weight:600;">{teaser_cta}</a>'
            f'</p></div>'
        )
    else:
        # Pro users: data-driven Scout teaser
        total = context.get("total_matches", 0)
        if total > 5:
            top_pct = context.get("percentile_rank")
            if top_pct is not None and top_pct <= 0.25:
                rank_pct = max(1, int(top_pct * 100))
                teaser_text = (
                    f"We\u2019re tracking {total} deals on your route. "
                    f"This one ranks in the top {rank_pct}%."
                )
            else:
                teaser_text = (
                    f"We\u2019re tracking {total} deals on your route. "
                    "See the full market picture."
                )

            parts.append(
                '<div style="padding:16px 20px 8px 20px;text-align:center;'
                'margin-bottom:16px;">'
                f'<p style="font-size:13px;color:#6B6452;margin:0;">'
                f'{teaser_text}'
                f' <a href="https://tripsignal.ca/scout" style="color:#2563EB;'
                f'text-decoration:none;font-weight:600;">Open Scout Insights \u2192</a>'
                f'</p></div>'
            )

    # ── Primary CTA ──
    parts.append(
        '<div style="text-align:center;margin:32px 0 24px;">'
    )
    parts.append(button("Open Trip Signal \u2192", "https://tripsignal.ca/signals"))
    parts.append('</div>')

    # ── Disclaimer ──
    parts.append(pricing_disclaimer())

    body = "".join(parts)
    return subject, wrap(
        body,
        preheader=preview,
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


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

    safe_signal = esc(signal_name)
    safe_hotel = esc(hotel_name)
    safe_route = esc(route)
    safe_drop = esc(drop_amount)
    safe_depart = esc(depart)

    subject = f"Price dropped {drop_amount} on {signal_name}"

    stars = stars_html(star_rating)
    price = format_price(new_price_cents)

    dates_info = f"{duration} nights"
    if depart:
        dates_info += f" · {safe_depart}"

    # Deal card
    card_inner = (
        f'<p style="margin:0 0 4px;font-size:16px;font-weight:600;color:#111;">'
        f'{safe_hotel}{stars}</p>'
        f'<p style="margin:0 0 16px;font-size:13px;color:#666;">'
        f'{safe_route} · {dates_info}</p>'
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

    drop_info = f" — down <strong>{safe_drop}</strong>"
    if drop_pct:
        drop_info += f" ({drop_pct}%)"

    body = (
        (price_drop_banner(drop_pct) if drop_pct and drop_pct >= 10 else "")
        + heading("Significant price drop")
        + para(
            f"<strong>{safe_hotel}</strong> on your {safe_signal} signal "
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
    return subject, wrap(
        body,
        preheader=f"{safe_drop} price drop",
        show_daily_summary_nudge=_is_instant(context),
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


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
        unsub_url=_unsub(context),
        user_email=_email(user),
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
        unsub_url=_unsub(context),
        user_email=_email(user),
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
    return subject, wrap(body, preheader="Pro features are now active", unsub_url=_unsub(context), user_email=_email(user))


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
    return subject, wrap(body, preheader="Please update your payment method", unsub_url=_unsub(context), user_email=_email(user))


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
    return subject, wrap(body, preheader="Payment update needed", unsub_url=_unsub(context), user_email=_email(user))


def subscription_canceled(*, user: "User", context: dict) -> tuple[str, str]:
    period_end = context.get("period_end", "")
    safe_period = esc(period_end)
    subject = "Your Trip Signal Pro subscription has been canceled"
    period_note = (
        f" You'll keep Pro access until <strong>{safe_period}</strong>."
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
    return subject, wrap(body, preheader="Pro canceled" + (f" — access until {safe_period}" if period_end else ""), unsub_url=_unsub(context), user_email=_email(user))


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
    return subject, wrap(body, preheader="Account deleted", unsub_url=_unsub(context), user_email=_email(user))


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
    return subject, wrap(body, preheader="Account deleted, subscription canceled", unsub_url=_unsub(context), user_email=_email(user))


# ═══════════════════════════════════════════════════════════════════════════════
# F) ENGAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def no_match_update(*, user: "User", context: dict) -> tuple[str, str]:
    signal_name = context.get("signal_name", "your signal")
    days_active = context.get("days_active", 14)
    safe_name = esc(signal_name)
    subject = f"Update on {signal_name} — no matches yet"
    body = (
        heading(f"No matches yet for {safe_name}")
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
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


def inactive_reengagement(*, user: "User", context: dict) -> tuple[str, str]:
    """Re-engagement email — proof-of-value zones.

    Context: total_deals_found, best_missed_deal (dict), min_price_ever_cents,
             max_price_ever_cents, trend_direction, current_best_deal (dict),
             days_inactive, best_missed_price_cents
    """
    from app.services.email_templates.subject_preview import (
        build_reengagement_subject, build_reengagement_preview,
    )

    subject = build_reengagement_subject(context)
    preview = build_reengagement_preview(context)

    total_deals = context.get("total_deals_found", 0)
    best_missed = context.get("best_missed_deal", {})
    min_ever = context.get("min_price_ever_cents")
    max_ever = context.get("max_price_ever_cents")
    trend_dir = context.get("trend_direction", "stable")
    current_best = context.get("current_best_deal", {})
    days_inactive = context.get("days_inactive", 21)

    parts: list[str] = []

    # ── Zone 1: Proof header ──
    if total_deals and total_deals > 0:
        parts.append(heading(f"Your signal found {total_deals} deals while you were away"))
    else:
        parts.append(heading("Your signals are still running"))

    parts.append(para(
        f"It\u2019s been {days_inactive} days since your last visit. "
        "Here\u2019s what we found."
    ))

    # ── Zone 2: Best missed deal ──
    if best_missed and best_missed.get("price_cents"):
        missed_price = format_price(best_missed["price_cents"])
        missed_hotel = esc(best_missed.get("hotel_name", ""))
        missed_nights = best_missed.get("duration_nights", 7)
        missed_depart = esc(best_missed.get("depart_date", ""))

        detail_parts = []
        if missed_hotel:
            detail_parts.append(missed_hotel)
        if missed_depart:
            detail_parts.append(str(missed_depart))
        if missed_nights:
            detail_parts.append(f"{missed_nights} nights")
        detail = " \u00b7 ".join(detail_parts)

        parts.append(
            '<div style="border:1px solid #e5e7eb;border-radius:12px;'
            'overflow:hidden;margin-bottom:24px;">'
            '<div style="padding:20px;">'
            '<p style="margin:0 0 4px;font-size:12px;color:#999;'
            'text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">'
            'Best deal you missed</p>'
            f'<p style="margin:0 0 4px;font-size:28px;font-weight:700;color:#111;">'
            f'{missed_price}</p>'
            f'<p style="margin:0;font-size:13px;color:#666;">{detail}</p>'
            '</div></div>'
        )

    # ── Zone 3: Price range + trend ──
    range_parts = []
    if min_ever:
        range_parts.append(f"Lowest: <strong>{format_price(min_ever)}</strong>")
    if max_ever:
        range_parts.append(f"Highest: <strong>{format_price(max_ever)}</strong>")
    if trend_dir == "down":
        range_parts.append("Trend: prices dropping")
    elif trend_dir == "up":
        range_parts.append("Trend: prices rising")

    if range_parts:
        parts.append(info_box(
            '<p style="margin:0;font-size:14px;color:#333;">'
            + " &middot; ".join(range_parts)
            + '</p>'
        ))

    # ── Zone 4: Current best deal ──
    if current_best and current_best.get("price_cents"):
        cur_price = format_price(current_best["price_cents"])
        cur_hotel = esc(current_best.get("hotel_name", ""))
        cur_nights = current_best.get("duration_nights", 7)
        parts.append(para(
            f'<strong>Right now:</strong> {cur_price} '
            f'{"at " + cur_hotel + " " if cur_hotel else ""}'
            f'\u00b7 {cur_nights} nights'
        ))

    # ── Zone 5: CTA ──
    parts.append(button("Your signal is still running \u2192", "https://tripsignal.ca/signals"))

    # ── Zone 6: Quiet unsubscribe nudge ──
    parts.append(para(
        '<span style="font-size:13px;color:#999;">'
        'Too many emails? '
        '<a href="https://tripsignal.ca/account/settings" style="color:#999;text-decoration:underline;">'
        'Switch to weekly only</a>.'
        '</span>'
    ))

    body = "".join(parts)
    return subject, wrap(
        body,
        preheader=preview,
        show_daily_summary_nudge=_is_instant(context),
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


def weekly_digest(*, user: "User", context: dict) -> tuple[str, str]:
    """Weekly digest — zone-structured for passive users.

    Context: deal_count, deals (list), trend_direction, trend_weeks,
             best_value_nights, best_value_pct_saving, total_matches,
             days_monitoring, signal_name, route, destination, best_price_cents
    """
    from app.services.email_templates.subject_preview import (
        build_digest_subject, build_digest_preview,
    )

    subject = build_digest_subject(context)
    preview = build_digest_preview(context)

    deal_count = context.get("deal_count", 0)
    deals = context.get("deals", [])
    best_deal = deals[0] if deals else {}
    trend_dir = context.get("trend_direction", "stable")
    trend_weeks = context.get("trend_weeks", 0)
    best_value_nights = context.get("best_value_nights")
    best_value_pct = context.get("best_value_pct_saving")
    total_matches = context.get("total_matches", 0)
    days_monitoring = context.get("days_monitoring", 0)
    signal_name = context.get("signal_name", "your signal")
    safe_name = esc(signal_name)

    parts: list[str] = []

    # ── Zone 1: Week summary ──
    parts.append(heading(f"This week on {safe_name}"))

    summary_items = []
    if deal_count:
        summary_items.append(f"<strong>{deal_count}</strong> deal{'s' if deal_count != 1 else ''} found")
    if trend_dir == "down" and trend_weeks >= 2:
        summary_items.append(f"prices dropped {trend_weeks} weeks in a row")
    elif trend_dir == "up" and trend_weeks >= 2:
        summary_items.append(f"prices rising for {trend_weeks} weeks")

    if summary_items:
        parts.append(para(" \u00b7 ".join(summary_items).capitalize()))

    # ── Zone 2: Best deal card ──
    if best_deal and best_deal.get("price_cents"):
        parts.append(_single_deal_card(best_deal, context.get("route", "")))

    # ── Zone 3: Night length insight (Module 3) ──
    if best_value_nights and best_value_pct and best_value_pct > 5:
        parts.append(info_box(
            f'<p style="margin:0;font-size:14px;color:#333;">'
            f'\U0001f4a1 <strong>{best_value_nights}-night trips</strong> are currently '
            f'{int(best_value_pct)}% cheaper per night than other durations on this route.'
            f'</p>'
        ))

    # ── Zone 3b: Destination Price Index ──
    dest_index = context.get("destination_index")
    if dest_index:
        parts.append(destination_index_html(dest_index))

    # ── Zone 3c: Departure Heatmap ──
    heatmap = context.get("departure_heatmap")
    if heatmap:
        parts.append(departure_heatmap_html(heatmap))

    # ── Zone 4: Signal health ──
    if total_matches > 0 or days_monitoring > 0:
        health_parts = []
        if total_matches:
            health_parts.append(f"{total_matches} total matches")
        if days_monitoring:
            health_parts.append(f"{days_monitoring} days monitoring")
        parts.append(para(
            '<span style="font-size:13px;color:#999;">'
            + " \u00b7 ".join(health_parts)
            + '</span>'
        ))

    # ── Zone 5: Soft CTA ──
    parts.append(button("Review this week's deals \u2192", "https://tripsignal.ca/signals"))

    # ── Zone 6: Preferences nudge ──
    parts.append(para(
        '<span style="font-size:13px;color:#999;">'
        'Getting too many emails? '
        '<a href="https://tripsignal.ca/account/settings" style="color:#999;text-decoration:underline;">'
        'Adjust your alert threshold</a>.'
        '</span>'
    ))

    body = "".join(parts)
    return subject, wrap(
        body,
        preheader=preview,
        show_daily_summary_nudge=_is_instant(context),
        unsub_url=_unsub(context),
        user_email=_email(user),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE: Deal card renderers (used by match_alert, major_drop_alert, digest)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_hero_stat(context: dict) -> str:
    """Build the single most compelling number for the hero zone."""
    is_top_25 = context.get("is_top_25", False)
    days_monitoring = context.get("days_monitoring", 0)
    pct_drop = context.get("pct_drop", 0)
    best_price_delta = context.get("best_price_delta", 0)
    trend_direction = context.get("trend_direction", "stable")
    trend_weeks = context.get("trend_weeks", 0)
    best_price_cents = context.get("best_price_cents")
    is_new_low = context.get("new_low", False)

    price = format_price(best_price_cents) if best_price_cents else ""

    # Priority: percentile rank > price delta > trend
    if is_top_25 and days_monitoring > 7:
        weeks = max(1, days_monitoring // 7)
        if weeks == 1:
            return f"{price} \u2014 lowest price this week"
        return f"{price} \u2014 lowest price in {weeks} weeks"

    if is_new_low and price:
        return f"{price} \u2014 lowest we\u2019ve seen on this route"

    if pct_drop and pct_drop >= 8 and best_price_delta:
        delta_str = format_price(abs(best_price_delta))
        return f"Down {delta_str} from last check"

    if trend_direction == "up" and trend_weeks >= 2 and pct_drop and pct_drop > 0:
        return f"First drop in {trend_weeks} weeks of rising prices"

    return ""


def _deal_delta_html(deal: dict) -> str:
    """Render a price delta indicator if the deal has a meaningful delta (> 5%)."""
    delta = deal.get("price_delta", 0)
    price = deal.get("price_cents", 0)
    if not delta or not price or delta <= 0:
        return ""
    pct = int(round(delta / (price + delta) * 100))
    if pct < 5:
        return ""
    delta_str = format_price(delta)
    return (
        f' <span style="color:#166534;font-size:12px;font-weight:600;">'
        f'\u2193 {delta_str}</span>'
    )


def _single_deal_card(deal: dict, route: str) -> str:
    """Render a prominent single-deal card with star rating, price, and delta."""
    hotel = esc(deal.get("hotel_name", "Hotel"))
    rating = deal.get("star_rating")
    price = format_price(deal.get("price_cents"))
    duration = deal.get("duration_nights", 7)
    depart = esc(deal.get("depart_date", ""))

    stars = stars_html(rating)
    delta = _deal_delta_html(deal)

    dates_info = f"{duration} nights"
    if depart:
        dates_info += f" \u00b7 {depart}"

    safe_route = esc(route)
    route_line = f"{safe_route} \u00b7 {dates_info}" if route else dates_info

    provider = deal.get("provider", "")
    via = ""
    if provider:
        label = "RedTag" if provider == "redtag" else "SellOff"
        via = f' <span style="color:#aaa;font-size:11px;font-weight:400;">via {label}</span>'

    card_inner = (
        f'<p style="margin:0 0 4px;font-size:16px;font-weight:600;color:#111;">'
        f'{hotel}{stars}{via}</p>'
        f'<p style="margin:0 0 16px;font-size:13px;color:#666;">{route_line}</p>'
    )
    if price:
        card_inner += (
            f'<p style="margin:0 0 4px;font-size:28px;font-weight:700;color:#111;">'
            f'{price}{delta}</p>'
            '<p style="margin:0;font-size:12px;color:#666;">'
            'per person (based on double occupancy)</p>'
        )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        f'<div style="padding:20px;">{card_inner}</div></div>'
    )


def _multi_deal_list(deals: list[dict]) -> str:
    """Render a stacked list of deal rows with delta indicators."""
    rows: list[str] = []
    for i, deal in enumerate(deals):
        hotel = esc(deal.get("hotel_name", "Hotel"))
        rating = deal.get("star_rating")
        price = format_price(deal.get("price_cents"))
        duration = deal.get("duration_nights", 7)
        depart = esc(deal.get("depart_date", ""))

        stars = stars_html(rating)
        delta = _deal_delta_html(deal)

        provider = deal.get("provider", "")
        via = ""
        if provider:
            label = "RedTag" if provider == "redtag" else "SellOff"
            via = f' <span style="color:#aaa;font-size:11px;font-weight:400;">via {label}</span>'

        dates_info = f"{duration} nights"
        if depart:
            dates_info += f" \u00b7 {depart}"

        is_last = i == len(deals) - 1
        border = "" if is_last else "border-bottom:1px solid #f3f4f6;"

        rows.append(
            f'<div style="padding:14px 20px;{border}">'
            f'<p style="margin:0 0 2px;font-size:14px;font-weight:600;color:#111;">'
            f'{hotel}{stars}{via}</p>'
            f'<p style="margin:0;font-size:13px;color:#666;">{dates_info} \u00b7 '
            f'<strong style="color:#111;">{price}/person</strong>{delta}</p>'
            '</div>'
        )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        + ''.join(rows)
        + '</div>'
    )


def trial_extended(*, user: "User", context: dict) -> tuple[str, str]:
    subject = "We've extended your trial"
    body = (
        heading("We\u2019ve extended your trial")
        + para(
            "Your signal hasn\u2019t found many matches yet, so we\u2019ve added "
            "7 more days to your trial."
        )
        + para(
            "This usually means your search criteria are quite specific. "
            "You might see more deals if you:"
        )
        + info_box(
            '<p style="margin:0;font-size:14px;color:#333;line-height:1.8;">'
            "\u2022 Widen your travel dates by a few weeks<br>"
            "\u2022 Increase your budget slightly<br>"
            "\u2022 Try a broader destination (e.g. \u201cAll Mexico\u201d instead of a specific city)"
            "</p>"
        )
        + para("We\u2019ll keep scanning \u2014 your next check runs tomorrow morning.")
        + button("Adjust your signal", "https://tripsignal.ca/signals")
    )
    return subject, wrap(
        body,
        preheader="We've added 7 more days to find you better deals",
        unsub_url=_unsub(context),
        user_email=_email(user),
    )
