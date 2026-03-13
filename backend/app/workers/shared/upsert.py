"""Shared deal upsert logic used by all scrapers."""

import html as html_module
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory

logger = logging.getLogger(__name__)

# Matches HTML tags (including self-closing)
_HTML_TAG_RE = re.compile(r"<[^>]{1,200}>")
# Matches control characters except tab, newline, carriage return
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Collapses whitespace runs
_WHITESPACE_RE = re.compile(r"\s+")


def _sanitize_text(value: str | None, max_len: int = 500) -> str | None:
    """Sanitize a scraped text field for safe storage.

    Strips HTML tags, decodes HTML entities, removes control characters,
    collapses whitespace, and enforces a length limit.
    """
    if not value:
        return value
    s = _HTML_TAG_RE.sub("", value)
    s = html_module.unescape(s)
    s = _CONTROL_CHAR_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s[:max_len] if s else None


def _sanitize_url(value: str | None, max_len: int = 2000) -> str | None:
    """Validate and sanitize a URL field. Only allows http/https schemes."""
    if not value:
        return value
    s = value.strip()[:max_len]
    if not s.startswith(("https://", "http://")):
        return None
    return s


def upsert_deal(db: Session, provider: str, deal: dict) -> Optional[Deal]:
    """Create or update a deal. Returns the Deal object with _price_dropped/_price_delta attrs."""
    dedupe_key = deal["dedupe_key"]

    existing = db.execute(
        select(Deal).where(Deal.dedupe_key == dedupe_key)
    ).scalar_one_or_none()

    if existing:
        old_price = existing.price_cents
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.missed_cycles = 0
        if not existing.is_active:
            existing.is_active = True
            existing.deactivated_at = None
            existing.reactivated_at = datetime.now(timezone.utc)

        delta = old_price - deal["price_cents"]
        existing._price_dropped = delta > 0
        existing._price_delta = delta

        if existing.price_cents != deal["price_cents"]:
            existing.price_cents = deal["price_cents"]
            # Only record price history when the price actually changes
            db.add(DealPriceHistory(deal_id=existing.id, price_cents=deal["price_cents"]))

        db.commit()
        return existing

    new_deal = Deal(
        provider=provider,
        origin=deal["gateway"],
        destination=_sanitize_text(deal["region"] or deal.get("destination_str", ""), max_len=200),
        depart_date=deal["depart_date"],
        return_date=deal["return_date"],
        price_cents=deal["price_cents"],
        currency="CAD",
        deeplink_url=_sanitize_url(deal.get("deeplink_url")),
        dedupe_key=dedupe_key,
        hotel_name=_sanitize_text(deal.get("hotel_name"), max_len=300),
        hotel_id=deal.get("hotel_id"),
        discount_pct=deal.get("discount_pct"),
        destination_str=_sanitize_text(deal.get("destination_str"), max_len=200),
        star_rating=deal.get("star_rating"),
    )
    db.add(new_deal)
    db.commit()
    db.refresh(new_deal)
    new_deal._price_dropped = False
    new_deal._price_delta = 0
    db.add(DealPriceHistory(deal_id=new_deal.id, price_cents=new_deal.price_cents))
    db.commit()
    return new_deal
