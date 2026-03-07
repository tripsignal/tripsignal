"""Signal CRUD endpoints."""
import copy
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.signals import (
    SignalCreate,
    SignalIntel,
    SignalOut,
    SignalStatus,
    SignalUpdate,
)
from app.services.market_intel import score_deal_for_match
from app.services.market_intel import (
    MarketStats,
    build_market_bucket_from_signal,
    compute_empty_state_insights,
    compute_market_stats,
    score_deal,
)

logger = logging.getLogger("signals")


router = APIRouter(prefix="/api/signals", tags=["signals"])


def _deep_merge_dict(base: dict, update: dict) -> dict:
    """Deep merge update dict into base dict."""
    result = copy.deepcopy(base)
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _signal_to_out(signal: Signal) -> SignalOut:
    """Convert Signal model to SignalOut schema."""
    config = signal.config
    return SignalOut(
        id=signal.id,
        name=signal.name,
        status=SignalStatus(signal.status),
        departure=config["departure"],
        destination=config["destination"],
        travel_window=config["travel_window"],
        travellers=config["travellers"],
        budget=config["budget"],
        notifications=config["notifications"],
        preferences=config["preferences"],
        created_at=signal.created_at,
        updated_at=signal.updated_at,
    )


def _match_signal_against_deals(db: Session, signal: Signal) -> int:
    """Match a newly created signal against all active deals (synchronous).

    Returns the number of new matches created.
    No email is sent — deals just appear on the dashboard immediately.
    """
    from datetime import datetime, timedelta

    from app.workers.shared.regions import deal_matches_signal_region

    config = signal.config or {}
    budget = config.get("budget", {})
    travel_window = config.get("travel_window", {})

    deals = db.execute(select(Deal).where(Deal.is_active)).scalars().all()
    new_matches = 0
    seen_deal_ids: set = set()

    for deal in deals:
        try:
            # Gateway check
            if deal.origin not in signal.departure_airports:
                continue
            # Region check
            dest = deal.destination or ""
            if not deal_matches_signal_region(dest, signal.destination_regions):
                continue

            # Duration check
            duration_days = (deal.return_date - deal.depart_date).days if deal.return_date else 7

            # Travel window — exact dates
            start_date_str = travel_window.get("start_date")
            end_date_str = travel_window.get("end_date")
            if start_date_str and end_date_str:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if deal.depart_date < start_dt:
                    continue
                deal_return = deal.return_date or (deal.depart_date + timedelta(days=duration_days))
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
            if min_nights and duration_days < min_nights:
                continue
            if max_nights and duration_days > max_nights:
                continue

            # Star rating check
            preferences = config.get("preferences", {})
            min_star_rating = preferences.get("min_star_rating")
            if min_star_rating and deal.star_rating is not None:
                if deal.star_rating < float(min_star_rating):
                    continue

            # Budget check (per-person)
            target_pp = budget.get("target_pp")
            if target_pp:
                budget_cents = int(target_pp) * 100
                if deal.price_cents > budget_cents:
                    continue

            # Dedup within this batch
            if deal.id in seen_deal_ids:
                continue
            seen_deal_ids.add(deal.id)

            try:
                vlabel = score_deal_for_match(db, deal)
            except Exception:
                vlabel = None
            match = DealMatch(signal_id=signal.id, deal_id=deal.id, value_label=vlabel)
            db.add(match)
            new_matches += 1
        except Exception:
            continue

    if new_matches:
        db.flush()

    logger.info(
        "Signal %s initial matching complete: %d matches from %d active deals",
        signal.id, new_matches, len(deals),
    )
    return new_matches


