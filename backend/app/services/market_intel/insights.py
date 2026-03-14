"""Draft signal insights, price spectrum, and top destinations."""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.services.market_intel.types import (
    MarketStats,
    build_market_bucket_from_draft,
    freshness_cutoff,
)
from app.services.market_intel.core import compute_market_stats, deals_in_bucket


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


def compute_top_destinations(db: Session, origin: str, limit: int = 3) -> list[dict]:
    """Return the top destinations by active deal count for a given origin airport."""
    cutoff = freshness_cutoff()
    today = date.today()

    rows = db.execute(
        select(Deal.destination, func.count(Deal.id).label("cnt"))
        .where(Deal.is_active == True)  # noqa: E712
        .where(Deal.last_seen_at >= cutoff)
        .where(Deal.depart_date >= today)
        .where(Deal.origin == origin)
        .group_by(Deal.destination)
        .order_by(func.count(Deal.id).desc())
        .limit(limit)
    ).all()

    return [{"destination": row[0], "deal_count": row[1]} for row in rows]


def compute_date_flexibility_gain(db: Session, draft: dict, flex_days: int = 3) -> Optional[int]:
    """Estimate how many additional packages a user would monitor with ±N days flexibility."""
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

    deals = deals_in_bucket(db, bucket, ignore_star=True)
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
    """Compute market intelligence for a draft signal during creation."""
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

    flex_gain = compute_date_flexibility_gain(db, draft)
    if flex_gain:
        result["date_flex_gain"] = flex_gain

    # Budget suggestion: if user's budget is below median, suggest the median
    budget_config = draft.get("budget", {})
    target_pp = budget_config.get("target_pp")
    if target_pp and stats.median_price:
        budget_cents = int(target_pp) * 100
        if budget_cents < stats.median_price:
            deals_at_budget = sum(1 for p in stats.prices if p <= budget_cents)
            deals_at_median = sum(1 for p in stats.prices if p <= stats.median_price)
            if deals_at_median > deals_at_budget:
                result["budget_suggestion"] = {
                    "suggested_budget": stats.median_price,
                    "current_matches": deals_at_budget,
                    "suggested_matches": deals_at_median,
                }

    return result
