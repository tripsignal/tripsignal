"""Market coverage and activity metrics."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.services.market_intel.types import freshness_cutoff


def compute_market_coverage(db: Session) -> dict:
    """Compute global market coverage metrics (for header)."""
    cutoff = freshness_cutoff()
    today = date.today()

    row = db.execute(
        select(
            func.count(Deal.id),
            func.count(func.distinct(Deal.hotel_name)),
            func.count(func.distinct(Deal.origin)),
            func.count(func.distinct(Deal.destination)),
        )
        .where(Deal.is_active == True)  # noqa: E712
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).one()

    return {
        "unique_packages_tracked": row[0] or 0,
        "unique_resorts_tracked": row[1] or 0,
        "departures_count": row[2] or 0,
        "destinations_count": row[3] or 0,
    }


def compute_market_activity(db: Session) -> dict:
    """Compute market activity metrics (price drops in last 24h)."""
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    price_drops = db.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT deal_id
            FROM (
                SELECT
                    deal_id,
                    price_cents,
                    LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                    ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
                FROM deal_price_history
                WHERE recorded_at >= :cutoff
            ) sub
            WHERE rn = 1 AND prev_price IS NOT NULL AND price_cents < prev_price
        ) drops
    """), {"cutoff": cutoff_24h}).scalar() or 0

    return {
        "price_drops_today": price_drops,
    }
