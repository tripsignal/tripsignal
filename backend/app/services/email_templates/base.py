"""Shared email layout wrapper."""
from __future__ import annotations

import html as _html


def esc(value: str | None) -> str:
    """HTML-escape a string to prevent injection in email templates."""
    if value is None:
        return ""
    return _html.escape(str(value))


def wrap(
    body_html: str,
    *,
    preheader: str = "",
    show_daily_summary_nudge: bool = False,
    unsub_url: str = "",
    user_email: str = "",
) -> str:
    """Wrap body content in the standard TripSignal email shell."""
    preheader_block = (
        f'<div style="display:none;font-size:1px;color:#f5f9ff;line-height:1px;'
        f'max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</div>'
        if preheader else ""
    )

    # Build footer — this is fixed and NOT editable via the admin template editor.
    # Conditional daily-summary nudge (only for users on "all emails")
    nudge_html = ""
    if show_daily_summary_nudge:
        nudge_html = (
            '<p style="font-size:12px;color:#999;margin:0 0 16px;text-align:center;line-height:1.6;">'
            'Want fewer emails? '
            '<a href="https://tripsignal.ca/account/settings" '
            'style="color:#999;text-decoration:underline;">Switch to a daily summary</a>.'
            '</p>'
        )

    # "This email was sent to …"
    from html import escape as _esc
    sent_to_line = ""
    if user_email:
        sent_to_line = (
            f'<p style="font-size:12px;color:#999;margin:0 0 12px;text-align:center;line-height:1.6;">'
            f'This email was sent to {_esc(user_email)}'
            f'</p>'
        )

    # Links row: Unsubscribe | Privacy Policy | Terms and Conditions
    notif_url = unsub_url or "https://tripsignal.ca/account/settings"
    links_html = (
        '<p style="font-size:12px;color:#999;margin:0 0 12px;text-align:center;line-height:1.6;">'
        f'<a href="{esc(notif_url)}" style="color:#999;text-decoration:underline;">Unsubscribe</a>'
        ' &nbsp;|&nbsp; '
        '<a href="https://tripsignal.ca/privacy-policy" style="color:#999;text-decoration:underline;">Privacy Policy</a>'
        ' &nbsp;|&nbsp; '
        '<a href="https://tripsignal.ca/terms-and-conditions" style="color:#999;text-decoration:underline;">Terms and Conditions</a>'
        '</p>'
    )

    # Address + copyright
    brand_html = (
        '<p style="font-size:11px;color:#bbb;margin:0 0 12px;text-align:center;line-height:1.5;">'
        '[Mailing Address]<br>'
        '\u00a9 2026 Trip Signal. All rights reserved.'
        '</p>'
    )

    # Legal copy
    legal_html = (
        '<p style="font-size:11px;color:#bbb;margin:0;text-align:center;line-height:1.5;">'
        "You\u2019re receiving this email because you created one or more Trip Signal alerts."
        '</p>'
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;background:#fff;max-width:560px;margin:0 auto;padding:40px 24px;">
{preheader_block}
<div style="margin-bottom:24px;">
  <img src="https://tripsignal.ca/new-logo-email.png" alt="Trip Signal" style="height:38px;width:auto;border-radius:8px;" />
</div>
{body_html}
<hr style="border:none;border-top:1px solid #eee;margin:32px 0;">
{nudge_html}{sent_to_line}{links_html}{brand_html}{legal_html}
</body>
</html>"""


def button(text: str, href: str) -> str:
    """Render an orange CTA button."""
    # Reject non-HTTP schemes (javascript:, data:, etc.)
    safe_href = href if href.startswith(("https://", "http://")) else "#"
    return (
        f'<a href="{esc(safe_href)}" style="display:inline-block;background:#F97316;color:#fff;'
        f'text-decoration:none;padding:14px 28px;border-radius:24px;font-size:14px;'
        f'font-weight:600;margin:8px 0 24px;">{esc(text)}</a>'
    )


def para(text: str) -> str:
    """Render a body paragraph."""
    return f'<p style="margin:0 0 16px;font-size:14px;color:#333;line-height:1.6;">{text}</p>'


def heading(text: str) -> str:
    """Render a section heading."""
    return f'<h1 style="font-size:22px;font-weight:600;margin:0 0 16px;color:#111;">{text}</h1>'


def info_box(content: str) -> str:
    """Render a blue info box."""
    return (
        f'<div style="background:#f0f7ff;border:1px solid #dbeafe;border-radius:8px;'
        f'padding:16px 20px;margin-bottom:24px;">{content}</div>'
    )


def stars_html(rating: float | None) -> str:
    """Render star rating inline. Returns '' if rating is None.

    Examples: 4.0 → ' ★★★★', 4.5 → ' ★★★★½', None → ''
    """
    if rating is None:
        return ""
    full = int(rating)
    has_half = (rating - full) >= 0.4
    s = "★" * full + ("½" if has_half else "")
    return f' <span style="color:#F59E0B;">{s}</span>'


def format_price(price_cents: int | None) -> str:
    """Format price in cents as '$X,XXX'. Returns '' if None."""
    if price_cents is None:
        return ""
    return f"${price_cents // 100:,}"


def value_score_badge(score: int) -> str:
    """Render a value score badge (0-100). Only show for scores >= 75."""
    if score < 75:
        return ""
    top_pct = max(1, 100 - score)
    # Color: green for 90+, blue for 75-89
    if score >= 90:
        bg, border, text_color = "#dcfce7", "#86efac", "#166534"
    else:
        bg, border, text_color = "#f0f7ff", "#dbeafe", "#1D4ED8"
    return (
        f'<div style="display:inline-block;background:{bg};border:1px solid {border};'
        f'border-radius:20px;padding:6px 14px;margin-bottom:16px;">'
        f'<span style="font-size:13px;font-weight:600;color:{text_color};">'
        f'Value Score: {score}/100 \u2014 top {top_pct}%</span></div>'
    )


def arbitrage_line(airport: str, savings_cents: int) -> str:
    """Render an airport arbitrage savings line."""
    from html import escape as _esc
    savings = format_price(savings_cents)
    return (
        '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        f'<p style="margin:0;font-size:13px;color:#92400E;">'
        f'\u2708\ufe0f Same resort is <strong>{savings}/pp cheaper</strong> from '
        f'<strong>{_esc(airport)}</strong></p></div>'
    )


def destination_index_html(destinations: list[dict]) -> str:
    """Render a destination price index leaderboard table.

    Each dict: {destination_region, current_week_avg_cents, week_over_week_pct}
    """
    if not destinations:
        return ""

    rows = []
    for i, d in enumerate(destinations):
        region = esc(d["destination_region"].replace("_", " ").title())
        price = format_price(d["current_week_avg_cents"])
        wow = d.get("week_over_week_pct")

        if wow is not None:
            if wow < -1:
                trend = f'<span style="color:#166534;">\u2193{abs(wow):.0f}%</span>'
            elif wow > 1:
                trend = f'<span style="color:#991b1b;">\u2191{wow:.0f}%</span>'
            else:
                trend = '<span style="color:#666;">stable</span>'
        else:
            trend = ""

        border = "border-bottom:1px solid #f3f4f6;" if i < len(destinations) - 1 else ""
        rows.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:10px 16px;{border}">'
            f'<span style="font-size:14px;color:#333;">'
            f'<strong>{i+1}.</strong> {region}</span>'
            f'<span style="font-size:14px;color:#111;font-weight:600;">'
            f'{price}/pp {trend}</span></div>'
        )

    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        '<div style="padding:12px 16px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">'
        '<p style="margin:0;font-size:13px;font-weight:600;color:#666;text-transform:uppercase;'
        'letter-spacing:0.5px;">This week\u2019s best value destinations</p></div>'
        + "".join(rows)
        + "</div>"
    )


def departure_heatmap_html(weeks: list[dict]) -> str:
    """Render a departure window heatmap showing avg price by week.

    Each dict: {week, avg_cents, deal_count, is_cheapest, is_priciest}
    """
    if not weeks:
        return ""

    rows = []
    for w in weeks:
        price = format_price(w["avg_cents"])
        # Format week as "Mar 9" style
        try:
            from datetime import date as date_type
            d = date_type.fromisoformat(w["week"])
            label = d.strftime("%b %-d")
        except (ValueError, TypeError):
            label = esc(str(w["week"]))

        if w.get("is_cheapest"):
            bg = "background:#dcfce7;"
            badge = ' <span style="color:#166534;font-size:11px;font-weight:600;">CHEAPEST</span>'
        elif w.get("is_priciest"):
            bg = "background:#fef2f2;"
            badge = ' <span style="color:#991b1b;font-size:11px;font-weight:600;">PRICIEST</span>'
        else:
            bg = ""
            badge = ""

        rows.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:8px 16px;{bg}border-bottom:1px solid #f3f4f6;">'
            f'<span style="font-size:13px;color:#333;">Week of {label}{badge}</span>'
            f'<span style="font-size:13px;color:#111;font-weight:600;">'
            f'{price}/pp</span></div>'
        )

    return (
        '<div style="border:1px solid #e5e7eb;border-radius:12px;'
        'overflow:hidden;margin-bottom:24px;">'
        '<div style="padding:12px 16px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">'
        '<p style="margin:0;font-size:13px;font-weight:600;color:#666;text-transform:uppercase;'
        'letter-spacing:0.5px;">Avg price by departure week</p></div>'
        + "".join(rows)
        + "</div>"
    )


def date_shift_line(date_shift: dict) -> str:
    """Render date shift saving insight line for email."""
    if not date_shift:
        return ""
    saving = date_shift["saving_cents"] / 100
    alt_date = date_shift["alt_date"]
    if hasattr(alt_date, 'strftime'):
        alt_date_str = alt_date.strftime("%b %-d")
    else:
        alt_date_str = str(alt_date)
    return (
        '<div style="background:#f0f7ff;border:1px solid #dbeafe;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        f'<p style="margin:0;font-size:13px;color:#2563EB;">'
        f'\U0001f4a1 <strong>Date flex:</strong> Depart {alt_date_str} instead '
        f'\u2014 save ${saving:,.0f}/pp</p></div>'
    )


def budget_nudge_line(nudge: dict) -> str:
    """Render budget nudge insight line for email."""
    from html import escape as _esc
    if not nudge:
        return ""
    extra = nudge["extra_cents"] / 100
    stars = nudge.get("star_rating", 0)
    hotel = _esc(nudge.get("hotel_name", "a higher-rated resort"))
    stars_str = f" {stars:.1f}\u2605" if stars else ""
    return (
        '<div style="background:#f0f7ff;border:1px solid #dbeafe;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        f'<p style="margin:0;font-size:13px;color:#2563EB;">'
        f'\U0001f4a1 <strong>Budget nudge:</strong> ${extra:,.0f}/pp more gets you '
        f'{hotel}{stars_str}</p></div>'
    )


def pricing_disclaimer() -> str:
    """Standard pricing disclaimer for deal-related emails."""
    return (
        '<p style="margin:24px 0 0;font-size:11px;color:#999;line-height:1.5;">'
        "All prices are per person, based on double occupancy, and subject to "
        "availability and change. Trip Signal does not sell travel packages &mdash; "
        "we monitor prices and send alerts. Always confirm final pricing on the "
        "provider&rsquo;s website before booking."
        "</p>"
    )


def new_low_banner(days_monitoring: int = 0) -> str:
    """Yellow banner for all-time low price alerts."""
    if days_monitoring and days_monitoring > 7:
        weeks = max(1, days_monitoring // 7)
        copy = f"Cheapest we\u2019ve seen in {weeks} weeks"
    else:
        copy = "All-time low price for this signal"
    return (
        '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        f'<p style="margin:0;font-size:14px;font-weight:600;color:#92400E;">'
        f"{copy}"
        "</p></div>"
    )


def price_drop_banner(pct_drop: int) -> str:
    """Green banner for significant price drops."""
    return (
        '<div style="background:#dcfce7;border:1px solid #86efac;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        f'<p style="margin:0;font-size:14px;font-weight:600;color:#166534;">'
        f"Price dropped {pct_drop}% since last check"
        "</p></div>"
    )
