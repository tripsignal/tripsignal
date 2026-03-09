"""Book Window engine — analyzes price history to recommend book/wait/watch.

Uses three heuristics:
1. Trend Direction — are prices rising, declining, or stable?
2. Seasonal Pattern — is the current price below/above historical average for this booking window?
3. Inventory Pressure — is the number of available deals shrinking or growing?

Results are combined into a recommendation with confidence level.
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.schemas.book_window import BookWindowFactor, BookWindowOut, BookWindowResult

logger = logging.getLogger("book_window")

# Minimum price observations required to produce a recommendation
MIN_DATA_POINTS = 10
HIGH_CONFIDENCE_THRESHOLD = 50


def _get_price_snapshots(
    db: Session, signal_id: UUID, days: int = 90
) -> list[tuple[datetime, int]]:
    """Get price snapshots for deals matched to a signal, ordered by time.

    Returns list of (recorded_at, price_cents) tuples.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(DealPriceHistory.recorded_at, DealPriceHistory.price_cents)
        .join(DealMatch, DealMatch.deal_id == DealPriceHistory.deal_id)
        .where(
            DealMatch.signal_id == signal_id,
            DealPriceHistory.recorded_at >= cutoff,
        )
        .order_by(DealPriceHistory.recorded_at)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _compute_trend_direction(
    snapshots: list[tuple[datetime, int]],
) -> Optional[BookWindowFactor]:
    """Heuristic 1: Are prices rising, declining, or stable?

    Groups snapshots into scrape cycles (6-hour windows), computes average
    price per cycle, then checks the last 3+ cycles for direction.
    """
    if len(snapshots) < 6:
        return None

    # Group into 6-hour buckets
    buckets: dict[int, list[int]] = defaultdict(list)
    for ts, price in snapshots:
        bucket_key = int(ts.timestamp()) // (6 * 3600)
        buckets[bucket_key].append(price)

    if len(buckets) < 3:
        return None

    # Average price per cycle
    cycle_avgs = [
        (k, sum(prices) / len(prices))
        for k, prices in sorted(buckets.items())
    ]

    # Check last 4 cycles (or all if fewer)
    recent = cycle_avgs[-min(4, len(cycle_avgs)):]
    deltas = [recent[i + 1][1] - recent[i][1] for i in range(len(recent) - 1)]

    declining_count = sum(1 for d in deltas if d < 0)
    rising_count = sum(1 for d in deltas if d > 0)

    if declining_count >= 2 and declining_count > rising_count:
        return BookWindowFactor(
            name="trend_direction",
            signal="declining",
            description="Prices have dropped in recent scan cycles",
        )
    elif rising_count >= 2 and rising_count > declining_count:
        return BookWindowFactor(
            name="trend_direction",
            signal="rising",
            description="Prices have risen in recent scan cycles",
        )
    else:
        return BookWindowFactor(
            name="trend_direction",
            signal="stable",
            description="Prices are holding steady",
        )


def _compute_seasonal_pattern(
    db: Session, signal_id: UUID, snapshots: list[tuple[datetime, int]]
) -> Optional[BookWindowFactor]:
    """Heuristic 2: Is the current price favorable compared to historical average?

    Only applies when we have 30+ days of data.
    """
    if not snapshots:
        return None

    earliest = snapshots[0][0]
    latest = snapshots[-1][0]
    data_span_days = (latest - earliest).days

    if data_span_days < 30:
        return None

    prices = [p for _, p in snapshots]
    historical_avg = sum(prices) / len(prices)

    # Current average = last 3 days
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    recent_prices = [p for ts, p in snapshots if ts >= three_days_ago]
    if not recent_prices:
        return None

    current_avg = sum(recent_prices) / len(recent_prices)

    if current_avg < historical_avg * 0.95:
        return BookWindowFactor(
            name="seasonal_pattern",
            signal="favorable",
            description="Current prices are below the historical average for this route",
        )
    elif current_avg > historical_avg * 1.05:
        return BookWindowFactor(
            name="seasonal_pattern",
            signal="unfavorable",
            description="Current prices are above the historical average for this route",
        )
    else:
        return BookWindowFactor(
            name="seasonal_pattern",
            signal="neutral",
            description="Current prices are near the historical average",
        )


def _compute_inventory_pressure(
    snapshots: list[tuple[datetime, int]],
) -> Optional[BookWindowFactor]:
    """Heuristic 3: Is availability shrinking or growing?

    Counts unique deals per 6-hour cycle and compares recent vs earlier counts.
    """
    if len(snapshots) < 10:
        return None

    # Group by 6-hour bucket, count unique prices as proxy for unique deals
    # (since we don't have deal_id in snapshots, we use distinct prices as an approximation)
    buckets: dict[int, set[int]] = defaultdict(set)
    for ts, price in snapshots:
        bucket_key = int(ts.timestamp()) // (6 * 3600)
        buckets[bucket_key].append(price) if not isinstance(buckets[bucket_key], set) else buckets[bucket_key].add(price)

    sorted_keys = sorted(buckets.keys())
    if len(sorted_keys) < 4:
        return None

    # Compare first half vs second half of cycles
    mid = len(sorted_keys) // 2
    early_counts = [len(buckets[k]) for k in sorted_keys[:mid]]
    late_counts = [len(buckets[k]) for k in sorted_keys[mid:]]

    early_avg = sum(early_counts) / len(early_counts) if early_counts else 0
    late_avg = sum(late_counts) / len(late_counts) if late_counts else 0

    if early_avg > 0 and late_avg < early_avg * 0.75:
        return BookWindowFactor(
            name="inventory_pressure",
            signal="tightening",
            description="Fewer deals available in recent scans compared to earlier",
        )
    elif early_avg > 0 and late_avg > early_avg * 1.1:
        return BookWindowFactor(
            name="inventory_pressure",
            signal="growing",
            description="More deals available now than in earlier scans",
        )
    else:
        return BookWindowFactor(
            name="inventory_pressure",
            signal="stable",
            description="Deal availability is holding steady",
        )