@router.post("", response_model=SignalOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_signal(
    request: Request,
    signal_data: SignalCreate,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
) -> SignalOut:
    """Create a new signal and immediately match against existing deals."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Enforce per-user signal limits (Free: 1, Pro: 10)
    SIGNAL_CAPS = {"free": 1, "pro": 10}
    cap = SIGNAL_CAPS.get(user.plan_type, 1)
    active_count = db.query(func.count(Signal.id)).filter(
        Signal.user_id == user.id, Signal.status != "deleted"
    ).scalar() or 0
    if active_count >= cap:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "SIGNAL_LIMIT_REACHED",
                "message": f"You've reached your limit of {cap} signal{'s' if cap > 1 else ''}.",
                "cap": cap,
            },
        )

    # Convert Pydantic models to dict for JSONB storage
    config_dict = signal_data.model_dump()

    # Extract and store mirrored columns
    departure_airports = [code.upper() for code in signal_data.departure.airports]
    destination_regions = [region.value for region in signal_data.destination.regions]

    # Create database record
    signal = Signal(
        name=signal_data.name,
        status="active",
        departure_airports=departure_airports,
        destination_regions=destination_regions,
        config=config_dict,
        user_id=user.id,
    )

    db.add(signal)
    db.flush()          # flush so signal has an ID for matching
    db.refresh(signal)

    # Match against existing deals synchronously (fast — just filtering ~2k deals)
    match_count = _match_signal_against_deals(db, signal)

    db.commit()

    # Trigger first-signal email if this is the user's first signal (idempotent)
    try:
        signal_count = db.execute(
            select(func.count(Signal.id)).where(Signal.user_id == user.id)
        ).scalar()
        if signal_count == 1:
            from app.services.email_orchestrator import EmailType
            from app.services.email_orchestrator import trigger as email_trigger
            email_trigger(
                db=db,
                email_type=EmailType.FIRST_SIGNAL,
                user_id=str(user.id),
                context={"signal_name": signal.name, "signal_id": str(signal.id)},
            )
    except Exception:
        logger.exception("Failed to trigger first-signal email for user %s", user.id)

    out = _signal_to_out(signal)
    return out.model_copy(update={"match_count": match_count})


@router.get("", response_model=List[SignalOut])
async def list_signals(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
) -> List[SignalOut]:
    """List signals for the current user (with match counts)."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stmt = (
        select(
            Signal,
            func.count(DealMatch.id).label("match_count"),
        )
        .outerjoin(DealMatch, DealMatch.signal_id == Signal.id)
        .where(Signal.user_id == user.id)
        .group_by(Signal.id)
        .order_by(Signal.created_at.desc())
    )

    rows = db.execute(stmt).all()

    # Batch-fetch intel cache for all user signals
    signal_ids = [row[0].id for row in rows]
    intel_map: dict = {}
    if signal_ids:
        intel_rows = db.execute(
            select(SignalIntelCache).where(SignalIntelCache.signal_id.in_(signal_ids))
        ).scalars().all()
        intel_map = {ic.signal_id: ic for ic in intel_rows}

    # Batch-fetch active deal prices per signal for live market intelligence
    price_map: dict[UUID, list[int]] = {}
    if signal_ids:
        price_rows = db.execute(
            select(DealMatch.signal_id, Deal.price_cents)
            .join(Deal, Deal.id == DealMatch.deal_id)
            .where(
                DealMatch.signal_id.in_(signal_ids),
                Deal.is_active == True,  # noqa: E712
                Deal.price_cents.isnot(None),
                Deal.price_cents > 0,
            )
        ).all()
        for sid, price in price_rows:
            price_map.setdefault(sid, []).append(price)

    out: List[SignalOut] = []
    for signal, match_count in rows:
        s_out = _signal_to_out(signal)

        # Build intel from cache
        ic = intel_map.get(signal.id)
        intel_kwargs: dict = {}
        if ic:
            intel_kwargs.update(
                value_score=ic.value_score,
                trend_direction=ic.trend_direction,
                trend_consecutive_weeks=ic.trend_consecutive_weeks,
                min_price_ever_cents=ic.min_price_ever_cents,
                current_deal_percentile=ic.current_deal_percentile,
                floor_proximity_pct=ic.floor_proximity_pct,
                best_value_nights=ic.best_value_nights,
                total_matches=ic.total_matches,
                cache_refreshed_at=ic.cache_refreshed_at,
            )

        # Compute market-bucket stats (used for scoring and spectrum)
        bucket = build_market_bucket_from_signal(signal)
        stats = compute_market_stats(db, bucket) if bucket else MarketStats()

        # Populate spectrum data when sample is trustworthy
        if stats.is_scorable() and stats.min_price and stats.max_price and stats.max_price > stats.min_price:
            intel_kwargs["spectrum_min"] = stats.min_price
            intel_kwargs["spectrum_p25"] = stats.p25_price
            intel_kwargs["spectrum_median"] = stats.median_price
            intel_kwargs["spectrum_p75"] = stats.p75_price
            intel_kwargs["spectrum_max"] = stats.max_price
            intel_kwargs["spectrum_sample_size"] = stats.sample_size

        # Compute live market intelligence from active deal prices
        prices = price_map.get(signal.id, [])
        if prices:
            sorted_prices = sorted(prices)
            best = sorted_prices[0]
            intel_kwargs["best_price_cents"] = best

            if stats.median_price is not None:
                intel_kwargs["median_price_cents"] = stats.median_price

            if stats.is_scorable():
                value_score = score_deal(best, stats)
                if value_score.label:
                    intel_kwargs["value_label"] = value_score.label
                if value_score.price_delta_amount and value_score.price_delta_direction == "below":
                    intel_kwargs["price_delta_amount"] = value_score.price_delta_amount
        elif bucket and signal.status == "active":
            # Empty-state diagnostics for signals with no active matched deals
            try:
                esi = compute_empty_state_insights(db, signal, bucket)
                intel_kwargs["empty_market_packages"] = stats.sample_size or 0

                if esi.closest_match_reason == "no_inventory":
                    intel_kwargs["empty_reason"] = "no_inventory"
                elif esi.closest_match_reason == "above_budget" and esi.closest_match_delta_cents:
                    intel_kwargs["empty_reason"] = "above_budget"
                    intel_kwargs["empty_budget_gap_cents"] = esi.closest_match_delta_cents
                elif esi.closest_match_reason == "outside_date_window" and esi.closest_match_date_delta_days:
                    intel_kwargs["empty_reason"] = "outside_date_window"
                    intel_kwargs["empty_date_gap_days"] = esi.closest_match_date_delta_days
                else:
                    intel_kwargs["empty_reason"] = "healthy"

                if esi.recommended_adjustment and esi.additional_matches_estimate:
                    intel_kwargs["empty_adjustment_type"] = esi.recommended_adjustment
                    intel_kwargs["empty_adjustment_value"] = esi.recommended_adjustment_value
                    intel_kwargs["empty_adjustment_matches"] = esi.additional_matches_estimate
            except Exception:
                logger.exception("Failed to compute empty-state insights for signal %s", signal.id)

        intel_data = SignalIntel(**intel_kwargs) if intel_kwargs else None
        out.append(
            s_out.model_copy(update={"match_count": int(match_count), "intel": intel_data})
        )

    return out


