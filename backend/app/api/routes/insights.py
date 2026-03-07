"""Insights page endpoints — deal radar and market pulse."""
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.session import get_db
from app.db.models.signal import Signal
from app.db.models.deal_match import DealMatch
from app.db.models.deal import Deal
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.route_intel_cache import RouteIntelCache
from app.db.models.market_snapshot import MarketSnapshot
from app.db.models.user import User
from app.services.market_intel import (
    compute_market_coverage,
    compute_market_activity,
    compute_market_events,
)

logger = logging.getLogger("insights")

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/deal-radar")
async def deal_radar(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Aggregated intelligence for all of the user's active signals."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch user's active signals
    signals = (
        db.query(Signal)
        .filter(Signal.user_id == user.id, Signal.status == "active")
        .all()
    )

    if not signals:
        return {
            "signals": [],
            "summary": {
                "total_signals": 0,
                "total_matches": 0,
                "total_favourites": 0,
                "signals_with_drops": 0,
            },
        }

    signal_ids = [s.id for s in signals]

    # Batch fetch intel caches
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {row.signal_id: row for row in intel_rows}

    # Batch fetch active match counts per signal
    match_counts = (
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
        )
        .group_by(DealMatch.signal_id)
        .all()
    )
    match_count_map = {row[0]: row[1] for row in match_counts}

    # Batch fetch favourite counts per signal
    fav_counts = (
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
            DealMatch.is_favourite == True,  # noqa: E712
        )
        .group_by(DealMatch.signal_id)
        .all()
    )
    fav_count_map = {row[0]: row[1] for row in fav_counts}

    # Batch fetch price drop counts per signal using window function
    signal_id_strs = [str(sid) for sid in signal_ids]
    drop_rows = db.execute(
        text("""
            SELECT dm.signal_id::text, COUNT(DISTINCT dm.deal_id) as drop_count
            FROM deal_matches dm
            JOIN deals d ON d.id = dm.deal_id
            JOIN (
                SELECT deal_id,
                       price_cents,
                       LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                       ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
                FROM deal_price_history
            ) ph ON ph.deal_id = dm.deal_id
            WHERE dm.signal_id = ANY(:signal_ids)
              AND d.is_active = true
              AND ph.rn = 1
              AND ph.prev_price IS NOT NULL
              AND ph.price_cents < ph.prev_price
            GROUP BY dm.signal_id
        """),
        {"signal_ids": signal_id_strs},
    ).all()
    drop_count_map = {row[0]: row[1] for row in drop_rows}

    # Build response
    signal_results = []
    total_matches = 0
    total_favourites = 0
    signals_with_drops = 0

    for s in signals:
        active = match_count_map.get(s.id, 0)
        favs = fav_count_map.get(s.id, 0)
        drops = drop_count_map.get(str(s.id), 0)

        total_matches += active
        total_favourites += favs
        if drops > 0:
            signals_with_drops += 1

        intel = intel_map.get(s.id)
        intel_dict = None
        if intel:
            intel_dict = {
                "value_score": intel.value_score,
                "trend_direction": intel.trend_direction,
                "trend_consecutive_weeks": intel.trend_consecutive_weeks,
                "trend_velocity": intel.trend_velocity,
                "trend_inflection": intel.trend_inflection,
                "inflection_pct_change": intel.inflection_pct_change,
                "best_value_nights": intel.best_value_nights,
                "best_value_pct_saving": intel.best_value_pct_saving,
                "star_price_anomaly_pct": intel.star_price_anomaly_pct,
                "hero_star_rating": intel.hero_star_rating,
                "floor_proximity_pct": intel.floor_proximity_pct,
                "min_price_ever_cents": intel.min_price_ever_cents,
                "current_deal_percentile": intel.current_deal_percentile,
                "total_matches": intel.total_matches,
            }

        signal_results.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "departure_airports": s.departure_airports or [],
            "destination_regions": s.destination_regions or [],
            "intel": intel_dict,
            "all_time_low_price": s.all_time_low_price,
            "all_time_low_at": s.all_time_low_at.isoformat() if s.all_time_low_at else None,
            "last_check_min_price": s.last_check_min_price,
            "active_matches": active,
            "favourite_count": favs,
            "price_drop_count": drops,
        })

    return {
        "signals": signal_results,
        "summary": {
            "total_signals": len(signals),
            "total_matches": total_matches,
            "total_favourites": total_favourites,
            "signals_with_drops": signals_with_drops,
        },
    }


@router.get("/market-pulse")
async def market_pulse(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Platform-wide market intelligence."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    coverage = compute_market_coverage(db)
    activity = compute_market_activity(db)
    events = compute_market_events(db)

    # Route intel — all cached rows
    route_rows = db.query(RouteIntelCache).all()
    route_intel = [
        {
            "origin": r.origin,
            "destination_region": r.destination_region,
            "cheapest_depart_week": r.cheapest_depart_week.isoformat() if r.cheapest_depart_week else None,
            "cheapest_week_avg_cents": r.cheapest_week_avg_cents,
            "priciest_depart_week": r.priciest_depart_week.isoformat() if r.priciest_depart_week else None,
            "priciest_week_avg_cents": r.priciest_week_avg_cents,
            "current_week_avg_cents": r.current_week_avg_cents,
            "prev_week_avg_cents": r.prev_week_avg_cents,
            "week_over_week_pct": r.week_over_week_pct,
            "late_booking_premium_pct": r.late_booking_premium_pct,
            "total_deals_analyzed": r.total_deals_analyzed,
        }
        for r in route_rows
    ]

    # Market snapshots — last 7 days (table may not exist yet)
    snapshots: list[dict] = []
    try:
        cutoff = date.today() - timedelta(days=7)
        snapshot_rows = (
            db.query(MarketSnapshot)
            .filter(MarketSnapshot.snapshot_date >= cutoff)
            .order_by(MarketSnapshot.snapshot_date.desc())
            .all()
        )
        snapshots = [
            {
                "snapshot_date": s.snapshot_date.isoformat(),
                "departure_airport": s.departure_airport,
                "destination_region": s.destination_region,
                "package_count": s.package_count,
                "min_price": s.min_price,
                "median_price": s.median_price,
            }
            for s in snapshot_rows
        ]
    except Exception:
        db.rollback()

    return {
        "plan_type": getattr(user, "plan_type", "free"),
        "coverage": coverage,
        "activity": activity,
        "events": events,
        "route_intel": route_intel,
        "snapshots": snapshots,
    }
