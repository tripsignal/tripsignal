"""Signal CRUD endpoints."""
import copy
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models.signal import Signal
from app.db.session import get_db
from app.schemas.signals import (
    SignalCreate,
    SignalOut,
    SignalStatus,
    SignalUpdate,
)

router = APIRouter(prefix="/signals", tags=["signals"])


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


@router.post("", response_model=SignalOut, status_code=status.HTTP_201_CREATED)
async def create_signal(
    signal_data: SignalCreate,
    db: Session = Depends(get_db),
) -> SignalOut:
    """Create a new signal."""
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
    )

    db.add(signal)
    db.commit()
    db.refresh(signal)

    return _signal_to_out(signal)


@router.get("", response_model=List[SignalOut])
async def list_signals(
    db: Session = Depends(get_db),
) -> List[SignalOut]:
    """List all signals."""
    signals = db.query(Signal).all()
    return [_signal_to_out(signal) for signal in signals]


@router.get("/{signal_id}", response_model=SignalOut)
async def get_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
) -> SignalOut:
    """Get a signal by ID."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
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
) -> SignalOut:
    """Update a signal."""
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
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
        signal.config = _deep_merge_dict(signal.config, update_dict)

        # Update mirrored columns if departure/destination changed
        if "departure" in update_dict:
            merged_departure = signal.config["departure"]
            departure_airports = [code.upper() for code in merged_departure.get("airports", [])]
            signal.departure_airports = departure_airports

        if "destination" in update_dict:
            merged_destination = signal.config["destination"]
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
