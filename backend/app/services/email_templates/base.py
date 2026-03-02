"""Shared email layout wrapper."""
from __future__ import annotations


def wrap(
    body_html: str,
    *,
    preheader: str = "",
    footer_note: str = "",
    unsub_url: str = "",
) -> str:
    """Wrap body content in the standard TripSignal email shell."""
    preheader_block = (
        f'<div style="display:none;font-size:1px;color:#f5f9ff;line-height:1px;'
        f'max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</div>'
        if preheader else ""
    )

    # Build footer — this is fixed and NOT editable via the admin template editor.
    footer_parts = []

    # Tagline or custom contextual note
    if footer_note:
        footer_parts.append(footer_note)
    else:
        footer_parts.append(
            '<a href="https://tripsignal.ca" style="color:#999;text-decoration:none;">Trip Signal</a>'
            ' &middot; Vacation deal monitoring for Canadians'
        )

    # Physical address (required by CASL on commercial emails)
    footer_parts.append(
        '<span style="font-size:11px;color:#bbb;">[Mailing address]</span>'
    )

    # Unsubscribe link with tap-friendly spacing
    if unsub_url:
        footer_parts.append(
            f'<a href="{unsub_url}" style="color:#999;text-decoration:underline;">'
            f'Manage email preferences</a>'
        )

    footer_html = '<br style="line-height:28px;">'.join(footer_parts)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;background:#fff;max-width:560px;margin:0 auto;padding:40px 24px;">
{preheader_block}
<div style="margin-bottom:24px;">
  <img src="https://tripsignal.ca/new-logo-email.png" alt="Trip Signal" style="height:70px;width:auto;border-radius:8px;" />
</div>
{body_html}
<hr style="border:none;border-top:1px solid #eee;margin:32px 0;">
<p style="font-size:12px;color:#999;margin:0;text-align:center;">
  {footer_html}
</p>
</body>
</html>"""


def button(text: str, href: str) -> str:
    """Render an orange CTA button."""
    return (
        f'<a href="{href}" style="display:inline-block;background:#F97316;color:#fff;'
        f'text-decoration:none;padding:14px 28px;border-radius:24px;font-size:14px;'
        f'font-weight:600;margin:8px 0 24px;">{text}</a>'
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


def new_low_banner() -> str:
    """Yellow banner for all-time low price alerts."""
    return (
        '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;'
        'padding:12px 16px;margin-bottom:20px;">'
        '<p style="margin:0;font-size:14px;font-weight:600;color:#92400E;">'
        "All-time low price for this signal"
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
