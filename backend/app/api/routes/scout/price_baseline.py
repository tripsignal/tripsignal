"""Scout price-baseline endpoint — price distribution across user's signals."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.session import get_db
from app.services.market_intel import (
    build_market_bucket_from_signal,
    build_spectrum_data,
    compute_market_stats,
    score_deal,
)

from .helpers import _get_user_and_signals

router = APIRouter()


@router.get("/price-baseline")
async def price_baseline(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Price distribution across user's signals — where do their deals sit?"""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"baselines": []}

    result = []
    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if not bucket:
            continue

        stats = compute_market_stats(db, bucket)
        spectrum = build_spectrum_data(stats, s.last_check_min_price)

        # Score the user's best deal if they have one
        value_label = None
        if s.last_check_min_price and stats.is_scorable():
            score_result = score_deal(s.last_check_min_price, stats)
            value_label = score_result.label

        result.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "spectrum": spectrum,
            "value_label": value_label,
            "best_price_cents": s.last_check_min_price,
            "all_time_low_cents": s.all_time_low_price,
            "sample_size": stats.sample_size,
        })

    return {"baselines": result}
