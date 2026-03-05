"""Signal CRUD endpoints."""
import copy
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.signals import (
    SignalCreate,
    SignalOut,
    SignalStatus,
    SignalUpdate,
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

    from app.workers.selloff_scraper import deal_matches_signal_region

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

            match = DealMatch(signal_id=signal.id, deal_id=deal.id)
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
    x_user_id: str = Header(None),
) -> SignalOut:
    """Create a new signal and immediately match against existing deals."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user ID")

    user = db.query(User).filter(User.clerk_id == x_user_id).first()
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
    x_user_id: str = Header(None),
) -> List[SignalOut]:
    """List signals for the current user (with match counts)."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user ID")

    user = db.query(User).filter(User.clerk_id == x_user_id).first()
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

    out: List[SignalOut] = []
    for signal, match_count in rows:
        s_out = _signal_to_out(signal)
        out.append(
            s_out.model_copy(update={"match_count": int(match_count)})
        )

    return out


@router.get("/{signal_id}", response_model=SignalOut)
async def get_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    x_user_id: str = Header(None),
) -> SignalOut:
    """Get a signal by ID."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user ID")

    user = db.query(User).filter(User.clerk_id == x_user_id).first()
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
    x_user_id: str = Header(None),
) -> None:
    """Delete a signal."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user ID")

    user = db.query(User).filter(User.clerk_id == x_user_id).first()
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
    x_user_id: str = Header(None),
) -> SignalOut:
    """Update a signal."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user ID")

    user = db.query(User).filter(User.clerk_id == x_user_id).first()
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