def _compute_current_percentile(snapshots: list[tuple[datetime, int]]) -> float:
    """Where does the current price sit relative to all observed prices? 0.0 = cheapest ever."""
    if not snapshots:
        return 0.5

    prices = sorted(set(p for _, p in snapshots))
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    recent_prices = [p for ts, p in snapshots if ts >= three_days_ago]
    current = sum(recent_prices) / len(recent_prices) if recent_prices else prices[len(prices) // 2]

    if len(prices) <= 1:
        return 0.5

    below = sum(1 for p in prices if p < current)
    return below / (len(prices) - 1) if len(prices) > 1 else 0.5


def get_book_window(
    signal_id: UUID, signal_name: str, route_label: str, db: Session
) -> BookWindowOut:
    """Compute Book Window recommendation for a signal.

    Returns BookWindowOut with result=None if not enough data.
    """
    snapshots = _get_price_snapshots(db, signal_id)

    if len(snapshots) < MIN_DATA_POINTS:
        return BookWindowOut(
            signal_id=str(signal_id),
            signal_name=signal_name,
            route_label=route_label,
            result=None,
        )

    # Compute heuristics
    trend = _compute_trend_direction(snapshots)
    seasonal = _compute_seasonal_pattern(db, signal_id, snapshots)
    inventory = _compute_inventory_pressure(snapshots)

    factors = [f for f in [trend, seasonal, inventory] if f is not None]
    data_points = len(snapshots)
    percentile = _compute_current_percentile(snapshots)

    # Extract signals for decision matrix
    trend_sig = trend.signal if trend else "unknown"
    seasonal_sig = seasonal.signal if seasonal else "unknown"
    inventory_sig = inventory.signal if inventory else "unknown"

    near_low = percentile < 0.25
    at_high = percentile > 0.75

    # Decision matrix
    if trend_sig == "declining" and not near_low:
        recommendation = "wait"
        reasoning = "Prices are trending down. We'll alert you when we see a change."
    elif trend_sig == "rising" and near_low:
        recommendation = "book_now"
        reasoning = "This is near the lowest we've seen and prices are climbing."
    elif inventory_sig == "tightening" and not at_high:
        recommendation = "book_now"
        reasoning = "Good price and availability is dropping. Don't wait too long."
    elif inventory_sig == "tightening" and at_high:
        recommendation = "watch"
        reasoning = "Fewer options available but prices are high. We're monitoring closely."
    elif trend_sig == "declining" and near_low:
        recommendation = "book_now"
        reasoning = "Prices are near the low end and still falling. A great time to lock in."
    elif seasonal_sig == "favorable" and trend_sig != "rising":
        recommendation = "book_now"
        reasoning = "Current prices are below average for this route. Good time to book."
    elif seasonal_sig == "unfavorable":
        recommendation = "wait"
        reasoning = "Prices are above average for this route. Worth waiting for a better deal."
    elif trend_sig == "rising":
        recommendation = "watch"
        reasoning = "Prices are climbing. Watch for a dip before committing."
    else:
        recommendation = "watch"
        reasoning = "Prices are holding steady. No urgency yet."

    # Confidence calculation
    active_heuristics = sum(
        1 for f in [trend, seasonal, inventory]
        if f is not None and f.signal not in ("stable", "neutral", "unknown")
    )
    agreeing = _count_agreeing_signals(recommendation, trend_sig, seasonal_sig, inventory_sig)

    if agreeing >= 2 and data_points >= HIGH_CONFIDENCE_THRESHOLD:
        confidence = "high"
    elif agreeing >= 2 or (active_heuristics >= 1 and data_points >= HIGH_CONFIDENCE_THRESHOLD):
        confidence = "medium"
    else:
        confidence = "low"

    return BookWindowOut(
        signal_id=str(signal_id),
        signal_name=signal_name,
        route_label=route_label,
        result=BookWindowResult(
            signal_id=str(signal_id),
            recommendation=recommendation,
            confidence=confidence,
            reasoning=reasoning,
            factors=factors,
            data_points=data_points,
        ),
    )


def _count_agreeing_signals(
    recommendation: str, trend: str, seasonal: str, inventory: str
) -> int:
    """Count how many heuristics agree with the recommendation."""
    count = 0
    if recommendation == "book_now":
        if trend in ("rising", "declining") and trend != "rising":
            count += 1
        if trend == "rising":
            # Rising + near low = book_now, that's agreement
            count += 1
        if seasonal == "favorable":
            count += 1
        if inventory == "tightening":
            count += 1
    elif recommendation == "wait":
        if trend == "declining":
            count += 1
        if seasonal == "unfavorable":
            count += 1
    elif recommendation == "watch":
        if trend in ("stable", "rising"):
            count += 1
        if seasonal == "neutral":
            count += 1
        if inventory == "stable":
            count += 1
    return count
