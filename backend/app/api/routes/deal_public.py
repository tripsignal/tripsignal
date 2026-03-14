"""Public deal page endpoint — no auth required."""
import json as json_mod
import logging
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory
from app.db.session import get_db
from app.services.formatting import dest_label, normalize_destination_display
from app.services.market_intel import (
    MarketBucket,
    compute_market_stats,
    duration_to_bucket,
    score_deal,
    star_to_bucket,
)
from app.services.signal_intel import NEARBY_AIRPORTS
from app.workers.selloff_scraper import AIRPORT_CITY_MAP

logger = logging.getLogger("deal_public")

router = APIRouter(prefix="/api/deals", tags=["deals_public"])


def _get_nearby_airport_saving(db: Session, deal: Deal) -> dict | None:
    """Check if the same hotel is cheaper from a nearby airport."""
    nearby = NEARBY_AIRPORTS.get(deal.origin, [])
    if not nearby:
        return None

    cheaper = db.query(Deal).filter(
        Deal.hotel_name == deal.hotel_name,
        Deal.depart_date == deal.depart_date,
        Deal.origin.in_(nearby),
        Deal.is_active == True,  # noqa: E712
        Deal.price_cents < deal.price_cents,
    ).order_by(Deal.price_cents.asc()).first()

    if not cheaper:
        return None

    saving = deal.price_cents - cheaper.price_cents
    if saving < 3000:  # Min $30 saving
        return None

    return {
        "airport_code": cheaper.origin,
        "airport_name": AIRPORT_CITY_MAP.get(cheaper.origin, cheaper.origin),
        "price_cents": cheaper.price_cents,
        "saving_cents": saving,
        "has_deeplink": cheaper.deeplink_url is not None,
        "deal_id": str(cheaper.id),
    }


def _get_date_shift_saving(db: Session, deal: Deal) -> dict | None:
    """Check if the same hotel/origin is cheaper on nearby dates."""
    if not deal.depart_date:
        return None

    window_start = deal.depart_date - timedelta(days=7)
    window_end = deal.depart_date + timedelta(days=7)

    cheaper = db.query(Deal).filter(
        Deal.hotel_name == deal.hotel_name,
        Deal.origin == deal.origin,
        Deal.depart_date >= window_start,
        Deal.depart_date <= window_end,
        Deal.depart_date != deal.depart_date,
        Deal.is_active == True,  # noqa: E712
        Deal.price_cents < deal.price_cents,
    ).order_by(Deal.price_cents.asc()).first()

    if not cheaper:
        return None

    saving = deal.price_cents - cheaper.price_cents
    if saving < 5000:  # Min $50 saving
        return None

    return {
        "alt_depart_date": cheaper.depart_date.isoformat(),
        "alt_return_date": cheaper.return_date.isoformat() if cheaper.return_date else None,
        "price_cents": cheaper.price_cents,
        "saving_cents": saving,
        "has_deeplink": cheaper.deeplink_url is not None,
        "deal_id": str(cheaper.id),
    }


def _get_budget_alternatives(db: Session, deal: Deal) -> list:
    """Find higher-rated deals at a slightly higher price on the same route+date."""
    if not deal.star_rating:
        return []

    ceiling = deal.price_cents + 15000  # Up to $150 more
    min_stars = float(deal.star_rating) + 0.5

    better = db.query(Deal).filter(
        Deal.destination == deal.destination,
        Deal.origin == deal.origin,
        Deal.depart_date == deal.depart_date,
        Deal.is_active == True,  # noqa: E712
        Deal.price_cents > deal.price_cents,
        Deal.price_cents <= ceiling,
        Deal.star_rating >= min_stars,
        Deal.hotel_name != deal.hotel_name,
    ).order_by(Deal.star_rating.desc(), Deal.price_cents.asc()).limit(3).all()

    return [{
        "hotel_name": d.hotel_name,
        "star_rating": float(d.star_rating) if d.star_rating else None,
        "price_cents": d.price_cents,
        "extra_cents": d.price_cents - deal.price_cents,
        "has_deeplink": d.deeplink_url is not None,
        "deal_id": str(d.id),
    } for d in better]


def _get_price_history_points(db: Session, deal_id) -> dict:
    """Get price history for this deal."""
    history = db.query(DealPriceHistory).filter(
        DealPriceHistory.deal_id == deal_id,
    ).order_by(DealPriceHistory.recorded_at.desc()).limit(30).all()

    if not history:
        return {"points": [], "all_time_low_cents": None, "all_time_high_cents": None}

    points = [{
        "price_cents": h.price_cents,
        "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
    } for h in reversed(history)]

    all_prices = [h.price_cents for h in history]

    return {
        "points": points,
        "all_time_low_cents": min(all_prices),
        "all_time_high_cents": max(all_prices),
    }