@router.get("/{signal_id}", response_model=SignalOut)
async def get_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
) -> SignalOut:
    """Get a signal by ID."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    signal = db.query(Signal).filter(Signal.id == signal_id, Signal.user_id == user.id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return _signal_to_out(signal)


@router.delete("/{signal_id}", status_code=204)
async def delete_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
) -> None:
    """Delete a signal."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    signal = db.query(Signal).filter(Signal.id == signal_id, Signal.user_id == user.id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    db.delete(signal)
    db.commit()


@router.patch("/{signal_id}", response_model=SignalOut)
async def update_signal(
    signal_id: UUID,
    signal_update: SignalUpdate,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
) -> SignalOut:
    """Update a signal."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    signal = db.query(Signal).filter(Signal.id == signal_id, Signal.user_id == user.id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    # Update direct fields
    if signal_update.name is not None:
        signal.name = signal_update.name
    if signal_update.status is not None:
        signal.status = signal_update.status.value

    # Deep merge config
    update_dict = signal_update.model_dump(exclude_unset=True, exclude={"name", "status"})
    if update_dict:
        signal.config = _deep_merge_dict(signal.config, update_dict)

        # Update mirrored columns if departure/destination changed
        # Use merged config to get final values
        if "departure" in update_dict:
            merged_departure = signal.config["departure"]
            departure_airports = [code.upper() for code in merged_departure.get("airports", [])]
            signal.departure_airports = departure_airports

        if "destination" in update_dict:
            merged_destination = signal.config["destination"]
            # Extract region values from the merged config
            destination_regions = []
            for region in merged_destination.get("regions", []):
                if isinstance(region, str):
                    destination_regions.append(region)
                elif hasattr(region, "value"):
                    destination_regions.append(region.value)
                else:
                    destination_regions.append(str(region))
            signal.destination_regions = destination_regions

    # Update updated_at timestamp
    from datetime import datetime, timezone

    signal.updated_at = datetime.now(timezone.utc)

    # Re-match deals if search criteria changed
    if update_dict and ("departure" in update_dict or "destination" in update_dict
                        or "travel_window" in update_dict or "budget" in update_dict):
        db.query(DealMatch).filter(DealMatch.signal_id == signal.id).delete()
        db.flush()
        _match_signal_against_deals(db, signal)

    db.commit()
    db.refresh(signal)

    return _signal_to_out(signal)
