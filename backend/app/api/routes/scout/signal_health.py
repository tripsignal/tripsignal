"""Scout signal-health endpoint — per-signal health overview."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.session import get_db

from .helpers import _get_user_and_signals

router = APIRouter()


@router.get("/signal-health")
async def signal_health(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Per-signal health overview: matches, trend, freshness."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"signals": []}

    signal_ids = [s.id for s in signals]

    # Batch match counts
    match_counts = dict(
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)
        .group_by(DealMatch.signal_id)
        .all()
    )

    # Intel caches
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    result = []
    for s in signals:
        active = match_counts.get(s.id, 0)
        intel = intel_map.get(s.id)

        # Freshness: how recently did we last check
        freshness = "stale"
        if s.last_check_at:
            hours_ago = (datetime.now(timezone.utc) - s.last_check_at).total_seconds() / 3600
            if hours_ago < 12:
                freshness = "fresh"
            elif hours_ago < 36:
                freshness = "recent"

        # Health status
        if active == 0:
            health = "no_matches"
        elif intel and intel.trend_direction == "falling":
            health = "improving"
        elif intel and intel.trend_direction == "rising":
            health = "worsening"
        else:
            health = "stable"

        result.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "departure_airports": s.departure_airports or [],
            "destination_regions": s.destination_regions or [],
            "active_matches": active,
            "health": health,
            "freshness": freshness,
            "last_check_at": s.last_check_at.isoformat() if s.last_check_at else None,
            "last_check_min_price": s.last_check_min_price,
            "all_time_low_price": s.all_time_low_price,
            "all_time_low_at": s.all_time_low_at.isoformat() if s.all_time_low_at else None,
            "trend_direction": intel.trend_direction if intel else None,
            "trend_consecutive_weeks": intel.trend_consecutive_weeks if intel else None,
            "value_score": intel.value_score if intel else None,
            "floor_proximity_pct": intel.floor_proximity_pct if intel else None,
        })

    return {"signals": result}
