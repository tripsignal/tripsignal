"""Hotel intelligence endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Optional

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.models.hotel_intel import HotelIntel
from app.db.session import get_db

router = APIRouter(prefix="/api/hotels", tags=["hotels"])


class HotelIntelResponse(BaseModel):
    """Response schema for hotel intelligence data."""
    hotel_name: str
    resort_size: Optional[str] = None
    adults_only: Optional[bool] = None
    kids_club: Optional[bool] = None
    teen_club: Optional[bool] = None
    waterpark: Optional[bool] = None
    transfer_time_minutes: Optional[int] = None
    sargassum_risk: Optional[str] = None
    sargassum_notes: Optional[str] = None
    red_flags: Optional[list[str]] = None
    top_complaints: Optional[list[str]] = None
    vibe: Optional[str] = None
    total_rooms: Optional[int] = None
    accommodates_5: Optional[bool] = None
    # Expanded fields
    star_rating: Optional[float] = None
    official_website: Optional[str] = None
    resort_chain: Optional[str] = None
    room_fit_for_5_type: Optional[str] = None
    room_types_for_5: Optional[list[str]] = None
    max_occupancy_standard_room: Optional[int] = None
    connecting_rooms_available: Optional[bool] = None
    kids_club_ages: Optional[str] = None
    kids_pool: Optional[bool] = None
    waterpark_notes: Optional[str] = None
    babysitting_available: Optional[bool] = None
    beach_access: Optional[bool] = None
    beach_type: Optional[str] = None
    beach_description: Optional[str] = None
    pool_count: Optional[int] = None
    nearest_airport_code: Optional[str] = None
    airport_transfer_included: Optional[bool] = None
    tripadvisor_rating: Optional[float] = None
    tripadvisor_review_count: Optional[int] = None
    restaurant_names: Optional[list[str]] = None
    best_time_to_visit: Optional[str] = None
    top_praise: Optional[list[str]] = None
    primary_demographics: Optional[str] = None
    resort_layout: Optional[str] = None

    model_config = {"from_attributes": True}


def _ensure_list(val) -> list[str]:
    """Safely coerce a JSONB value to a list of strings."""
    if isinstance(val, list):
        return val
    return []


def _row_to_response(row: HotelIntel) -> HotelIntelResponse:
    return HotelIntelResponse(
        hotel_name=row.hotel_name,
        resort_size=row.resort_size,
        adults_only=row.adults_only,
        kids_club=row.kids_club,
        teen_club=row.teen_club,
        waterpark=row.waterpark,
        transfer_time_minutes=row.transfer_time_minutes,
        sargassum_risk=row.sargassum_risk,
        sargassum_notes=row.sargassum_notes,
        red_flags=_ensure_list(row.red_flags),
        top_complaints=_ensure_list(row.top_complaints),
        vibe=row.vibe,
        total_rooms=row.total_rooms,
        accommodates_5=row.accommodates_5,
        star_rating=float(row.star_rating) if row.star_rating is not None else None,
        official_website=row.official_website,
        resort_chain=row.resort_chain,
        room_fit_for_5_type=row.room_fit_for_5_type,
        room_types_for_5=_ensure_list(row.room_types_for_5),
        max_occupancy_standard_room=row.max_occupancy_standard_room,
        connecting_rooms_available=row.connecting_rooms_available,
        kids_club_ages=row.kids_club_ages,
        kids_pool=row.kids_pool,
        waterpark_notes=row.waterpark_notes,
        babysitting_available=row.babysitting_available,
        beach_access=row.beach_access,
        beach_type=row.beach_type,
        beach_description=row.beach_description,
        pool_count=row.pool_count,
        nearest_airport_code=row.nearest_airport_code,
        airport_transfer_included=row.airport_transfer_included,
        tripadvisor_rating=float(row.tripadvisor_rating) if row.tripadvisor_rating is not None else None,
        tripadvisor_review_count=row.tripadvisor_review_count,
        restaurant_names=_ensure_list(row.restaurant_names),
        best_time_to_visit=row.best_time_to_visit,
        top_praise=_ensure_list(row.top_praise),
        primary_demographics=row.primary_demographics,
        resort_layout=row.resort_layout,
    )


@router.get("/intel", response_model=HotelIntelResponse)
@limiter.limit("30/minute")
def get_hotel_intel(
    request: Request,
    hotel_name: str = Query(..., min_length=1, max_length=300, description="Hotel name to look up"),
    clerk_id: str = Depends(get_clerk_user_id),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(HotelIntel).where(
            func.lower(HotelIntel.hotel_name) == func.lower(hotel_name)
        ).limit(1)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Hotel not found")

    return _row_to_response(row)


class HotelIntelBatchRequest(BaseModel):
    hotel_names: list[str]

    @field_validator("hotel_names")
    @classmethod
    def validate_names(cls, v: list[str]) -> list[str]:
        return [n[:300] for n in v if len(n) > 0][:50]


@router.post("/intel/batch", response_model=dict[str, HotelIntelResponse])
@limiter.limit("10/minute")
def get_hotel_intel_batch(
    request: Request,
    body: HotelIntelBatchRequest,
    clerk_id: str = Depends(get_clerk_user_id),
    db: Session = Depends(get_db),
):
    """Look up hotel intel for multiple hotels in a single request."""
    names = list(set(body.hotel_names))
    if not names:
        return {}

    rows = db.execute(
        select(HotelIntel).where(
            func.lower(HotelIntel.hotel_name).in_([n.lower() for n in names])
        )
    ).scalars().all()

    return {row.hotel_name: _row_to_response(row) for row in rows}
