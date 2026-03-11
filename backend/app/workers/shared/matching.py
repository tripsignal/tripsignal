"""Shared deal-to-signal matching logic used by all scrapers."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.signal import Signal
from app.workers.shared.regions import deal_matches_signal_region

logger = logging.getLogger(__name__)


def load_active_signals(db: Session) -> list[Signal]:
    """Load all active signals once. Call at cycle start and pass to match_deal_to_signals."""
    return db.execute(
        select(Signal).where(Signal.status == "active")
    ).scalars().all()


def match_deal_to_signals(
    db: Session,
    deal: Deal,
    deal_meta: dict,
    signals: Optional[list[Signal]] = None,
) -> list[Signal]:
    """Match a single deal against active signals. Returns matched signals.

    If signals is None, queries the DB (backwards-compatible fallback).
    Pass pre-loaded signals from load_active_signals() to avoid repeated queries.
    """
    if signals is None:
        signals = load_active_signals(db)

    matches = []
    for signal in signals:
        try:
            config = signal.config
            budget = config.get("budget", {})
            travel_window = config.get("travel_window", {})

            if deal_meta["gateway"] not in signal.departure_airports:
                continue
            if not deal_matches_signal_region(deal_meta["region"], signal.destination_regions):
                continue

            start_date_str = travel_window.get("start_date")
            end_date_str = travel_window.get("end_date")
            if start_date_str and end_date_str:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                # start_date = earliest departure, end_date = latest return
                if deal.depart_date < start_dt:
                    continue
                deal_return = deal.return_date or (deal.depart_date + timedelta(days=deal_meta.get("duration_days", 7)))
                if deal_return > end_dt:
                    continue
            else:
                start_month_str = travel_window.get("start_month")
                end_month_str = travel_window.get("end_month")
                if start_month_str and end_month_str:
                    start_month = datetime.strptime(start_month_str, "%Y-%m").date().replace(day=1)
                    end_month_dt = datetime.strptime(end_month_str, "%Y-%m")
                    if end_month_dt.month == 12:
                        end_month = end_month_dt.replace(day=31).date()
                    else:
                        end_month = (end_month_dt.replace(month=end_month_dt.month + 1, day=1) - timedelta(days=1)).date()
                    if not (start_month <= deal.depart_date <= end_month):
                        continue

            min_nights = travel_window.get("min_nights")
            max_nights = travel_window.get("max_nights")
            if min_nights and deal_meta["duration_days"] < min_nights:
                continue
            if max_nights and deal_meta["duration_days"] > max_nights:
                continue

            preferences = config.get("preferences", {})
            min_star_rating = preferences.get("min_star_rating")
            if min_star_rating and deal.star_rating is not None:
                if deal.star_rating < float(min_star_rating):
                    continue

            # Budget check (deal prices are per-person, target_pp is per-person)
            target_pp = budget.get("target_pp")
            if target_pp:
                budget_cents = int(target_pp) * 100
                if deal.price_cents > budget_cents:
                    continue

            matches.append(signal)
        except Exception as e:
            logger.warning("Error matching signal %s: %s", signal.id, e)
            continue

    return matches
