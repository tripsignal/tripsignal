"""
Snapshot render tests for email templates — subject/preview builders,
match alert locked priority, deal card rendering, and full template coverage.

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_email_templates.py -v
"""
from __future__ import annotations

import pytest

from app.services.email_orchestrator import EmailType
from app.services.email_templates.subject_preview import (
    build_match_subject,
    build_match_preview,
    build_subject,
    build_preview,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

class _FakeUser:
    id = "00000000-0000-0000-0000-000000000000"
    email = "test@example.com"
    plan_type = "pro"
    plan_status = "active"
    clerk_id = "test_clerk_preview"


def _user():
    return _FakeUser()


def _deal(**overrides):
    """Build a sample deal dict."""
    base = {
        "hotel_name": "Riu Palace Riviera Maya",
        "star_rating": 4.5,
        "price_cents": 89900,
        "duration_nights": 7,
        "depart_date": "Apr 15",
        "deeplink_url": "https://example.com/deal",
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# LOCKED MATCH ALERT SUBJECT PRIORITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchAlertSubject:
    """LOCKED priority: new_low > pct_drop >= 10 > single > multi."""

    def test_new_low(self):
        ctx = {"route": "Regina (YQR) → Cancun", "new_low": True, "deal_count": 1}
        assert build_match_subject(ctx) == "New low: Regina (YQR) → Cancun"

    def test_pct_drop_10(self):
        ctx = {"route": "Regina (YQR) → Cancun", "pct_drop": 10, "deal_count": 1}
        assert build_match_subject(ctx) == "Price drop: Regina (YQR) → Cancun"

    def test_pct_drop_25(self):
        ctx = {"route": "Regina (YQR) → Cancun", "pct_drop": 25, "deal_count": 3}
        assert build_match_subject(ctx) == "Price drop: Regina (YQR) → Cancun"

    def test_single_deal(self):
        ctx = {"route": "Regina (YQR) → Cancun", "deal_count": 1}
        assert build_match_subject(ctx) == "New deal: Regina (YQR) → Cancun"

    def test_multi_deal(self):
        ctx = {"route": "Regina (YQR) → Cancun", "deal_count": 3}
        assert build_match_subject(ctx) == "New deals found (3): Regina (YQR) → Cancun"

    def test_new_low_beats_pct_drop(self):
        ctx = {"route": "Regina (YQR) → Cancun", "new_low": True, "pct_drop": 20, "deal_count": 5}
        assert build_match_subject(ctx) == "New low: Regina (YQR) → Cancun"

    def test_pct_drop_beats_multi(self):
        ctx = {"route": "Regina (YQR) → Cancun", "pct_drop": 15, "deal_count": 5}
        assert build_match_subject(ctx) == "Price drop: Regina (YQR) → Cancun"

    def test_pct_drop_below_10_falls_to_multi(self):
        ctx = {"route": "Regina (YQR) → Cancun", "pct_drop": 9, "deal_count": 3}
        assert build_match_subject(ctx) == "New deals found (3): Regina (YQR) → Cancun"

    def test_pct_drop_below_10_falls_to_single(self):
        ctx = {"route": "Regina (YQR) → Cancun", "pct_drop": 5, "deal_count": 1}
        assert build_match_subject(ctx) == "New deal: Regina (YQR) → Cancun"

    def test_route_format_preserved(self):
        ctx = {"route": "Toronto (YYZ) → Punta Cana", "deal_count": 1}
        assert "Toronto (YYZ) → Punta Cana" in build_match_subject(ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# LOCKED MATCH ALERT PREVIEW PRIORITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchAlertPreview:
    """LOCKED priority: new_low > pct_drop >= 10 > single > multi."""

    def test_new_low(self):
        ctx = {
            "new_low": True, "deal_count": 1,
            "deals": [_deal(hotel_name="Riu Palace", price_cents=89900)],
        }
        assert build_match_preview(ctx) == "All-time low $899/person at Riu Palace"

    def test_new_low_no_hotel(self):
        ctx = {
            "new_low": True, "deal_count": 1,
            "deals": [_deal(hotel_name="", price_cents=89900)],
        }
        assert build_match_preview(ctx) == "All-time low $899/person"

    def test_pct_drop(self):
        ctx = {
            "pct_drop": 15, "deal_count": 1,
            "deals": [_deal(hotel_name="Riu Palace", price_cents=89900)],
        }
        preview = build_match_preview(ctx)
        assert "Down 15%" in preview
        assert "$899/person" in preview
        assert "Riu Palace" in preview

    def test_single_deal(self):
        ctx = {
            "deal_count": 1,
            "deals": [_deal(hotel_name="Riu Palace", price_cents=89900, duration_nights=7)],
        }
        assert build_match_preview(ctx) == "$899/person \u00b7 Riu Palace \u00b7 7 nights"

    def test_multi_deal(self):
        ctx = {
            "deal_count": 3,
            "deals": [_deal(price_cents=89900)],
        }
        assert build_match_preview(ctx) == "3 deals from $899/person"

    def test_new_low_beats_pct_drop(self):
        ctx = {
            "new_low": True, "pct_drop": 20, "deal_count": 1,
            "deals": [_deal(hotel_name="Riu", price_cents=89900)],
        }
        assert build_match_preview(ctx).startswith("All-time low")

    def test_pct_drop_beats_single(self):
        ctx = {
            "pct_drop": 12, "deal_count": 1,
            "deals": [_deal(hotel_name="Riu", price_cents=89900)],
        }
        assert "Down 12%" in build_match_preview(ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC SUBJECT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubjectBuilder:

    def test_welcome(self):
        assert build_subject(EmailType.WELCOME, {}) == "Welcome to Trip Signal"

    def test_first_signal(self):
        s = build_subject(EmailType.FIRST_SIGNAL, {"signal_name": "Mexico Trip"})
        assert s == 'Your signal "Mexico Trip" is now active'

    def test_trial_expiring_singular(self):
        s = build_subject(EmailType.TRIAL_EXPIRING_SOON, {"days_left": 1})
        assert s == "Your Trip Signal trial ends in 1 day"

    def test_trial_expiring_plural(self):
        s = build_subject(EmailType.TRIAL_EXPIRING_SOON, {"days_left": 3})
        assert s == "Your Trip Signal trial ends in 3 days"

    def test_pro_activated(self):
        assert build_subject(EmailType.PRO_ACTIVATED, {}) == "Welcome to Trip Signal Pro"

    def test_payment_failed(self):
        s = build_subject(EmailType.PAYMENT_FAILED, {})
        assert "payment failed" in s.lower()

    def test_subscription_canceled(self):
        s = build_subject(EmailType.SUBSCRIPTION_CANCELED, {})
        assert "canceled" in s.lower()

    def test_no_match_update(self):
        s = build_subject(EmailType.NO_MATCH_UPDATE, {"signal_name": "Europe"})
        assert "Europe" in s
        assert "no matches" in s.lower()

    def test_match_alert_delegates(self):
        """MATCH_ALERT subject must use the locked priority builder."""
        s = build_subject(EmailType.MATCH_ALERT, {
            "route": "Regina (YQR) → Cancun", "deal_count": 1,
        })
        assert s == "New deal: Regina (YQR) → Cancun"


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC PREVIEW BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreviewBuilder:

    def test_welcome(self):
        assert build_preview(EmailType.WELCOME, {}) == "Start monitoring vacation deals"

    def test_first_signal(self):
        p = build_preview(EmailType.FIRST_SIGNAL, {"signal_name": "Mexico"})
        assert "Mexico" in p

    def test_no_match(self):
        p = build_preview(EmailType.NO_MATCH_UPDATE, {"days_active": 14})
        assert p == "No matches after 14 days"

    def test_trial_expiring(self):
        p = build_preview(EmailType.TRIAL_EXPIRING_SOON, {"days_left": 3})
        assert "3 days" in p

    def test_match_alert_delegates(self):
        p = build_preview(EmailType.MATCH_ALERT, {
            "deal_count": 2, "deals": [_deal(price_cents=89900)],
        })
        assert p == "2 deals from $899/person"


# ═══════════════════════════════════════════════════════════════════════════════
# MATCH ALERT TEMPLATE RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

class TestMatchAlertTemplate:

    def test_single_deal_card(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Caribbean Trip",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 1,
            "deals": [_deal()],
        }
        subject, html = match_alert(user=_user(), context=ctx)
        assert subject == "New deal: Regina (YQR) → Cancun"
        assert "Riu Palace Riviera Maya" in html
        assert "$899" in html
        assert "★★★★½" in html
        assert "per person (based on double occupancy)" in html
        assert "7 nights" in html
        assert "View deal" in html
        assert "example.com/deal" in html

    def test_multi_deal_list(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Caribbean Trip",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 2,
            "deals": [
                _deal(hotel_name="Riu Palace", price_cents=89900, star_rating=4.5),
                _deal(hotel_name="Gran Bahia", price_cents=94900, star_rating=4.0),
            ],
        }
        subject, html = match_alert(user=_user(), context=ctx)
        assert subject == "New deals found (2): Regina (YQR) → Cancun"
        assert "Riu Palace" in html
        assert "Gran Bahia" in html
        assert "$899/person" in html
        assert "$949/person" in html
        assert "View all deals" in html

    def test_new_low_banner(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Test",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 1,
            "new_low": True,
            "deals": [_deal()],
        }
        _, html = match_alert(user=_user(), context=ctx)
        assert "All-time low" in html

    def test_pct_drop_banner(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Test",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 1,
            "pct_drop": 15,
            "deals": [_deal()],
        }
        _, html = match_alert(user=_user(), context=ctx)
        assert "Price dropped 15%" in html

    def test_no_star_rating(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Test",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 1,
            "deals": [_deal(hotel_name="Budget Hotel", star_rating=None)],
        }
        _, html = match_alert(user=_user(), context=ctx)
        assert "Budget Hotel" in html
        assert "★" not in html

    def test_pricing_disclaimer(self):
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Test",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 1,
            "deals": [_deal()],
        }
        _, html = match_alert(user=_user(), context=ctx)
        assert "does not sell travel" in html

    def test_legacy_context_fallback(self):
        """Works with old-style context (no deals list)."""
        from app.services.email_templates.templates import match_alert
        ctx = {
            "signal_name": "Caribbean Trip",
            "route": "Regina (YQR) → Cancun",
            "deal_count": 3,
            "best_price": "$899",
        }
        subject, html = match_alert(user=_user(), context=ctx)
        assert "New deals found (3)" in subject
        assert "$899" in html


# ═══════════════════════════════════════════════════════════════════════════════
# MAJOR DROP ALERT TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

class TestMajorDropAlert:

    def test_renders_hotel_and_stars(self):
        from app.services.email_templates.templates import major_drop_alert
        ctx = {
            "signal_name": "Caribbean Trip",
            "route": "Regina (YQR) → Cancun",
            "hotel_name": "Riu Palace",
            "star_rating": 4.5,
            "drop_amount": "$250",
            "drop_pct": 20,
            "new_price_cents": 74900,
            "duration_nights": 7,
            "depart_date": "Apr 15",
            "deeplink_url": "https://example.com/deal",
        }
        subject, html = major_drop_alert(user=_user(), context=ctx)
        assert "Price dropped $250" in subject
        assert "Riu Palace" in html
        assert "★★★★½" in html
        assert "$749" in html
        assert "per person (based on double occupancy)" in html
        assert "Regina (YQR) → Cancun" in html
        assert "7 nights" in html
        assert "example.com/deal" in html

    def test_drop_pct_banner(self):
        from app.services.email_templates.templates import major_drop_alert
        ctx = {
            "signal_name": "Test",
            "route": "Test Route",
            "hotel_name": "Riu",
            "drop_amount": "$250",
            "drop_pct": 20,
            "new_price_cents": 74900,
        }
        _, html = major_drop_alert(user=_user(), context=ctx)
        assert "Price dropped 20%" in html

    def test_pricing_disclaimer(self):
        from app.services.email_templates.templates import major_drop_alert
        ctx = {
            "signal_name": "Test",
            "route": "Test Route",
            "hotel_name": "Riu",
            "drop_amount": "$250",
            "new_price_cents": 74900,
        }
        _, html = major_drop_alert(user=_user(), context=ctx)
        assert "does not sell travel" in html

    def test_no_star_rating(self):
        from app.services.email_templates.templates import major_drop_alert
        ctx = {
            "signal_name": "Test",
            "route": "Test Route",
            "hotel_name": "Budget Hotel",
            "star_rating": None,
            "drop_amount": "$100",
            "new_price_cents": 59900,
        }
        _, html = major_drop_alert(user=_user(), context=ctx)
        assert "Budget Hotel" in html
        assert "★" not in html


# ═══════════════════════════════════════════════════════════════════════════════
# FULL TEMPLATE SUITE — render every template and validate content
# ═══════════════════════════════════════════════════════════════════════════════

_SAMPLE_CONTEXTS = {
    "welcome": {},
    "first_signal": {"signal_name": "Mexico Trip"},
    "no_signal_reminder": {},
    "match_alert": {
        "signal_name": "Caribbean Trip",
        "route": "Regina (YQR) → Cancun",
        "deal_count": 1,
        "deals": [_deal()],
    },
    "major_drop_alert": {
        "signal_name": "Caribbean Trip",
        "route": "Regina (YQR) → Cancun",
        "hotel_name": "Riu Palace",
        "star_rating": 4.0,
        "drop_amount": "$250",
        "drop_pct": 20,
        "new_price_cents": 74900,
        "duration_nights": 7,
        "depart_date": "Apr 15",
        "deeplink_url": "https://example.com",
    },
    "trial_expiring_soon": {"days_left": 3},
    "trial_expired_upsell": {},
    "pro_activated": {},
    "payment_failed": {"invoice_id": "inv_123"},
    "payment_failed_reminder": {"reminder_num": 1},
    "subscription_canceled": {"period_end": "March 15, 2026"},
    "account_deleted_free": {},
    "account_deleted_pro": {},
    "no_match_update": {"signal_name": "Europe Trip", "days_active": 14},
    "inactive_reengagement": {"days_inactive": 21},
}


class TestAllTemplatesRender:
    """Verify all 15 templates render and contain expected elements."""

    @pytest.mark.parametrize("template_name", list(_SAMPLE_CONTEXTS.keys()))
    def test_renders_without_error(self, template_name):
        from app.services.email_templates import templates
        fn = getattr(templates, template_name)
        ctx = _SAMPLE_CONTEXTS[template_name]
        subject, html = fn(user=_user(), context=ctx)
        assert isinstance(subject, str) and len(subject) > 0
        assert isinstance(html, str) and len(html) > 100

    @pytest.mark.parametrize("template_name", list(_SAMPLE_CONTEXTS.keys()))
    def test_brand_name_is_trip_signal(self, template_name):
        """All templates must use 'Trip Signal' (with space) in display text."""
        from app.services.email_templates import templates
        fn = getattr(templates, template_name)
        ctx = _SAMPLE_CONTEXTS[template_name]
        subject, html = fn(user=_user(), context=ctx)
        # Strip domain references — only check display text
        cleaned = (subject + html).replace("tripsignal.ca", "").replace("email-logo.png", "")
        assert "TripSignal" not in cleaned, (
            f"Template '{template_name}' uses 'TripSignal' without space"
        )

    @pytest.mark.parametrize("template_name", list(_SAMPLE_CONTEXTS.keys()))
    def test_contains_website_link(self, template_name):
        from app.services.email_templates import templates
        fn = getattr(templates, template_name)
        _, html = fn(user=_user(), context=_SAMPLE_CONTEXTS[template_name])
        assert "tripsignal.ca" in html

    @pytest.mark.parametrize("template_name", [
        "welcome", "first_signal", "no_signal_reminder",
        "match_alert", "major_drop_alert",
        "trial_expiring_soon", "trial_expired_upsell",
        "pro_activated", "payment_failed", "payment_failed_reminder",
        "subscription_canceled", "no_match_update", "inactive_reengagement",
    ])
    def test_has_single_cta_button(self, template_name):
        """Each non-exit template should have exactly one CTA button."""
        from app.services.email_templates import templates
        fn = getattr(templates, template_name)
        _, html = fn(user=_user(), context=_SAMPLE_CONTEXTS[template_name])
        count = html.count("background:#F97316")
        assert count == 1, f"Template '{template_name}' has {count} CTA buttons, expected 1"

    def test_exit_templates_have_no_cta_button(self):
        """Deletion confirmation emails don't need a CTA button."""
        from app.services.email_templates import templates
        for name in ("account_deleted_free", "account_deleted_pro"):
            _, html = getattr(templates, name)(user=_user(), context={})
            assert html.count("background:#F97316") == 0

    @pytest.mark.parametrize("template_name", ["match_alert", "major_drop_alert"])
    def test_alert_templates_have_pricing_disclaimer(self, template_name):
        from app.services.email_templates import templates
        fn = getattr(templates, template_name)
        _, html = fn(user=_user(), context=_SAMPLE_CONTEXTS[template_name])
        assert "does not sell travel" in html


# ═══════════════════════════════════════════════════════════════════════════════
# BASE HTML HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaseHelpers:

    def test_stars_html_none(self):
        from app.services.email_templates.base import stars_html
        assert stars_html(None) == ""

    def test_stars_html_4(self):
        from app.services.email_templates.base import stars_html
        result = stars_html(4.0)
        assert "★★★★" in result
        assert "½" not in result

    def test_stars_html_4_5(self):
        from app.services.email_templates.base import stars_html
        result = stars_html(4.5)
        assert "★★★★½" in result

    def test_stars_html_5(self):
        from app.services.email_templates.base import stars_html
        result = stars_html(5.0)
        assert "★★★★★" in result

    def test_format_price(self):
        from app.services.email_templates.base import format_price
        assert format_price(89900) == "$899"
        assert format_price(149900) == "$1,499"
        assert format_price(None) == ""

    def test_pricing_disclaimer_content(self):
        from app.services.email_templates.base import pricing_disclaimer
        text = pricing_disclaimer()
        assert "per person" in text
        assert "double occupancy" in text
        assert "Trip Signal" in text
        assert "does not sell travel" in text

    def test_new_low_banner(self):
        from app.services.email_templates.base import new_low_banner
        html = new_low_banner()
        assert "All-time low" in html

    def test_price_drop_banner(self):
        from app.services.email_templates.base import price_drop_banner
        html = price_drop_banner(15)
        assert "15%" in html


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistry:

    def test_all_types_registered(self):
        from app.services.email_templates import _REGISTRY
        for et in EmailType:
            assert et in _REGISTRY, f"Missing template for {et}"

    def test_all_types_have_variables_entry(self):
        from app.services.email_templates import TEMPLATE_VARIABLES
        for et in EmailType:
            assert et in TEMPLATE_VARIABLES, f"Missing TEMPLATE_VARIABLES for {et}"
