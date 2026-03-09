"""Scout market-context endpoint — platform-wide market context."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.route_intel_cache import RouteIntelCache
from app.db.session import get_db

from .helpers import _get_user_and_signals, _region_label

router = APIRouter()


@router.get("/market-context")
async def market_context(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Platform-wide market context relevant to the user's signals."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    # Total active deals platform-wide
    total_active = db.query(func.count(Deal.id)).filter(Deal.is_active == True).scalar() or 0

    # Deals tracked today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    deals_today = (
        db.query(func.count(Deal.id))
        .filter(Deal.found_at >= today_start)
        .scalar()
    ) or 0

    # Provider breakdown
    provider_counts = dict(
        db.query(Deal.provider, func.count(Deal.id))
        .filter(Deal.is_active == True)
        .group_by(Deal.provider)
        .all()
    )

    # Top destinations by deal count
    top_dests = (
        db.query(Deal.destination, func.count(Deal.id).label("cnt"))
        .filter(Deal.is_active == True)
        .group_by(Deal.destination)
        .order_by(func.count(Deal.id).desc())
        .limit(5)
        .all()
    )

    # Route intel for user's routes — WoW trends
    route_trends = []
    if signals:
        user_routes: set[tuple[str, str]] = set()
        for s in signals:
            for apt in (s.departure_airports or []):
                for reg in (s.destination_regions or []):
                    user_routes.add((apt, reg))

        if user_routes:
            all_route_intel = db.query(RouteIntelCache).all()
            for ri in all_route_intel:
                if (ri.origin, ri.destination_region) in user_routes:
                    route_trends.append({
                        "origin": ri.origin,
                        "destination_region": ri.destination_region,
                        "destination_label": _region_label(ri.destination_region),
                        "week_over_week_pct": ri.week_over_week_pct,
                        "current_week_avg_cents": ri.current_week_avg_cents,
                        "late_booking_premium_pct": ri.late_booking_premium_pct,
                    })

    return {
        "total_active_deals": total_active,
        "deals_tracked_today": deals_today,
        "providers": provider_counts,
        "top_destinations": [
            {"destination": d[0], "label": _region_label(d[0]), "count": d[1]}
            for d in top_dests
        ],
        "route_trends": route_trends,
    }
