"""Scout destinations endpoint — per-destination price intelligence."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.route_intel_cache import RouteIntelCache
from app.db.session import get_db

from .helpers import _get_user_and_signals, _region_label

router = APIRouter()


@router.get("/destinations")
@limiter.limit("20/minute")
async def destinations(
    request: Request,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Per-destination price intelligence with sparkline data."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"destinations": []}

    signal_ids = [s.id for s in signals]

    # Collect unique (origin, destination_region) pairs from user's signals
    route_pairs: set[tuple[str, str]] = set()
    for s in signals:
        for apt in (s.departure_airports or []):
            for reg in (s.destination_regions or []):
                route_pairs.add((apt, reg))

    # Get matched deals grouped by destination
    matched_deals = (
        db.query(Deal)
        .join(DealMatch, DealMatch.deal_id == Deal.id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,
        )
        .all()
    )

    # Group deals by destination
    by_dest: dict[str, list[Deal]] = defaultdict(list)
    for d in matched_deals:
        by_dest[d.destination].append(d)

    # Get route intel for sparkline context
    route_intel_map: dict[tuple[str, str], RouteIntelCache] = {}
    if route_pairs:
        route_rows = db.query(RouteIntelCache).all()
        for r in route_rows:
            route_intel_map[(r.origin, r.destination_region)] = r

    # Get price history for sparklines — last 14 days, grouped by destination
    fourteen_days_ago = datetime.now(timezone.utc) - timedelta(days=14)
    dest_deal_ids = [d.id for deals in by_dest.values() for d in deals]

    sparkline_data: dict[str, list[dict]] = defaultdict(list)
    if dest_deal_ids:
        price_rows = (
            db.query(
                Deal.destination,
                func.date_trunc("day", DealPriceHistory.recorded_at).label("day"),
                func.min(DealPriceHistory.price_cents).label("min_price"),
            )
            .join(DealPriceHistory, DealPriceHistory.deal_id == Deal.id)
            .filter(
                Deal.id.in_(dest_deal_ids),
                DealPriceHistory.recorded_at >= fourteen_days_ago,
            )
            .group_by(Deal.destination, "day")
            .order_by("day")
            .all()
        )
        for row in price_rows:
            sparkline_data[row[0]].append({
                "date": row[1].strftime("%Y-%m-%d") if row[1] else None,
                "price_cents": row[2],
            })

    result = []
    for dest, deals in sorted(by_dest.items(), key=lambda x: len(x[1]), reverse=True):
        prices = [d.price_cents for d in deals]
        min_price = min(prices)
        median_price = sorted(prices)[len(prices) // 2]

        # Find WoW change from route intel
        wow_pct = None
        for (orig, reg), ri in route_intel_map.items():
            if reg == dest and ri.week_over_week_pct is not None:
                wow_pct = ri.week_over_week_pct
                break

        result.append({
            "destination": dest,
            "destination_label": _region_label(dest),
            "deal_count": len(deals),
            "min_price_cents": min_price,
            "median_price_cents": median_price,
            "week_over_week_pct": wow_pct,
            "sparkline": sparkline_data.get(dest, []),
        })

    return {"destinations": result}
