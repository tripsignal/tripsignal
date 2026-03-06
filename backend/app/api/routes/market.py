"""Market intelligence endpoints."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
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
    compute_market_stats,
    compute_trigger_likelihood,
)

logger = logging.getLogger("market")

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview")
async def market_overview(db: Session = Depends(get_db)):
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


@router.get("/signal/{signal_id}/intelligence")
async def signal_market_intelligence(
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
async def draft_signal_insights(
    draft: dict,
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

    insights = compute_draft_signal_insights(db, draft)
    if not insights:
        return {"insights": None}

    return {"insights": insights}
