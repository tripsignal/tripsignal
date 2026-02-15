"""Signals API routes."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.signal import Signal
from app.db.models.user import User
from app.api.deps import get_current_user


router = APIRouter()


# --- Schemas ---

class SignalConfig(BaseModel):
    resort_quality: str = "any"
    travel_window: str = "anytime"
    selected_months: List[str] = []
    trip_length: str = "7-10"
    max_budget: Optional[int] = None
    notify_email: str


class SignalCreate(BaseModel):
    departure_airport: str
    destination: str
    resort_quality: str = "any"
    travel_window: str = "anytime"
    selected_months: List[str] = []
    trip_length: str = "7-10"
    max_budget: Optional[int] = None
    notify_email: str


class SignalResponse(BaseModel):
    id: UUID
    name: str
    status: str
    departure_airports: List[str]
    destination_regions: List[str]
    config: dict

    class Config:
        from_attributes = True


class SignalListResponse(BaseModel):
    signals: List[SignalResponse]


# --- Helpers ---

def generate_signal_name(departure: str, destination: str) -> str:
    """Generate a human-readable signal name."""
    dest_label = destination
    if destination == "all":
        dest_label = "Anywhere"
    elif destination.startswith("all-"):
        country = destination.replace("all-", "").replace("-", " ").title()
        dest_label = f"All {country}"
    
    return f"{departure} → {dest_label}"


# --- Routes ---

@router.get("", response_model=SignalListResponse)
def list_signals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all signals for the current user."""
    signals = db.execute(
        select(Signal)
        .where(Signal.user_id == current_user.id)
        .order_by(Signal.created_at.desc())
    ).scalars().all()
    
    return SignalListResponse(signals=signals)


@router.post("", response_model=SignalResponse, status_code=status.HTTP_201_CREATED)
def create_signal(
    payload: SignalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new signal."""
    # Build config object
    config = {
        "resort_quality": payload.resort_quality,
        "travel_window": payload.travel_window,
        "selected_months": payload.selected_months,
        "trip_length": payload.trip_length,
        "max_budget": payload.max_budget,
        "notify_email": payload.notify_email,
    }
    
    # Create signal
    signal = Signal(
        name=generate_signal_name(payload.departure_airport, payload.destination),
        user_id=current_user.id,
        departure_airports=[payload.departure_airport],
        destination_regions=[payload.destination],
        config=config,
        status="active",
    )
    
    db.add(signal)
    db.commit()
    db.refresh(signal)
    
    return signal


@router.get("/{signal_id}", response_model=SignalResponse)
def get_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific signal."""
    signal = db.execute(
        select(Signal)
        .where(Signal.id == signal_id, Signal.user_id == current_user.id)
    ).scalar_one_or_none()
    
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )
    
    return signal


@router.patch("/{signal_id}", response_model=SignalResponse)
def update_signal(
    signal_id: UUID,
    payload: SignalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a signal."""
    signal = db.execute(
        select(Signal)
        .where(Signal.id == signal_id, Signal.user_id == current_user.id)
    ).scalar_one_or_none()
    
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )
    
    # Update fields
    signal.name = generate_signal_name(payload.departure_airport, payload.destination)
    signal.departure_airports = [payload.departure_airport]
    signal.destination_regions = [payload.destination]
    signal.config = {
        "resort_quality": payload.resort_quality,
        "travel_window": payload.travel_window,
        "selected_months": payload.selected_months,
        "trip_length": payload.trip_length,
        "max_budget": payload.max_budget,
        "notify_email": payload.notify_email,
    }
    
    db.commit()
    db.refresh(signal)
    
    return signal


@router.delete("/{signal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_signal(
    signal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a signal."""
    signal = db.execute(
        select(Signal)
        .where(Signal.id == signal_id, Signal.user_id == current_user.id)
    ).scalar_one_or_none()
    
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )
    
    db.delete(signal)
    db.commit()
