"""
Unit tests for market intelligence scoring (no DB required).

Tests cover:
- Duration bucket mapping
- Star bucket mapping
- Z-score label assignment
- Gap validation downgrades
- Sample-size suppression
- Null-safe / edge-case behavior

Run: cd /opt/tripsignal/backend && python -m pytest tests/test_market_intel_scoring.py -v
"""
from __future__ import annotations

import math

import pytest

from app.services.market_intel import (
    DURATION_BUCKETS,
    GAP_GREAT_ABS,
    GAP_RARE_ABS,
    MIN_SAMPLE_SIZE,
    STAR_BUCKETS,
    STRONG_SAMPLE_SIZE,
    DealValueScore,
    MarketStats,
    duration_to_bucket,
    score_deal,
    star_to_bucket,
)


# ── Duration Bucket Mapping ──────────────────────────────────────────────────


class TestDurationToBucket:
    @pytest.mark.parametrize(
        "nights, expected",
        [
            (3, "short_stay"),
            (4, "short_stay"),
            (5, "short_stay"),
            (6, "one_week"),
            (7, "one_week"),
            (8, "one_week"),
            (9, "extended"),
            (10, "extended"),
            (12, "extended"),
            (13, "long_stay"),
            (16, "long_stay"),
        ],
    )
    def test_valid_mappings(self, nights: int, expected: str):
        assert duration_to_bucket(nights) == expected

    @pytest.mark.parametrize("nights", [0, 1, 2, 17, 30, 100])
    def test_out_of_range_returns_none(self, nights: int):
        assert duration_to_bucket(nights) is None

    def test_all_bucket_boundaries_covered(self):
        """Every night from 3-16 maps to exactly one bucket."""
        for n in range(3, 17):
            result = duration_to_bucket(n)
            assert result is not None, f"Night {n} has no bucket"


# ── Star Bucket Mapping ──────────────────────────────────────────────────────


