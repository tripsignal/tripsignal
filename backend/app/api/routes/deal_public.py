"""Public deal page endpoint — no auth required."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.session import get_db
from app.services.market_intel import (
    MarketBucket,
    compute_market_stats,
    duration_to_bucket,
    score_deal,
    star_to_bucket,
    _dest_label,
)
from app.workers.selloff_scraper import AIRPORT_CITY_MAP

logger = logging.getLogger("deal_public")

router = APIRouter(prefix="/api/deals", tags=["deals_public"])


def _get_price_delta(db: Session, deal_id: UUID) -> int | None:
    """Get the most recent price drop for a single deal (positive = drop)."""
    row = db.execute(text("""
        WITH recent AS (
            SELECT price_cents,
                   LAG(price_cents) OVER (ORDER BY recorded_at ASC) as prev_price,
                   ROW_NUMBER() OVER (ORDER BY recorded_at DESC) as rn
            FROM deal_price_history
            WHERE deal_id = :deal_id
        )
        SELECT (prev_price - price_cents) as delta
        FROM recent
        WHERE rn = 1 AND prev_price IS NOT NULL AND prev_price > price_cents
    """), {"deal_id": str(deal_id)}).scalar()
    return row if row else None


@router.get("/{deal_id}/public")
async def get_public_deal(deal_id: UUID, db: Session = Depends(get_db)):
    """Public deal page data. No auth required."""
    deal = db.query(Deal).filter(Deal.id == deal_id, Deal.is_active == True).first()  # noqa: E712
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    duration_days = (
        (deal.return_date - deal.depart_date).days
        if deal.return_date and deal.depart_date
        else None
    )

    # Build market bucket for value scoring
    value_score = None
    dur_bucket = duration_to_bucket(duration_days) if duration_days else None
    star_bucket = star_to_bucket(deal.star_rating)

    if dur_bucket:
        bucket = MarketBucket(
            origin=deal.origin,
            destination=deal.destination,
            duration_bucket=dur_bucket,
            star_bucket=star_bucket,
        )
        stats = compute_market_stats(db, bucket)
        if stats.sample_size > 0:
            score = score_deal(deal.price_cents, stats)
            value_score = score.__dict__.copy()

    # Price delta from history
    price_delta_cents = _get_price_delta(db, deal.id)

    # Human-readable labels
    origin_label = AIRPORT_CITY_MAP.get(deal.origin, deal.origin)
    destination_label = _dest_label(deal.destination)

    return {
        "id": str(deal.id),
        "hotel_name": deal.hotel_name,
        "destination": deal.destination,
        "destination_label": destination_label,
        "origin": deal.origin,
        "origin_label": origin_label,
        "depart_date": deal.depart_date.isoformat() if deal.depart_date else None,
        "return_date": deal.return_date.isoformat() if deal.return_date else None,
        "duration_days": duration_days,
        "price_cents": deal.price_cents,
        "currency": deal.currency,
        "star_rating": deal.star_rating,
        "deeplink_url": deal.deeplink_url,
        "destination_str": deal.destination_str,
        "provider": deal.provider,
        "value_score": value_score,
        "price_delta_cents": price_delta_cents,
    }
