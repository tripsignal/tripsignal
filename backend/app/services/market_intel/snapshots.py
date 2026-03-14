"""Daily market snapshot generation."""
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.market_snapshot import MarketSnapshot
from app.services.market_intel.types import DURATION_BUCKETS, MarketBucket, freshness_cutoff
from app.services.market_intel.core import compute_market_stats

logger = logging.getLogger("market_intel")


def generate_daily_snapshots(db: Session) -> int:
    """Generate daily market snapshot rows for all active market buckets.

    Iterates every distinct (origin, destination) pair with active deals,
    computes stats per duration bucket, and inserts a snapshot row for each.

    Returns: number of snapshot rows created.
    """
    today = date.today()
    cutoff = freshness_cutoff()

    # Find all distinct active routes
    routes = db.execute(
        select(
            func.distinct(Deal.origin),
            Deal.destination,
        )
        .where(Deal.is_active == True)  # noqa: E712
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).all()

    created = 0
    batch_size = 50
    pending = 0

    for origin, destination in routes:
        for dur_key in DURATION_BUCKETS:
            bucket = MarketBucket(
                origin=origin,
                destination=destination,
                duration_bucket=dur_key,
                star_bucket=None,
            )
            stats = compute_market_stats(db, bucket)
            if stats.sample_size == 0:
                continue

            snapshot = MarketSnapshot(
                snapshot_date=today,
                departure_airport=origin,
                destination_region=destination,
                duration_bucket=dur_key,
                star_bucket=None,
                package_count=stats.sample_size,
                unique_resort_count=stats.unique_resort_count,
                min_price=stats.min_price,
                median_price=stats.median_price,
                p75_price=stats.p75_price,
                max_price=stats.max_price,
                price_stddev=stats.price_stddev,
            )
            db.add(snapshot)
            created += 1
            pending += 1

            if pending >= batch_size:
                try:
                    db.commit()
                except Exception:
                    logger.exception("Failed to commit snapshot batch at %d", created)
                    db.rollback()
                    return created - pending
                pending = 0

    # Commit remaining
    if pending > 0:
        try:
            db.commit()
        except Exception:
            logger.exception("Failed to commit final snapshot batch")
            db.rollback()
            return created - pending

    logger.info("Generated %d daily market snapshots for %s", created, today)
    return created
