"""Market intelligence package.

Re-exports all public symbols for backward compatibility.
Existing imports like `from app.services.market_intel import score_deal` continue to work.
"""

# Types, constants, and pure helpers
from app.services.market_intel.types import (  # noqa: F401
    DURATION_BUCKETS,
    FRESHNESS_DAYS,
    GAP_GREAT_ABS,
    GAP_GREAT_PCT,
    GAP_RARE_ABS,
    GAP_RARE_PCT,
    LEGACY_DURATION_MAP,
    MIN_SAMPLE_SIZE,
    STAR_BUCKETS,
    STRONG_SAMPLE_SIZE,
    ZSCORE_GOOD,
    ZSCORE_GREAT,
    ZSCORE_RARE,
    DealValueScore,
    EmptyStateInsights,
    MarketBucket,
    MarketStats,
    TriggerLikelihood,
    build_market_bucket_from_draft,
    build_market_bucket_from_signal,
    compute_percentile,
    duration_to_bucket,
    freshness_cutoff,
    star_to_bucket,
)

# Core queries
from app.services.market_intel.core import (  # noqa: F401
    compute_market_stats,
    deals_in_bucket,
)

# Scoring
from app.services.market_intel.scoring import (  # noqa: F401
    score_deal,
    score_deal_for_match,
    score_deal_resort_anomaly,
)

# Coverage
from app.services.market_intel.coverage import (  # noqa: F401
    compute_market_activity,
    compute_market_coverage,
)

# Events
from app.services.market_intel.events import compute_market_events  # noqa: F401

# Empty state & trigger likelihood
from app.services.market_intel.empty_state import (  # noqa: F401
    compute_empty_state_insights,
    compute_trigger_likelihood,
)

# Insights & spectrum
from app.services.market_intel.insights import (  # noqa: F401
    build_spectrum_data,
    compute_date_flexibility_gain,
    compute_draft_signal_insights,
    compute_top_destinations,
)

# Snapshots
from app.services.market_intel.snapshots import generate_daily_snapshots  # noqa: F401

# Backward compatibility aliases
from app.services.formatting import DESTINATION_LABELS, dest_label  # noqa: F401

_dest_label = dest_label  # Legacy alias
_compute_percentile = compute_percentile  # Legacy alias
_freshness_cutoff = freshness_cutoff  # Legacy alias
_deals_in_bucket = deals_in_bucket  # Legacy alias
