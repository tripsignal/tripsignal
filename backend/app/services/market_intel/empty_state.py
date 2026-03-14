"""Empty-state intelligence and trigger likelihood for signals."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal_match import DealMatch
from app.services.market_intel.types import (
    EmptyStateInsights,
    MarketBucket,
    MarketStats,
    TriggerLikelihood,
)
from app.services.market_intel.core import deals_in_bucket
from app.services.market_intel.coverage import compute_market_activity


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
    broad_deals = deals_in_bucket(db, bucket, ignore_star=True)
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
    _compute_adjustment_recommendation(db, signal, bucket, budget_cents, tw, result, broad_deals)

    return result


def _compute_adjustment_recommendation(
    db: Session, signal, bucket: MarketBucket,
    budget_cents: Optional[int], tw: dict,
    result: EmptyStateInsights,
    broad_deals: list,
):
    """Find the smallest meaningful adjustment to improve match coverage."""
    # Count current matches
    current_match_count = db.execute(
        select(func.count(DealMatch.id))
        .where(DealMatch.signal_id == signal.id)
    ).scalar() or 0

    if not broad_deals:
        return

    # Test budget adjustments
    if budget_cents:
        for bump in [10000, 20000, 30000]:  # $100, $200, $300
            test_budget = budget_cents + bump
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
    budget_proximity_score = 0
    gap = stats.min_price - budget_cents
    if gap <= 0:
        budget_proximity_score = 100
    elif stats.median_price:
        range_size = stats.median_price - budget_cents
        if range_size > 0:
            budget_proximity_score = max(0, 100 - (gap / range_size * 100))

    # 2. Near-match count (25%)
    near_threshold = int(budget_cents * 1.10)
    near_count = sum(1 for p in stats.prices if budget_cents < p <= near_threshold)
    near_match_score = min(100, near_count * 20)

    # 3. Market activity (20%)
    activity = compute_market_activity(db)
    drops = activity.get("price_drops_today", 0)
    activity_score = min(100, drops * 2)

    # 4. Inventory depth (15%)
    inventory_score = min(100, stats.sample_size * 5)

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
