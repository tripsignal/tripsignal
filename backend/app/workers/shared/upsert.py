"""Shared deal upsert logic used by all scrapers."""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory

logger = logging.getLogger(__name__)


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
        if existing.price_cents != deal["price_cents"]:
            existing.price_cents = deal["price_cents"]
            db.commit()
        delta = old_price - deal["price_cents"]
        existing._price_dropped = delta > 0
        existing._price_delta = delta
        db.add(DealPriceHistory(deal_id=existing.id, price_cents=deal["price_cents"]))
        db.commit()
        return existing

    new_deal = Deal(
        provider=provider,
        origin=deal["gateway"],
        destination=deal["region"] or deal.get("destination_str", ""),
        depart_date=deal["depart_date"],
        return_date=deal["return_date"],
        price_cents=deal["price_cents"],
        currency="CAD",
        deeplink_url=deal.get("deeplink_url"),
        dedupe_key=dedupe_key,
        hotel_name=deal.get("hotel_name"),
        hotel_id=deal.get("hotel_id"),
        discount_pct=deal.get("discount_pct"),
        destination_str=deal.get("destination_str"),
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
