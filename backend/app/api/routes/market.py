"""Market intelligence endpoints."""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db
from app.services.market_intel import (
    build_market_bucket_from_signal,
    build_spectrum_data,
    compute_draft_signal_insights,
    compute_empty_state_insights,
    compute_market_activity,
    compute_market_coverage,
    compute_market_events,
    compute_market_stats,
    compute_top_destinations,
    compute_trigger_likelihood,
)


class _DraftDeparture(BaseModel):
    airports: list[str] = Field(default_factory=list, max_length=10)

class _DraftDestination(BaseModel):
    regions: list[str] = Field(default_factory=list, max_length=20)

class _DraftTravelWindow(BaseModel):
    start_month: Optional[str] = None
    end_month: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    min_nights: Optional[int] = Field(default=None, ge=1, le=30)
    max_nights: Optional[int] = Field(default=None, ge=1, le=30)

class _DraftPreferences(BaseModel):
    min_star_rating: Optional[float] = Field(default=None, ge=0, le=5)

class _DraftBudget(BaseModel):
    target_pp: Optional[int] = Field(default=None, ge=0, le=100000)

class DraftSignalRequest(BaseModel):
    departure: Optional[_DraftDeparture] = None
    destination: Optional[_DraftDestination] = None
    travel_window: Optional[_DraftTravelWindow] = None
    preferences: Optional[_DraftPreferences] = None
    budget: Optional[_DraftBudget] = None

logger = logging.getLogger("market")

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview")
@limiter.limit("30/minute")
async def market_overview(request: Request, db: Session = Depends(get_db)):
    """Public market overview metrics for the signals page header."""
    coverage = compute_market_coverage(db)
    activity = compute_market_activity(db)

    return {
        "total_packages": coverage["unique_packages_tracked"],
        "total_resorts": coverage["unique_resorts_tracked"],
        "departures_count": coverage["departures_count"],
        "destinations_count": coverage["destinations_count"],
        "price_drops_today": activity["price_drops_today"],
    }


@router.get("/events")
@limiter.limit("30/minute")
async def market_events(request: Request, db: Session = Depends(get_db)):
    """Today's signals and market movers. Public endpoint."""
    return compute_market_events(db)


@router.get("/top-destinations/{origin}")
@limiter.limit("30/minute")
async def top_destinations(
    request: Request,
    origin: str,
    db: Session = Depends(get_db),
):
    """Top destinations by deal count for a departure airport. Public endpoint."""
    destinations = compute_top_destinations(db, origin.upper(), limit=3)
    return {"origin": origin.upper(), "destinations": destinations}


@router.get("/signal/{signal_id}/intelligence")
@limiter.limit("20/minute")
async def signal_market_intelligence(
    request: Request,
    signal_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Per-signal market intelligence: stats, spectrum, scoring, trigger likelihood."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    signal = db.query(Signal).filter(
        Signal.id == signal_id, Signal.user_id == user.id
    ).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    bucket = build_market_bucket_from_signal(signal)
    if not bucket:
        return {"market_stats": None, "spectrum": None}

    stats = compute_market_stats(db, bucket)

    result: dict = {
        "market_bucket": {
            "origin": bucket.origin,
            "destination": bucket.destination,
            "duration_bucket": bucket.duration_bucket,
            "star_bucket": bucket.star_bucket,
        },
        "market_stats": stats.to_dict(),
        "spectrum": build_spectrum_data(stats),
    }

    # Trigger likelihood
    likelihood = compute_trigger_likelihood(db, signal, bucket, stats)
    result["trigger_likelihood"] = likelihood.to_dict()

    # Empty-state insights (only if signal has no matches)
    match_count = db.execute(
        select(func.count(DealMatch.id)).where(DealMatch.signal_id == signal.id)
    ).scalar() or 0

    if match_count == 0:
        empty_insights = compute_empty_state_insights(db, signal, bucket)
        result["empty_state"] = empty_insights.to_dict()

    return result


@router.post("/draft/insights")
@limiter.limit("20/minute")
async def draft_signal_insights(
    request: Request,
    draft: DraftSignalRequest,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Market intelligence for a draft signal during Create Signal flow.

    Accepts a partial signal config and returns market stats, coverage,
    and spectrum data for the matching market bucket.
    """
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    insights = compute_draft_signal_insights(db, draft.model_dump(exclude_none=True))
    if not insights:
        return {"insights": None}

    return {"insights": insights}
