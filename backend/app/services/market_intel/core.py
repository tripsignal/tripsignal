"""Core market queries and stats computation."""
import math
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.services.market_intel.types import (
    DURATION_BUCKETS,
    STAR_BUCKETS,
    MarketBucket,
    MarketStats,
    compute_percentile,
    freshness_cutoff,
)


def _base_fresh_deals_query():
    """Return a base query for fresh, active deals."""
    cutoff = freshness_cutoff()
    return (
        select(Deal)
        .where(Deal.is_active == True)  # noqa: E712
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= date.today())
    )


def deals_in_bucket(
    db: Session, bucket: MarketBucket, ignore_star: bool = False, limit: int = 2000,
) -> list[Deal]:
    """Fetch active, fresh deals matching a market bucket.

    Results are ordered by price (cheapest first) and capped at *limit* rows
    to prevent unbounded memory usage on large buckets.
    """
    stmt = _base_fresh_deals_query()
    stmt = stmt.where(Deal.origin == bucket.origin)
    stmt = stmt.where(Deal.destination == bucket.destination)

    # Duration filter
    dur_range = DURATION_BUCKETS.get(bucket.duration_bucket)
    if dur_range:
        lo, hi = dur_range
        # Duration = return_date - depart_date
        stmt = stmt.where(Deal.return_date.isnot(None))
        stmt = stmt.where(
            (Deal.return_date - Deal.depart_date).between(lo, hi)
        )

    # Star filter (optional)
    if not ignore_star:
        if bucket.min_star_rating is not None:
            stmt = stmt.where(Deal.star_rating.isnot(None))
            stmt = stmt.where(Deal.star_rating >= bucket.min_star_rating)
        elif bucket.star_bucket:
            star_range = STAR_BUCKETS.get(bucket.star_bucket)
            if star_range:
                lo, hi = star_range
                stmt = stmt.where(Deal.star_rating.isnot(None))
                stmt = stmt.where(Deal.star_rating.between(lo, hi))

    stmt = stmt.order_by(Deal.price_cents).limit(limit)
    return db.execute(stmt).scalars().all()


def compute_market_stats(db: Session, bucket: MarketBucket) -> MarketStats:
    """Compute distribution statistics for a market bucket."""
    deals = deals_in_bucket(db, bucket)

    if not deals:
        return MarketStats()

    prices = sorted([d.price_cents for d in deals if d.price_cents and d.price_cents > 0])
    if not prices:
        return MarketStats()

    # Cross-provider dedup by package key for unique counts
    package_keys: set[str] = set()
    resort_names: set[str] = set()
    for d in deals:
        duration = (d.return_date - d.depart_date).days if d.return_date else 7
        pkg_key = f"{d.hotel_id or d.hotel_name or 'unk'}:{d.origin}:{d.depart_date}:{duration}"
        package_keys.add(pkg_key)
        if d.hotel_name:
            resort_names.add(d.hotel_name.lower().strip())

    n = len(prices)
    mean = sum(prices) / n
    variance = sum((p - mean) ** 2 for p in prices) / n if n > 1 else 0
    stddev = math.sqrt(variance) if variance > 0 else None

    return MarketStats(
        sample_size=n,
        unique_package_count=len(package_keys),
        unique_resort_count=len(resort_names),
        min_price=prices[0],
        p25_price=compute_percentile(prices, 0.25),
        median_price=compute_percentile(prices, 0.50),
        p75_price=compute_percentile(prices, 0.75),
        max_price=prices[-1],
        price_stddev=stddev,
        prices=prices,
    )
