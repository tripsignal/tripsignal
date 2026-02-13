"""Signal CRUD endpoints."""
import copy
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.deal_match import DealMatch
from app.db.models.plan import Plan
from app.db.models.signal import Signal
from app.db.models.subscription import Subscription
from app.db.session import get_db
from app.schemas.signals import (
    SignalCreate,
    SignalOut,
    SignalStatus,
    SignalUpdate,
)

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
    config = signal.config or {}

    # --- Departure (required: mode) ---
    dep = config.get("departure") or {}
    dep.setdefault("mode", "multiple")  # enum: single | multiple | any
    dep.setdefault("airports", [])
    config["departure"] = dep

    # --- Destination (required: mode + regions enum list) ---
    dest = config.get("destination") or {}
    dest.setdefault("mode", "multiple")  # enum: single | multiple | any
    dest.setdefault("regions", ["mexico"])  # enum values: mexico, dominican_republic, cuba, jamaica
    config["destination"] = dest

    # --- Travel window (required: start_month, end_month) ---
    tw = config.get("travel_window") or {}
    tw.setdefault("start_month", "2026-02")  # must be YYYY-MM
    tw.setdefault("end_month", "2026-04")    # must be YYYY-MM
    config["travel_window"] = tw

    # --- Travellers ---
    trav = config.get("travellers") or {}
    trav.setdefault("adults", 2)
    trav.setdefault("children", 0)
    config["travellers"] = trav

    # --- Budget (required: target_pp) ---
    bud = config.get("budget") or {}
    bud.setdefault("target_pp", 2000)
    config["budget"] = bud

    # --- Notifications ---
    notif = config.get("notifications") or {}
    notif.setdefault("channel", "log")
    config["notifications"] = notif

    # --- Preferences ---
    prefs = config.get("preferences") or {}
    config["preferences"] = prefs

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


@router.post("", response_model=SignalOut, status_code=status.HTTP_201_CREATED)
async def create_signal(
    signal_data: SignalCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SignalOut:
    """Create a new signal."""

    # --- Plan enforcement: max signals per plan ---
    sub = db.execute(
        select(Subscription).where(
            Subscription.user_id == current_user.id,
            Subscription.status == "active",
        )
    ).scalar_one_or_none()

    if not sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active subscription. Upgrade required to create signals.",
        )

    plan = db.get(Plan, sub.plan_id)
    max_signals = plan.max_active_signals

    current_count = db.execute(
    select(func.count())
    .select_from(Signal)
    .where(
        Signal.user_id == current_user.id,
        Signal.status == "active",
    )
).scalar_one()


    if current_count >= max_signals:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Plan limit reached: max {max_signals} active signals.",
        )

    # --- End plan enforcement ---

    # Convert Pydantic models to dict for JSONB storage
    config_dict = signal_data.model_dump()

    # Extract and store mirrored columns
    departure_airports = [code.upper() for code in signal_data.departure.airports]
    destination_regions = [region.value for region in signal_data.destination.regions]

    # Create database record
    signal = Signal(
        user_id=current_user.id,
        name=signal_data.name,
        status="active",
        departure_airports=departure_airports,
        destination_regions=destination_regions,
        config=config_dict,
    )

    db.add(signal)
    db.commit()
    db.refresh(signal)

    return _signal_to_out(signal)
@router.get("", response_model=List[SignalOut])
async def list_signals(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> List[SignalOut]:
    """List current user's signals (with match counts)."""
    print(f"[LIST_SIGNALS DEBUG] Current user ID: {current_user.id}")
    print(f"[LIST_SIGNALS DEBUG] Current user clerk_id: {current_user.clerk_id}")
    
    stmt = (
        select(
            Signal,
            func.count(DealMatch.id).label("match_count"),
        )
        .outerjoin(DealMatch, DealMatch.signal_id == Signal.id)
        .where(Signal.user_id == current_user.id)
        .group_by(Signal.id)
        .order_by(Signal.created_at.desc())
    )

    rows = db.execute(stmt).all()

    out: List[SignalOut] = []
    for signal, match_count in rows:
        s_out = _signal_to_out(signal)
        out.append(s_out.model_copy(update={"match_count": int(match_count)}))

    return out

    return out


@router.get("/{signal_id}", response_model=SignalOut)
async def get_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SignalOut:
    """Get a signal by ID (must belong to current user)."""
    signal = (
        db.query(Signal)
        .filter(Signal.id == signal_id, Signal.user_id == current_user.id)
        .first()
    )
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signal with id {signal_id} not found",
        )
    return _signal_to_out(signal)


@router.patch("/{signal_id}", response_model=SignalOut)
async def update_signal(
    signal_id: UUID,
    signal_update: SignalUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SignalOut:
    """Update a signal (must belong to current user)."""
    signal = (
        db.query(Signal)
        .filter(Signal.id == signal_id, Signal.user_id == current_user.id)
        .first()
    )
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signal with id {signal_id} not found",
        )

    # Update direct fields
    if signal_update.name is not None:
        signal.name = signal_update.name
    if signal_update.status is not None:
        signal.status = signal_update.status.value

    # Deep merge config
    update_dict = signal_update.model_dump(exclude_unset=True, exclude={"name", "status"})
    if update_dict:
        signal.config = _deep_merge_dict(signal.config or {}, update_dict)

        # Update mirrored columns if departure/destination changed
        if "departure" in update_dict:
            merged_departure = (signal.config or {}).get("departure") or {}
            departure_airports = [code.upper() for code in merged_departure.get("airports", [])]
            signal.departure_airports = departure_airports

        if "destination" in update_dict:
            merged_destination = (signal.config or {}).get("destination") or {}
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

    db.commit()
    db.refresh(signal)

    return _signal_to_out(signal)
