"""Market intelligence types, constants, and pure helpers (no DB dependencies)."""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


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
    min_star_rating: Optional[float] = None  # >= filter (used by signals with min_star pref)


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
# Pure helpers (no DB)
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


def compute_percentile(sorted_prices: list[int], pct: float) -> int:
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


def freshness_cutoff() -> datetime:
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
    min_star = float(star_rating) if star_rating else None

    return MarketBucket(
        origin=origin,
        destination=destination,
        duration_bucket=dur_bucket,
        min_star_rating=min_star,
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
    min_star = float(star_rating) if star_rating else None

    return MarketBucket(
        origin=origin,
        destination=dest,
        duration_bucket=dur_bucket,
        min_star_rating=min_star,
    )
