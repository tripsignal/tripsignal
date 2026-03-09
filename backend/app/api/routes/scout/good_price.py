"""Scout what-is-a-good-price endpoint — educational price ranges."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.session import get_db
from app.services.market_intel import (
    build_market_bucket_from_signal,
    compute_market_stats,
)

from .helpers import _get_user_and_signals, _region_label

router = APIRouter()


@router.get("/what-is-a-good-price")
async def what_is_a_good_price(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Educational: price ranges for each of the user's signal routes."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"routes": []}

    routes = []
    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if not bucket:
            continue

        stats = compute_market_stats(db, bucket)
        if stats.sample_size < 3:
            continue

        # What label would a deal at each price point get?
        labels = {}
        for label_name, price in [
            ("great", stats.p25_price),
            ("typical", stats.median_price),
            ("high", stats.p75_price),
        ]:
            if price:
                labels[label_name] = price

        routes.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "origins": s.departure_airports or [],
            "destinations": [_region_label(r) for r in (s.destination_regions or [])],
            "great_price_cents": stats.p25_price,
            "typical_price_cents": stats.median_price,
            "high_price_cents": stats.p75_price,
            "floor_price_cents": stats.min_price,
            "sample_size": stats.sample_size,
            "unique_resorts": stats.unique_resort_count,
        })

    return {"routes": routes}
