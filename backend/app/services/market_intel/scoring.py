"""Deal value scoring against market buckets."""
import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.services.market_intel.types import (
    DURATION_BUCKETS,
    DealValueScore,
    GAP_GREAT_ABS,
    GAP_GREAT_PCT,
    GAP_RARE_ABS,
    GAP_RARE_PCT,
    MarketBucket,
    MarketStats,
    ZSCORE_GOOD,
    ZSCORE_GREAT,
    ZSCORE_RARE,
    duration_to_bucket,
    freshness_cutoff,
    star_to_bucket,
)
from app.services.market_intel.core import compute_market_stats

logger = logging.getLogger("market_intel")


def score_deal(price_cents: int, stats: MarketStats) -> DealValueScore:
    """Score a single deal against its market bucket distribution."""
    result = DealValueScore(comparable_sample_size=stats.sample_size)

    if not stats.is_scorable() or stats.median_price is None or stats.price_stddev is None:
        # Not enough data for meaningful scoring
        if stats.median_price is not None:
            delta = stats.median_price - price_cents
            result.price_delta_amount = abs(delta)
            result.price_delta_direction = "below" if delta > 0 else "above"
        return result

    # Z-score (positive = below median = good deal)
    z = (stats.median_price - price_cents) / stats.price_stddev
    result.z_score = z

    # Price delta vs typical
    delta = stats.median_price - price_cents
    result.price_delta_amount = abs(delta)
    result.price_delta_direction = "below" if delta > 0 else "above"

    # Gap validation
    sorted_prices = stats.prices
    price_gap = 0
    price_gap_pct = 0.0
    if len(sorted_prices) >= 2 and price_cents <= sorted_prices[0]:
        price_gap = sorted_prices[1] - sorted_prices[0]
        price_gap_pct = price_gap / sorted_prices[1] if sorted_prices[1] > 0 else 0

    # Assign label with gap validation
    if z >= ZSCORE_RARE and (price_gap >= GAP_RARE_ABS or price_gap_pct >= GAP_RARE_PCT):
        result.label = "Rare value"
    elif z >= ZSCORE_GREAT and (price_gap >= GAP_GREAT_ABS or price_gap_pct >= GAP_GREAT_PCT):
        result.label = "Great value"
    elif z >= ZSCORE_GOOD:
        result.label = "Good price"
    elif z >= -0.5:
        result.label = "Typical price"
    else:
        result.label = "High for market"

    # Suppress strong labels if sample is weak
    if not stats.is_strong() and result.label in ("Rare value",):
        result.label = "Great value"

    return result


def _deal_bucket_key(deal: Deal) -> tuple:
    """Return a hashable key for a deal's market bucket."""
    duration = (deal.return_date - deal.depart_date).days if deal.return_date else 7
    dur_bucket = duration_to_bucket(duration) or "one_week"
    star_bkt = star_to_bucket(deal.star_rating)
    return (deal.origin, deal.destination, dur_bucket, star_bkt)


def score_deal_for_match(
    db: Session,
    deal: Deal,
    stats_cache: Optional[dict[tuple, MarketStats]] = None,
) -> Optional[str]:
    """Score a deal against its market bucket and return the value label.

    Returns only positive labels ('Rare value', 'Great value') or None.
    Used when creating DealMatch records to store the label at match time.

    Pass a stats_cache dict to avoid recomputing market stats for deals in
    the same bucket during a batch operation (e.g. scraper run).
    """
    if not deal.price_cents:
        return None

    cache_key = _deal_bucket_key(deal)

    if stats_cache is not None and cache_key in stats_cache:
        stats = stats_cache[cache_key]
    else:
        duration = (deal.return_date - deal.depart_date).days if deal.return_date else 7
        dur_bucket = duration_to_bucket(duration) or "one_week"
        star_bkt = star_to_bucket(deal.star_rating)
        bucket = MarketBucket(
            origin=deal.origin,
            destination=deal.destination,
            duration_bucket=dur_bucket,
            star_bucket=star_bkt,
        )
        stats = compute_market_stats(db, bucket)
        if stats_cache is not None:
            stats_cache[cache_key] = stats

    if not stats.is_scorable():
        return None

    result = score_deal(deal.price_cents, stats)

    # Only return positive labels — neutral/negative are not shown
    if result.label in ("Rare value", "Great value"):
        return result.label
    return None


def score_deal_resort_anomaly(
    db: Session, deal: Deal, price_cents: int
) -> tuple[bool, Optional[float]]:
    """Check if this deal is unusually cheap for the same resort across other dates.

    Groups by: hotel_id + origin + duration_bucket
    Returns: (is_anomaly, discount_pct)
    """
    if not deal.hotel_id:
        return False, None

    duration = (deal.return_date - deal.depart_date).days if deal.return_date else None
    if not duration:
        return False, None

    dur_bucket = duration_to_bucket(duration)
    if not dur_bucket:
        return False, None

    dur_range = DURATION_BUCKETS[dur_bucket]
    cutoff = freshness_cutoff()

    # Find all prices for the same resort in the same context
    stmt = (
        select(Deal.price_cents)
        .where(Deal.is_active == True)  # noqa: E712
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.hotel_id == deal.hotel_id)
        .where(Deal.origin == deal.origin)
        .where(Deal.return_date.isnot(None))
        .where(
            (Deal.return_date - Deal.depart_date)
            .between(dur_range[0], dur_range[1])
        )
        .where(Deal.id != deal.id)  # Exclude the deal itself
    )
    other_prices = [row[0] for row in db.execute(stmt).all() if row[0] and row[0] > 0]

    if len(other_prices) < 3:
        return False, None

    sorted_prices = sorted(other_prices)
    n = len(sorted_prices)
    mid = n // 2
    resort_median = sorted_prices[mid] if n % 2 == 1 else (sorted_prices[mid - 1] + sorted_prices[mid]) // 2

    if resort_median <= 0:
        return False, None

    discount_pct = (resort_median - price_cents) / resort_median * 100

    # Only flag if >10% below resort median
    if discount_pct >= 10:
        return True, discount_pct

    return False, None
