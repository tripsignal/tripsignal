"""Market intelligence service — computes market stats, deal scoring, and coverage metrics.

This module provides the core intelligence layer for TripSignal's market analysis.
All computations are based on live scrape data (active deals seen within the freshness window).

Architecture notes:
- Package key: cross-provider dedup via (hotel_id, origin, depart_date, duration_nights)
- Market bucket: comparable grouping via (origin, destination, duration_bucket, star_bucket)
- All stats use median (not mean) for typical price
- Scoring uses z-score with gap validation to prevent false positives
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, case, func, select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.market_snapshot import MarketSnapshot

logger = logging.getLogger("market_intel")

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

FRESHNESS_DAYS = 7  # Only consider deals seen within this window

DURATION_BUCKETS = {
    "short_stay": (3, 5),
    "one_week": (6, 8),
    "extended": (9, 12),
    "long_stay": (13, 16),
}

# Legacy trip-length mapping for backward compatibility
LEGACY_DURATION_MAP = {
    7: "one_week",
    "7": "one_week",
    "7-10": "one_week",  # maps to closest
    "10-14": "extended",
}

STAR_BUCKETS = {
    "economy": (0, 3.4),
    "standard": (3.5, 3.9),
    "premium": (4.0, 4.4),
    "luxury": (4.5, 5.0),
}

# Scoring thresholds
MIN_SAMPLE_SIZE = 6
STRONG_SAMPLE_SIZE = 8

# Value label z-score thresholds
ZSCORE_RARE = 2.0
ZSCORE_GREAT = 1.0
ZSCORE_GOOD = 0.5

# Gap validation rules (price gap between best and second-best)
GAP_RARE_ABS = 15000  # $150 in cents
GAP_RARE_PCT = 0.08
GAP_GREAT_ABS = 7500  # $75 in cents
GAP_GREAT_PCT = 0.04


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketBucket:
    """Defines a comparable market segment for scoring."""
    origin: str  # IATA code
    destination: str  # region code
    duration_bucket: str  # key from DURATION_BUCKETS
    star_bucket: Optional[str] = None  # key from STAR_BUCKETS, None = any


@dataclass
class MarketStats:
    """Distribution statistics for a market bucket."""
    sample_size: int = 0
    unique_package_count: int = 0
    unique_resort_count: int = 0
    min_price: Optional[int] = None
    p25_price: Optional[int] = None
    median_price: Optional[int] = None
    p75_price: Optional[int] = None
    max_price: Optional[int] = None
    price_stddev: Optional[float] = None
    prices: list[int] = field(default_factory=list)  # raw sorted prices for scoring

    def is_scorable(self) -> bool:
        return self.sample_size >= MIN_SAMPLE_SIZE and self.price_stddev is not None

    def is_strong(self) -> bool:
        return self.sample_size >= STRONG_SAMPLE_SIZE

    def to_dict(self) -> dict:
        return {
            "sample_size": self.sample_size,
            "unique_package_count": self.unique_package_count,
            "unique_resort_count": self.unique_resort_count,
            "min_price": self.min_price,
            "p25_price": self.p25_price,
            "median_price": self.median_price,
            "p75_price": self.p75_price,
            "max_price": self.max_price,
            "price_stddev": round(self.price_stddev) if self.price_stddev else None,
        }


@dataclass
class DealValueScore:
    """Scoring result for a single deal within its market."""
    label: Optional[str] = None  # 'Rare value', 'Great value', etc.
    z_score: Optional[float] = None
    price_delta_amount: Optional[int] = None  # median - deal_price (positive = below typical)
    price_delta_direction: Optional[str] = None  # 'below' or 'above'
    comparable_sample_size: int = 0
    resort_anomaly: bool = False
    resort_discount_pct: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.label:
            d["label"] = self.label
        if self.z_score is not None:
            d["z_score"] = round(self.z_score, 2)
        if self.price_delta_amount is not None:
            d["price_delta_amount"] = self.price_delta_amount
            d["price_delta_direction"] = self.price_delta_direction
        if self.comparable_sample_size >= MIN_SAMPLE_SIZE:
            d["comparable_sample_size"] = self.comparable_sample_size
        if self.resort_anomaly:
            d["resort_anomaly"] = True
            d["resort_discount_pct"] = round(self.resort_discount_pct, 1) if self.resort_discount_pct else None
        return d


@dataclass
class EmptyStateInsights:
    """Intelligence for signals with no current matches."""
    market_floor_price: Optional[int] = None
    closest_match_reason: Optional[str] = None  # 'above_budget', 'outside_date_window', 'both', 'no_inventory'
    closest_match_delta_cents: Optional[int] = None
    closest_match_date_delta_days: Optional[int] = None
    recommended_adjustment: Optional[str] = None  # 'budget_flex', 'date_flex', None
    recommended_adjustment_value: Optional[str] = None  # e.g. '+$200' or '±7 days'
    additional_matches_estimate: Optional[int] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.market_floor_price is not None:
            d["market_floor_price"] = self.market_floor_price
        if self.closest_match_reason:
            d["closest_match_reason"] = self.closest_match_reason
        if self.closest_match_delta_cents is not None:
            d["closest_match_delta_cents"] = self.closest_match_delta_cents
        if self.closest_match_date_delta_days is not None:
            d["closest_match_date_delta_days"] = self.closest_match_date_delta_days
        if self.recommended_adjustment:
            d["recommended_adjustment"] = self.recommended_adjustment
            d["recommended_adjustment_value"] = self.recommended_adjustment_value
            d["additional_matches_estimate"] = self.additional_matches_estimate
        return d


@dataclass
class TriggerLikelihood:
    """Heuristic estimate of how close a signal is to triggering."""
    label: Optional[str] = None  # 'Likely soon', 'Possible', 'Unlikely right now'
    reason: Optional[str] = None
    score: Optional[float] = None  # 0-100 internal score

    def to_dict(self) -> dict:
        d: dict = {}
        if self.label:
            d["label"] = self.label
            d["reason"] = self.reason
        return d


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def duration_to_bucket(nights: int) -> Optional[str]:
    """Map a night count to its duration bucket key."""
    for key, (lo, hi) in DURATION_BUCKETS.items():
        if lo <= nights <= hi:
            return key
    return None


def star_to_bucket(star_rating: Optional[float]) -> Optional[str]:
    """Map a star rating to its bucket key."""
    if star_rating is None:
        return None
    for key, (lo, hi) in STAR_BUCKETS.items():
        if lo <= star_rating <= hi:
            return key
    return None


def _compute_percentile(sorted_prices: list[int], pct: float) -> int:
    """Compute percentile from sorted price list."""
    n = len(sorted_prices)
    if n == 0:
        return 0
    idx = (n - 1) * pct
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_prices[lo]
    frac = idx - lo
    return round(sorted_prices[lo] * (1 - frac) + sorted_prices[hi] * frac)


def _freshness_cutoff() -> datetime:
    """Return the timestamp for the freshness window."""
    return datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)


def build_market_bucket_from_signal(signal) -> Optional[MarketBucket]:
    """Build a market bucket from a Signal model instance."""
    config = signal.config or {}
    tw = config.get("travel_window", {})
    prefs = config.get("preferences", {})

    origin = (signal.departure_airports or [None])[0]
    destination = (signal.destination_regions or [None])[0]
    if not origin or not destination:
        return None

    min_nights = tw.get("min_nights", 7)
    max_nights = tw.get("max_nights", 7)
    mid_nights = (min_nights + max_nights) // 2
    dur_bucket = duration_to_bucket(mid_nights) or "one_week"

    star_rating = prefs.get("min_star_rating")
    star_bkt = star_to_bucket(float(star_rating)) if star_rating else None

    return MarketBucket(
        origin=origin,
        destination=destination,
        duration_bucket=dur_bucket,
        star_bucket=star_bkt,
    )


def build_market_bucket_from_draft(draft: dict) -> Optional[MarketBucket]:
    """Build a market bucket from a draft signal dict (Create Signal flow)."""
    departure = draft.get("departure", {})
    destination = draft.get("destination", {})
    tw = draft.get("travel_window", {})
    prefs = draft.get("preferences", {})

    airports = departure.get("airports", [])
    regions = destination.get("regions", [])
    origin = airports[0] if airports else None
    dest = regions[0] if regions else None
    if not origin or not dest:
        return None

    min_nights = tw.get("min_nights", 7)
    max_nights = tw.get("max_nights", 7)
    mid_nights = (min_nights + max_nights) // 2
    dur_bucket = duration_to_bucket(mid_nights) or "one_week"

    star_rating = prefs.get("min_star_rating")
    star_bkt = star_to_bucket(float(star_rating)) if star_rating else None

    return MarketBucket(
        origin=origin,
        destination=dest,
        duration_bucket=dur_bucket,
        star_bucket=star_bkt,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core queries
# ──────────────────────────────────────────────────────────────────────────────

def _base_fresh_deals_query(db: Session):
    """Return a base query for fresh, active deals."""
    cutoff = _freshness_cutoff()
    return (
        select(Deal)
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= date.today())
    )


def _deals_in_bucket(db: Session, bucket: MarketBucket, ignore_star: bool = False) -> list[Deal]:
    """Fetch active, fresh deals matching a market bucket."""
    stmt = _base_fresh_deals_query(db)
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
    if bucket.star_bucket and not ignore_star:
        star_range = STAR_BUCKETS.get(bucket.star_bucket)
        if star_range:
            lo, hi = star_range
            stmt = stmt.where(Deal.star_rating.isnot(None))
            stmt = stmt.where(Deal.star_rating.between(lo, hi))

    return db.execute(stmt).scalars().all()


# ──────────────────────────────────────────────────────────────────────────────
# Market Stats
# ──────────────────────────────────────────────────────────────────────────────

def compute_market_stats(db: Session, bucket: MarketBucket) -> MarketStats:
    """Compute distribution statistics for a market bucket."""
    deals = _deals_in_bucket(db, bucket)

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
        p25_price=_compute_percentile(prices, 0.25),
        median_price=_compute_percentile(prices, 0.50),
        p75_price=_compute_percentile(prices, 0.75),
        max_price=prices[-1],
        price_stddev=stddev,
        prices=prices,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Deal Value Scoring
# ──────────────────────────────────────────────────────────────────────────────

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
    cutoff = _freshness_cutoff()

    # Find all prices for the same resort in the same context
    stmt = (
        select(Deal.price_cents)
        .where(Deal.is_active == True)
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


# ──────────────────────────────────────────────────────────────────────────────
# Market Coverage
# ──────────────────────────────────────────────────────────────────────────────

def compute_market_coverage(db: Session) -> dict:
    """Compute global market coverage metrics (for header)."""
    cutoff = _freshness_cutoff()
    today = date.today()

    # Total active packages and unique resorts
    row = db.execute(
        select(
            func.count(Deal.id),
            func.count(func.distinct(Deal.hotel_name)),
        )
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).one()
    total_packages = row[0] or 0
    total_resorts = row[1] or 0

    # Unique departure airports
    dep_count = db.execute(
        select(func.count(func.distinct(Deal.origin)))
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).scalar() or 0

    # Unique destinations
    dest_count = db.execute(
        select(func.count(func.distinct(Deal.destination)))
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).scalar() or 0

    return {
        "unique_packages_tracked": total_packages,
        "unique_resorts_tracked": total_resorts,
        "departures_count": dep_count,
        "destinations_count": dest_count,
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


# ──────────────────────────────────────────────────────────────────────────────
# Market Events (Today's Signals + Market Movers)
# ──────────────────────────────────────────────────────────────────────────────

# Destination key → display label mapping (server-side)
DESTINATION_LABELS: dict[str, str] = {
    "mexico": "Mexico", "riviera_maya": "Riviera Maya", "cancun": "Cancún",
    "puerto_vallarta": "Puerto Vallarta", "los_cabos": "Los Cabos",
    "mazatlan": "Mazatlán", "huatulco": "Huatulco", "ixtapa": "Ixtapa",
    "dominican_republic": "Dominican Republic", "punta_cana": "Punta Cana",
    "puerto_plata": "Puerto Plata", "la_romana": "La Romana", "samana": "Samaná",
    "jamaica": "Jamaica", "montego_bay": "Montego Bay", "negril": "Negril",
    "cuba": "Cuba", "varadero": "Varadero", "holguin": "Holguín", "havana": "Havana",
    "cayo_coco": "Cayo Coco", "caribbean": "Caribbean", "aruba": "Aruba",
    "barbados": "Barbados", "curacao": "Curaçao", "saint_lucia": "Saint Lucia",
    "turks_caicos": "Turks & Caicos", "bahamas": "Bahamas", "antigua": "Antigua",
    "costa_rica": "Costa Rica", "panama": "Panama", "belize": "Belize",
    "roatan": "Roatán",
}


def _dest_label(key: str) -> str:
    return DESTINATION_LABELS.get(key, key.replace("_", " ").title())


def compute_market_events(db: Session) -> dict:
    """Compute today's signals and market movers from real scrape data.

    Today's Signals: notable price drops, resort anomalies, inventory shifts.
    Market Movers: strongest destination-level price/inventory changes.

    Returns dict with 'todays_signals' and 'market_movers' lists (max 5 each).
    Empty lists when data is insufficient.
    """
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_48h = datetime.now(timezone.utc) - timedelta(hours=48)
    freshness = _freshness_cutoff()
    today_date = date.today()

    todays_signals: list[dict] = []
    market_movers: list[dict] = []

    # ── 1. Price drops by destination (last 24h) ──
    # Find destinations where deals dropped in price, compute average % drop
    drop_rows = db.execute(text("""
        SELECT
            d.destination,
            COUNT(DISTINCT sub.deal_id) AS drop_count,
            AVG(sub.drop_pct) AS avg_drop_pct
        FROM (
            SELECT
                deal_id,
                price_cents,
                LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                CASE WHEN LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) > 0
                     THEN (LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) - price_cents)::float
                          / LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) * 100
                     ELSE 0 END AS drop_pct,
                ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
            FROM deal_price_history
            WHERE recorded_at >= :cutoff_24h
        ) sub
        JOIN deals d ON d.id = sub.deal_id
        WHERE sub.rn = 1
          AND sub.prev_price IS NOT NULL
          AND sub.price_cents < sub.prev_price
          AND sub.drop_pct >= 2.0
          AND d.is_active = true
          AND d.depart_date >= :today
        GROUP BY d.destination
        HAVING COUNT(DISTINCT sub.deal_id) >= 3
        ORDER BY AVG(sub.drop_pct) DESC
        LIMIT 5
    """), {"cutoff_24h": cutoff_24h, "today": today_date}).all()

    for dest, drop_count, avg_pct in drop_rows:
        pct = round(avg_pct)
        if pct >= 3:
            todays_signals.append({
                "text": f"{_dest_label(dest)} prices dropped {pct}% overnight",
                "type": "price_drop",
                "destination": dest,
                "magnitude": pct,
            })

    # ── 2. Resort anomalies (unusually cheap resorts) ──
    # Find hotels with current price significantly below their own recent median
    anomaly_rows = db.execute(text("""
        WITH hotel_stats AS (
            SELECT
                hotel_name,
                destination,
                MIN(price_cents) AS current_min,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY price_cents) AS median_price
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :freshness
              AND depart_date >= :today
              AND hotel_name IS NOT NULL
            GROUP BY hotel_name, destination
            HAVING COUNT(*) >= 5
        )
        SELECT hotel_name, destination, current_min, median_price,
               ROUND((1.0 - current_min::float / median_price) * 100) AS discount_pct
        FROM hotel_stats
        WHERE median_price > 0
          AND current_min < median_price * 0.85
        ORDER BY discount_pct DESC
        LIMIT 3
    """), {"freshness": freshness, "today": today_date}).all()

    for hotel, dest, _current, _median, discount in anomaly_rows:
        # Truncate long hotel names
        short_name = hotel if len(hotel) <= 30 else hotel[:27] + "..."
        todays_signals.append({
            "text": f"{short_name} unusually cheap this week",
            "type": "resort_anomaly",
            "destination": dest,
            "magnitude": int(discount),
        })

    # ── 3. Inventory growth by destination ──
    # Compare deal counts: last 24h vs previous 24h
    inventory_rows = db.execute(text("""
        WITH recent AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND found_at >= :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
        ),
        previous AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND found_at >= :cutoff_48h
              AND found_at < :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
        )
        SELECT r.destination, r.cnt AS new_count, COALESCE(p.cnt, 0) AS prev_count
        FROM recent r
        LEFT JOIN previous p ON p.destination = r.destination
        WHERE r.cnt >= 5
          AND r.cnt > COALESCE(p.cnt, 0) * 1.3
        ORDER BY r.cnt - COALESCE(p.cnt, 0) DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "cutoff_48h": cutoff_48h, "today": today_date}).all()

    for dest, new_count, prev_count in inventory_rows:
        if prev_count > 0:
            pct_increase = round((new_count - prev_count) / prev_count * 100)
            if pct_increase >= 10:
                todays_signals.append({
                    "text": f"{_dest_label(dest)} deals increasing",
                    "type": "inventory_growth",
                    "destination": dest,
                    "magnitude": pct_increase,
                })

    # Cap today's signals at 5
    todays_signals = todays_signals[:5]

    # ── Market Movers: destination-level strongest shifts ──
    # Price movers: destinations with biggest average price change
    price_mover_rows = db.execute(text("""
        SELECT
            d.destination,
            AVG(sub.change_pct) AS avg_change_pct,
            COUNT(DISTINCT sub.deal_id) AS deal_count,
            CASE WHEN AVG(sub.change_pct) > 0 THEN 'up' ELSE 'down' END AS direction
        FROM (
            SELECT
                deal_id,
                price_cents,
                LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                CASE WHEN LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) > 0
                     THEN (price_cents::float - LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at))
                          / LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) * 100
                     ELSE 0 END AS change_pct,
                ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
            FROM deal_price_history
            WHERE recorded_at >= :cutoff_24h
        ) sub
        JOIN deals d ON d.id = sub.deal_id
        WHERE sub.rn = 1
          AND sub.prev_price IS NOT NULL
          AND ABS(sub.change_pct) >= 2.0
          AND d.is_active = true
          AND d.depart_date >= :today
        GROUP BY d.destination
        HAVING COUNT(DISTINCT sub.deal_id) >= 3
           AND ABS(AVG(sub.change_pct)) >= 3.0
        ORDER BY ABS(AVG(sub.change_pct)) DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "today": today_date}).all()

    for dest, avg_pct, _count, direction in price_mover_rows:
        pct = abs(round(avg_pct))
        arrow = "↓" if direction == "down" else "↑"
        market_movers.append({
            "text": f"{_dest_label(dest)} prices {arrow} {pct}%",
            "type": "price",
            "destination": dest,
            "direction": direction,
            "magnitude": pct,
        })

    # Inventory movers
    inv_mover_rows = db.execute(text("""
        WITH current_inv AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
            HAVING COUNT(*) >= 10
        ),
        prev_inv AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :cutoff_48h
              AND last_seen_at < :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
            HAVING COUNT(*) >= 5
        )
        SELECT c.destination,
               c.cnt AS current_count,
               p.cnt AS prev_count,
               ROUND((c.cnt::float - p.cnt) / p.cnt * 100) AS change_pct
        FROM current_inv c
        JOIN prev_inv p ON p.destination = c.destination
        WHERE p.cnt > 0
          AND ABS(c.cnt::float - p.cnt) / p.cnt * 100 >= 10
        ORDER BY ABS(c.cnt::float - p.cnt) / p.cnt DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "cutoff_48h": cutoff_48h, "today": today_date}).all()

    for dest, current, _prev, change_pct in inv_mover_rows:
        pct = abs(int(change_pct))
        arrow = "↑" if current > _prev else "↓"
        market_movers.append({
            "text": f"{_dest_label(dest)} inventory {arrow} {pct}%",
            "type": "inventory",
            "destination": dest,
            "direction": "up" if current > _prev else "down",
            "magnitude": pct,
        })

    # Sort movers by magnitude descending, cap at 5
    market_movers.sort(key=lambda x: x["magnitude"], reverse=True)
    market_movers = market_movers[:5]

    return {
        "todays_signals": todays_signals,
        "market_movers": market_movers,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Empty-State Intelligence
# ──────────────────────────────────────────────────────────────────────────────

def compute_empty_state_insights(
    db: Session, signal, bucket: MarketBucket
) -> EmptyStateInsights:
    """Compute intelligence for signals with no current matches.

    Finds the market floor, closest non-matching package, and suggests adjustments.
    """
    result = EmptyStateInsights()
    config = signal.config or {}
    budget_config = config.get("budget", {})
    tw = config.get("travel_window", {})

    # 1. Market floor: lowest price in the broader bucket (ignore star/budget filters)
    broad_deals = _deals_in_bucket(db, bucket, ignore_star=True)
    if not broad_deals:
        result.closest_match_reason = "no_inventory"
        return result

    floor_prices = sorted([d.price_cents for d in broad_deals if d.price_cents and d.price_cents > 0])
    if floor_prices:
        result.market_floor_price = floor_prices[0]

    # 2. Closest match analysis
    target_pp = budget_config.get("target_pp")
    budget_cents = int(target_pp) * 100 if target_pp else None

    start_date_str = tw.get("start_date")
    end_date_str = tw.get("end_date")

    above_budget_deals = []
    outside_date_deals = []

    for d in broad_deals:
        is_budget_fail = budget_cents and d.price_cents > budget_cents
        is_date_fail = False
        date_delta = 0

        if start_date_str and end_date_str:
            try:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                deal_return = d.return_date or (d.depart_date + timedelta(days=7))

                if d.depart_date < start_dt:
                    is_date_fail = True
                    date_delta = (start_dt - d.depart_date).days
                elif deal_return > end_dt:
                    is_date_fail = True
                    date_delta = (deal_return - end_dt).days
            except (ValueError, TypeError):
                pass

        if is_budget_fail and not is_date_fail:
            above_budget_deals.append((d, d.price_cents - budget_cents))
        elif is_date_fail and not is_budget_fail:
            outside_date_deals.append((d, date_delta))
        elif is_budget_fail and is_date_fail:
            pass  # both fail

    if above_budget_deals:
        above_budget_deals.sort(key=lambda x: x[1])
        closest = above_budget_deals[0]
        result.closest_match_reason = "above_budget"
        result.closest_match_delta_cents = closest[1]
    elif outside_date_deals:
        outside_date_deals.sort(key=lambda x: x[1])
        closest = outside_date_deals[0]
        result.closest_match_reason = "outside_date_window"
        result.closest_match_date_delta_days = closest[1]
    elif broad_deals:
        result.closest_match_reason = "both"

    # 3. Adjustment recommendations
    _compute_adjustment_recommendation(db, signal, bucket, budget_cents, tw, result)

    return result


def _compute_adjustment_recommendation(
    db: Session, signal, bucket: MarketBucket,
    budget_cents: Optional[int], tw: dict,
    result: EmptyStateInsights,
):
    """Find the smallest meaningful adjustment to improve match coverage."""
    from app.workers.shared.matching import match_deal_to_signals

    # Count current matches
    current_match_count = db.execute(
        select(func.count(DealMatch.id))
        .where(DealMatch.signal_id == signal.id)
    ).scalar() or 0

    # Test budget adjustments
    if budget_cents:
        for bump in [10000, 20000, 30000]:  # $100, $200, $300
            test_budget = budget_cents + bump
            broad_deals = _deals_in_bucket(db, bucket, ignore_star=True)
            new_matches = sum(
                1 for d in broad_deals
                if d.price_cents and d.price_cents <= test_budget
            )
            improvement = new_matches - current_match_count
            if improvement >= 5 or (current_match_count == 0 and new_matches >= 3):
                result.recommended_adjustment = "budget_flex"
                result.recommended_adjustment_value = f"+${bump // 100}"
                result.additional_matches_estimate = improvement
                return

    # Test date flexibility (only for exact-date signals)
    start_date_str = tw.get("start_date")
    end_date_str = tw.get("end_date")
    if start_date_str and end_date_str:
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()

            for flex_days in [3, 7]:
                new_start = start_dt - timedelta(days=flex_days)
                new_end = end_dt + timedelta(days=flex_days)

                broad_deals = _deals_in_bucket(db, bucket, ignore_star=True)
                new_matches = 0
                for d in broad_deals:
                    if d.depart_date < new_start:
                        continue
                    deal_return = d.return_date or (d.depart_date + timedelta(days=7))
                    if deal_return > new_end:
                        continue
                    if budget_cents and d.price_cents and d.price_cents > budget_cents:
                        continue
                    new_matches += 1

                improvement = new_matches - current_match_count
                if improvement >= 5 or (current_match_count == 0 and new_matches >= 3):
                    result.recommended_adjustment = "date_flex"
                    result.recommended_adjustment_value = f"±{flex_days} days"
                    result.additional_matches_estimate = improvement
                    return
        except (ValueError, TypeError):
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Trigger Likelihood
# ──────────────────────────────────────────────────────────────────────────────

def compute_trigger_likelihood(
    db: Session, signal, bucket: MarketBucket, stats: MarketStats
) -> TriggerLikelihood:
    """Estimate how close a signal is to matching based on market conditions.

    Internal score: 0-100
    - 40% budget proximity
    - 25% near-match count
    - 20% market activity (price drops)
    - 15% inventory depth
    """
    result = TriggerLikelihood()
    config = signal.config or {}
    budget_config = config.get("budget", {})

    if stats.sample_size < 3:
        return result  # Not enough data

    target_pp = budget_config.get("target_pp")
    budget_cents = int(target_pp) * 100 if target_pp else None

    if not budget_cents or not stats.min_price:
        return result

    # 1. Budget proximity (40%)
    # How close is the cheapest deal to the budget?
    budget_proximity_score = 0
    gap = stats.min_price - budget_cents
    if gap <= 0:
        budget_proximity_score = 100  # Already within budget
    elif stats.median_price:
        # Normalize: 0 = way above, 100 = at budget
        range_size = stats.median_price - budget_cents
        if range_size > 0:
            budget_proximity_score = max(0, 100 - (gap / range_size * 100))

    # 2. Near-match count (25%)
    # Count deals within 10% above budget
    near_threshold = int(budget_cents * 1.10)
    near_count = sum(1 for p in stats.prices if budget_cents < p <= near_threshold)
    near_match_score = min(100, near_count * 20)  # 5 near-matches = 100

    # 3. Market activity (20%)
    activity = compute_market_activity(db)
    drops = activity.get("price_drops_today", 0)
    activity_score = min(100, drops * 2)  # 50+ drops = 100

    # 4. Inventory depth (15%)
    inventory_score = min(100, stats.sample_size * 5)  # 20+ packages = 100

    total = (
        budget_proximity_score * 0.40 +
        near_match_score * 0.25 +
        activity_score * 0.20 +
        inventory_score * 0.15
    )

    result.score = total

    if total >= 65:
        result.label = "Likely soon"
        if gap <= 0:
            result.reason = "Deals within your budget are already available in this market."
        elif near_count >= 3:
            result.reason = f"{near_count} packages are within 10% of your budget."
        else:
            result.reason = "Active pricing movement in your market suggests deals may appear soon."
    elif total >= 35:
        result.label = "Possible"
        if near_count >= 1:
            result.reason = f"Some packages are getting close to your budget."
        else:
            result.reason = "There is inventory in your market, but prices haven't reached your target yet."
    else:
        result.label = "Unlikely right now"
        result.reason = "Current market prices are significantly above your target."

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Price Spectrum Data
# ──────────────────────────────────────────────────────────────────────────────

def build_spectrum_data(stats: MarketStats, marker_price: Optional[int] = None) -> Optional[dict]:
    """Build the price spectrum payload for the UI component."""
    if stats.sample_size < 3:
        return None

    return {
        "min_price": stats.min_price,
        "p25_price": stats.p25_price,
        "median_price": stats.median_price,
        "p75_price": stats.p75_price,
        "max_price": stats.max_price,
        "sample_size": stats.sample_size,
        "marker_price": marker_price,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Draft Signal Insights (Create Signal flow)
# ──────────────────────────────────────────────────────────────────────────────

def compute_top_destinations(db: Session, origin: str, limit: int = 3) -> list[dict]:
    """Return the top destinations by active deal count for a given origin airport."""
    cutoff = _freshness_cutoff()
    today = date.today()

    rows = db.execute(
        select(Deal.destination, func.count(Deal.id).label("cnt"))
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
        .where(Deal.origin == origin)
        .group_by(Deal.destination)
        .order_by(func.count(Deal.id).desc())
        .limit(limit)
    ).all()

    return [{"destination": row[0], "deal_count": row[1]} for row in rows]


def compute_date_flexibility_gain(db: Session, draft: dict, flex_days: int = 3) -> Optional[int]:
    """Estimate how many additional packages a user would monitor with ±N days flexibility.

    Only meaningful for specific-dates signals.
    """
    tw = draft.get("travel_window", {})
    start_date_str = tw.get("start_date")
    end_date_str = tw.get("end_date")
    if not start_date_str or not end_date_str:
        return None

    bucket = build_market_bucket_from_draft(draft)
    if not bucket:
        return None

    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    deals = _deals_in_bucket(db, bucket, ignore_star=True)
    if not deals:
        return None

    budget_config = draft.get("budget", {})
    target_pp = budget_config.get("target_pp")
    budget_cents = int(target_pp) * 100 if target_pp else None

    # Count deals matching the exact window
    exact_count = 0
    for d in deals:
        deal_return = d.return_date or (d.depart_date + timedelta(days=7))
        if d.depart_date < start_dt or deal_return > end_dt:
            continue
        if budget_cents and d.price_cents and d.price_cents > budget_cents:
            continue
        exact_count += 1

    # Count deals matching the expanded window
    flex_start = start_dt - timedelta(days=flex_days)
    flex_end = end_dt + timedelta(days=flex_days)
    flex_count = 0
    for d in deals:
        deal_return = d.return_date or (d.depart_date + timedelta(days=7))
        if d.depart_date < flex_start or deal_return > flex_end:
            continue
        if budget_cents and d.price_cents and d.price_cents > budget_cents:
            continue
        flex_count += 1

    gain = flex_count - exact_count
    return gain if gain > 0 else None


def compute_draft_signal_insights(db: Session, draft: dict) -> Optional[dict]:
    """Compute market intelligence for a draft signal during creation.

    Returns insights about what the user can expect if they create this signal.
    """
    bucket = build_market_bucket_from_draft(draft)
    if not bucket:
        return None

    stats = compute_market_stats(db, bucket)

    result: dict = {
        "packages_monitored": stats.unique_package_count,
    }

    if stats.median_price:
        result["typical_price"] = stats.median_price

    if stats.min_price and stats.max_price and stats.sample_size >= 3:
        result["price_range"] = {
            "min": stats.min_price,
            "max": stats.max_price,
        }

    spectrum = build_spectrum_data(stats)
    if spectrum:
        result["spectrum"] = spectrum

    # Date flexibility gain (only for specific-dates signals)
    flex_gain = compute_date_flexibility_gain(db, draft)
    if flex_gain:
        result["date_flex_gain"] = flex_gain

    # Budget suggestion: if user's budget is below median, suggest the median
    budget_config = draft.get("budget", {})
    target_pp = budget_config.get("target_pp")
    if target_pp and stats.median_price:
        budget_cents = int(target_pp) * 100
        if budget_cents < stats.median_price:
            # Count how many more deals the user would get at median
            deals_at_budget = sum(1 for p in stats.prices if p <= budget_cents)
            deals_at_median = sum(1 for p in stats.prices if p <= stats.median_price)
            if deals_at_median > deals_at_budget:
                result["budget_suggestion"] = {
                    "suggested_budget": stats.median_price,
                    "current_matches": deals_at_budget,
                    "suggested_matches": deals_at_median,
                }

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Daily Market Snapshots
# ──────────────────────────────────────────────────────────────────────────────

def generate_daily_snapshots(db: Session) -> int:
    """Generate daily market snapshot rows for all active market buckets.

    Iterates every distinct (origin, destination) pair with active deals,
    computes stats per duration bucket (and optionally star bucket),
    and inserts a snapshot row for each.

    Returns: number of snapshot rows created.
    """
    today = date.today()
    cutoff = _freshness_cutoff()

    # Find all distinct active routes
    routes = db.execute(
        select(
            func.distinct(Deal.origin),
            Deal.destination,
        )
        .where(Deal.is_active == True)
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
    ).all()

    created = 0

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

    try:
        db.commit()
        logger.info("Generated %d daily market snapshots for %s", created, today)
    except Exception:
        logger.exception("Failed to commit daily market snapshots")
        db.rollback()
        created = 0

    return created
