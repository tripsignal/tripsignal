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

    model_config = {"from_attributes": True}


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
        red_flags=row.red_flags if isinstance(row.red_flags, list) else [],
        top_complaints=row.top_complaints if isinstance(row.top_complaints, list) else [],
        vibe=row.vibe,
        total_rooms=row.total_rooms,
        accommodates_5=row.accommodates_5,
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