def _get_hotel_intel(db: Session, hotel_name: str) -> dict | None:
    """Look up hotel intelligence by name."""
    row = db.execute(text("""
        SELECT hotel_name, destination, star_rating, resort_size, adults_only,
               kids_club, kids_club_ages, teen_club, waterpark, waterpark_notes,
               num_restaurants, restaurant_names, transfer_time_minutes,
               nearest_airport_code, airport_transfer_included,
               sargassum_risk, sargassum_notes, vibe, total_rooms,
               accommodates_5, room_fit_for_5_type, room_types_for_5,
               connecting_rooms_available, max_occupancy_standard_room,
               beach_access, beach_type, beach_description,
               pool_count, pool_types,
               tripadvisor_rating, tripadvisor_review_count,
               top_complaints, top_praise, red_flags,
               primary_demographics, resort_layout, best_time_to_visit,
               official_website, resort_chain,
               babysitting_available, kids_pool, cribs_available
        FROM hotel_intel
        WHERE LOWER(hotel_name) = LOWER(:name)
        LIMIT 1
    """), {"name": hotel_name}).fetchone()

    if not row:
        return None

    def _jsonb(val):
        if val is None:
            return []
        if isinstance(val, list):
            return val
        try:
            return json_mod.loads(val)
        except Exception:
            return []

    return {
        "hotel_name": row.hotel_name,
        "destination": row.destination,
        "star_rating": float(row.star_rating) if row.star_rating else None,
        "resort_size": row.resort_size,
        "adults_only": row.adults_only,
        "kids_club": row.kids_club,
        "kids_club_ages": row.kids_club_ages,
        "teen_club": row.teen_club,
        "waterpark": row.waterpark,
        "waterpark_notes": row.waterpark_notes,
        "num_restaurants": row.num_restaurants,
        "restaurant_names": _jsonb(row.restaurant_names),
        "transfer_time_minutes": row.transfer_time_minutes,
        "nearest_airport_code": row.nearest_airport_code,
        "airport_transfer_included": row.airport_transfer_included,
        "sargassum_risk": row.sargassum_risk,
        "sargassum_notes": row.sargassum_notes,
        "vibe": row.vibe,
        "total_rooms": row.total_rooms,
        "accommodates_5": row.accommodates_5,
        "room_fit_for_5_type": row.room_fit_for_5_type,
        "room_types_for_5": _jsonb(row.room_types_for_5),
        "connecting_rooms_available": row.connecting_rooms_available,
        "max_occupancy_standard_room": row.max_occupancy_standard_room,
        "beach_access": row.beach_access,
        "beach_type": row.beach_type,
        "beach_description": row.beach_description,
        "pool_count": row.pool_count,
        "pool_types": _jsonb(row.pool_types),
        "tripadvisor_rating": float(row.tripadvisor_rating) if row.tripadvisor_rating else None,
        "tripadvisor_review_count": row.tripadvisor_review_count,
        "top_complaints": _jsonb(row.top_complaints),
        "top_praise": _jsonb(row.top_praise),
        "red_flags": _jsonb(row.red_flags),
        "primary_demographics": row.primary_demographics,
        "resort_layout": row.resort_layout,
        "best_time_to_visit": row.best_time_to_visit,
        "official_website": row.official_website,
        "resort_chain": row.resort_chain,
        "babysitting_available": row.babysitting_available,
        "kids_pool": row.kids_pool,
        "cribs_available": row.cribs_available,
    }


@router.get("/{deal_id}/public")
@limiter.limit("30/minute")
def get_public_deal(request: Request, deal_id: UUID, db: Session = Depends(get_db)):
    """Public deal page data. No auth required."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
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
            value_score = score.to_dict()
            # Add market stats for price spectrum bar
            value_score["market_min"] = stats.min_price
            value_score["market_median"] = stats.median_price
            value_score["market_max"] = stats.max_price
            # Compute percentile: % of deals this price is cheaper than
            if stats.prices:
                cheaper_count = sum(1 for p in stats.prices if p > deal.price_cents)
                value_score["percentile"] = round(cheaper_count / len(stats.prices) * 100)

    # Smart insights
    nearby_airport = _get_nearby_airport_saving(db, deal)
    date_shift = _get_date_shift_saving(db, deal)
    budget_alternatives = _get_budget_alternatives(db, deal)
    price_history = _get_price_history_points(db, deal.id)

    # Derive price delta from history (avoids a separate query)
    price_delta_cents = None
    if len(price_history["points"]) >= 2:
        current = price_history["points"][-1]["price_cents"]
        previous = price_history["points"][-2]["price_cents"]
        if previous > current:
            price_delta_cents = previous - current

    # Hotel intelligence
    hotel_intel = None
    if deal.hotel_name:
        hotel_intel = _get_hotel_intel(db, deal.hotel_name)

    # Human-readable labels
    origin_label = AIRPORT_CITY_MAP.get(deal.origin, deal.origin)
    destination_label = dest_label(deal.destination)

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
        "is_active": deal.is_active,
        "has_deeplink": deal.deeplink_url is not None,
        "destination_str": normalize_destination_display(deal.destination_str, deal.destination),
        "provider": deal.provider,
        "value_score": value_score,
        "price_delta_cents": price_delta_cents,
        "nearby_airport": nearby_airport,
        "date_shift": date_shift,
        "budget_alternatives": budget_alternatives,
        "price_history": price_history,
        "hotel_intel": hotel_intel,
    }


@router.get("/{deal_id}/go")
@limiter.limit("10/minute")
def redirect_to_deal(request: Request, deal_id: UUID, db: Session = Depends(get_db)):
    """Redirect to the deal provider's booking page. Prevents raw affiliate URL exposure."""
    deal = db.query(Deal).filter(Deal.id == deal_id, Deal.is_active == True).first()  # noqa: E712
    if not deal or not deal.deeplink_url:
        raise HTTPException(status_code=404, detail="Deal not found")
    return RedirectResponse(url=deal.deeplink_url, status_code=302)