class TestStarToBucket:
    @pytest.mark.parametrize(
        "rating, expected",
        [
            (0, "economy"),
            (2.0, "economy"),
            (3.4, "economy"),
            (3.5, "standard"),
            (3.9, "standard"),
            (4.0, "premium"),
            (4.4, "premium"),
            (4.5, "luxury"),
            (5.0, "luxury"),
        ],
    )
    def test_valid_mappings(self, rating: float, expected: str):
        assert star_to_bucket(rating) == expected

    def test_none_returns_none(self):
        assert star_to_bucket(None) is None

    def test_negative_returns_none(self):
        # Negative ratings don't match any bucket
        assert star_to_bucket(-1.0) is None

    def test_above_five_returns_none(self):
        assert star_to_bucket(5.1) is None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_stats(
    prices: list[int],
    sample_size: int | None = None,
) -> MarketStats:
    """Build a MarketStats from a price list (sorted internally)."""
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    if n == 0:
        return MarketStats()

    mean = sum(sorted_prices) / n
    variance = sum((p - mean) ** 2 for p in sorted_prices) / n
    stddev = math.sqrt(variance) if variance > 0 else None
    mid = n // 2
    median = sorted_prices[mid] if n % 2 == 1 else (sorted_prices[mid - 1] + sorted_prices[mid]) // 2

    return MarketStats(
        sample_size=sample_size if sample_size is not None else n,
        min_price=sorted_prices[0],
        p25_price=sorted_prices[n // 4] if n >= 4 else sorted_prices[0],
        median_price=median,
        p75_price=sorted_prices[3 * n // 4] if n >= 4 else sorted_prices[-1],
        max_price=sorted_prices[-1],
        price_stddev=stddev,
        prices=sorted_prices,
    )


# ── Z-Score Label Mapping ────────────────────────────────────────────────────


class TestZScoreLabels:
    """Test that z-score thresholds produce correct labels."""

    def _uniform_stats(self, n: int = 20) -> MarketStats:
        """Create stats where median=100000 (~$1000), stddev ~28868 for uniform 50k-150k."""
        prices = [50000 + i * (100000 // (n - 1)) for i in range(n)]
        return _make_stats(prices)

    def test_typical_price_near_median(self):
        stats = self._uniform_stats()
        # Price near the median should be "Typical price"
        result = score_deal(stats.median_price, stats)
        assert result.label == "Typical price"

    def test_good_price(self):
        stats = self._uniform_stats()
        # Price 0.7 stddev below median
        price = int(stats.median_price - 0.7 * stats.price_stddev)
        result = score_deal(price, stats)
        assert result.label == "Good price"

    def test_high_for_market(self):
        stats = self._uniform_stats()
        # Price well above median
        price = int(stats.median_price + stats.price_stddev)
        result = score_deal(price, stats)
        assert result.label == "High for market"

    def test_z_score_is_set(self):
        stats = self._uniform_stats()
        result = score_deal(stats.min_price, stats)
        assert result.z_score is not None
        assert result.z_score > 0  # Below median = positive z


# ── Gap Validation ───────────────────────────────────────────────────────────


class TestGapValidation:
    """Test that gap validation prevents false positives for 'Rare' and 'Great' labels."""

    def test_rare_value_requires_gap(self):
        """A deal with z >= 2.0 but tiny gap should NOT get 'Rare value'."""
        # All prices tightly clustered except one outlier
        # Prices: 99000, 100000, 100000, 100000, 100000, 100000, 100000, 100000
        # The outlier at 99000 has z > 0 but the gap (1000 cents = $10) is tiny
        prices = [99000] + [100000] * 7
        stats = _make_stats(prices)
        # Create a deal price far below — but artificially set it as the min
        # so the gap between 1st and 2nd is small
        result = score_deal(99000, stats)
        # The gap is only 1000 cents ($10), well below GAP_RARE_ABS ($150)
        # So even if z >= 2.0, label should NOT be "Rare value"
        assert result.label != "Rare value" or result.z_score < 2.0

    def test_rare_value_with_sufficient_gap(self):
        """A deal with z >= 2.0 AND large gap gets 'Rare value'."""
        # Create distribution where cheapest is $200+ below next cheapest
        prices = [50000, 75000, 90000, 95000, 100000, 105000, 110000, 120000]
        stats = _make_stats(prices)
        result = score_deal(50000, stats)
        # Gap = 75000 - 50000 = 25000 ($250) > GAP_RARE_ABS ($150)
        assert result.z_score >= 2.0
        assert result.label == "Rare value"

    def test_great_value_with_moderate_gap(self):
        """A deal with z >= 1.0 and moderate gap gets 'Great value'."""
        # Spread prices so the best deal has a reasonable gap
        prices = [70000, 82000, 90000, 95000, 100000, 105000, 110000, 120000]
        stats = _make_stats(prices)
        result = score_deal(70000, stats)
        # Gap = 82000 - 70000 = 12000 ($120) > GAP_GREAT_ABS ($75)
        if result.z_score >= 1.0:
            assert result.label in ("Great value", "Rare value")

    def test_great_downgraded_without_gap(self):
        """z >= 1.0 but tiny gap should downgrade to 'Good price'."""
        # Tight cluster with a slightly cheaper deal
        prices = [94000, 95000, 100000, 100000, 105000, 105000, 110000, 110000]
        stats = _make_stats(prices)
        result = score_deal(94000, stats)
        # Gap = 95000 - 94000 = 1000 ($10), too small for Great
        if result.z_score >= 1.0:
            # Without sufficient gap, it should be "Good price" at most
            assert result.label in ("Good price", "Typical price")


# ── Sample-Size Suppression ─────────────────────────────────────────────────


class TestSampleSizeSuppression:
    def test_below_min_sample_no_label(self):
        """With < MIN_SAMPLE_SIZE prices, no z-score label is assigned."""
        prices = [50000, 80000, 100000]  # Only 3 prices
        stats = _make_stats(prices)
        assert not stats.is_scorable()
        result = score_deal(50000, stats)
        assert result.label is None  # Not enough data

    def test_at_min_sample_labels_work(self):
        """With exactly MIN_SAMPLE_SIZE prices, labels are assigned."""
        prices = [50000, 75000, 90000, 100000, 110000, 120000]
        assert len(prices) == MIN_SAMPLE_SIZE
        stats = _make_stats(prices)
        assert stats.is_scorable()
        result = score_deal(50000, stats)
        assert result.label is not None

    def test_rare_suppressed_below_strong_sample(self):
        """'Rare value' downgrades to 'Great value' when sample < STRONG_SAMPLE_SIZE."""
        # 6 prices (above MIN but below STRONG)
        prices = [30000, 75000, 90000, 100000, 110000, 130000]
        assert len(prices) >= MIN_SAMPLE_SIZE
        assert len(prices) < STRONG_SAMPLE_SIZE
        stats = _make_stats(prices)
        result = score_deal(30000, stats)
        # Even if z >= 2.0 and gap is sufficient, label should cap at "Great value"
        if result.z_score and result.z_score >= 2.0:
            assert result.label == "Great value"

    def test_rare_allowed_at_strong_sample(self):
        """'Rare value' is allowed when sample >= STRONG_SAMPLE_SIZE."""
        prices = [30000, 75000, 90000, 95000, 100000, 105000, 110000, 130000]
        assert len(prices) >= STRONG_SAMPLE_SIZE
        stats = _make_stats(prices)
        result = score_deal(30000, stats)
        # With strong sample, large gap, and high z, should allow "Rare value"
        if result.z_score and result.z_score >= 2.0:
            gap = prices[1] - prices[0]  # 75000 - 30000 = 45000
            assert gap >= GAP_RARE_ABS
            assert result.label == "Rare value"


# ── Null-Safe / Edge Cases ───────────────────────────────────────────────────


class TestNullSafeBehavior:
    def test_empty_stats_returns_no_label(self):
        """Empty MarketStats produces no label or z-score."""
        stats = MarketStats()
        result = score_deal(50000, stats)
        assert result.label is None
        assert result.z_score is None

    def test_zero_stddev_no_crash(self):
        """All identical prices (stddev=0) doesn't crash."""
        prices = [100000] * 8
        stats = _make_stats(prices)
        # stddev is None when all prices are identical
        result = score_deal(100000, stats)
        # Should not crash; label may or may not be set
        assert isinstance(result, DealValueScore)

    def test_single_price_no_crash(self):
        """Single price shouldn't crash."""
        stats = _make_stats([100000])
        result = score_deal(100000, stats)
        assert isinstance(result, DealValueScore)
        assert result.label is None  # Not scorable

    def test_price_delta_direction(self):
        """Price delta direction is correct."""
        prices = [50000, 75000, 90000, 100000, 110000, 120000]
        stats = _make_stats(prices)
        # Below median
        result = score_deal(50000, stats)
        assert result.price_delta_direction == "below"
        assert result.price_delta_amount > 0
        # Above median
        result_high = score_deal(120000, stats)
        assert result_high.price_delta_direction == "above"

    def test_deal_value_score_to_dict(self):
        """DealValueScore.to_dict() produces clean output."""
        result = DealValueScore(label="Great value", z_score=1.5, price_delta_amount=15000,
                                price_delta_direction="below", comparable_sample_size=10)
        d = result.to_dict()
        assert d["label"] == "Great value"
        assert d["z_score"] == 1.5
        assert d["price_delta_amount"] == 15000
        assert d["comparable_sample_size"] == 10

    def test_empty_score_to_dict(self):
        """Empty DealValueScore.to_dict() returns minimal dict."""
        result = DealValueScore()
        d = result.to_dict()
        assert "label" not in d
        assert "z_score" not in d


# ── MarketStats Helper Methods ───────────────────────────────────────────────


class TestMarketStatsHelpers:
    def test_is_scorable_true(self):
        stats = _make_stats([50000, 60000, 70000, 80000, 90000, 100000])
        assert stats.is_scorable()

    def test_is_scorable_false_low_sample(self):
        stats = _make_stats([50000, 60000, 70000])
        assert not stats.is_scorable()

    def test_is_scorable_false_zero_stddev(self):
        stats = _make_stats([100000] * 8)
        # All same price → stddev is None
        assert not stats.is_scorable()

    def test_is_strong_true(self):
        stats = _make_stats(list(range(50000, 130000, 10000)))  # 8 prices
        assert stats.is_strong()

    def test_is_strong_false(self):
        stats = _make_stats([50000, 60000, 70000, 80000, 90000, 100000])  # 6 prices
        assert not stats.is_strong()
