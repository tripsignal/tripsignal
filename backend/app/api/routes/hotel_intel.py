"""Hotel intelligence endpoint."""
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi import Depends
import json

from app.db.session import get_db

router = APIRouter(prefix="/api/hotels", tags=["hotels"])


@router.get("/intel")
def get_hotel_intel(
    hotel_name: str = Query(..., description="Hotel name to look up"),
    db: Session = Depends(get_db),
):
    row = db.execute(
        text("""
            SELECT hotel_name, resort_size, adults_only, kids_club, teen_club,
                   waterpark, transfer_time_minutes, sargassum_risk,
                   sargassum_notes, red_flags, top_complaints, vibe, total_rooms,
                   accommodates_5,
                   star_rating, official_website, resort_chain,
                   room_fit_for_5_type, room_types_for_5,
                   max_occupancy_standard_room, connecting_rooms_available,
                   kids_club_ages, kids_pool, waterpark_notes,
                   babysitting_available,
                   beach_access, beach_type, beach_description,
                   pool_count, nearest_airport_code, airport_transfer_included,
                   tripadvisor_rating, tripadvisor_review_count,
                   restaurant_names, best_time_to_visit, top_praise,
                   primary_demographics, resort_layout
            FROM hotel_intel
            WHERE LOWER(hotel_name) = LOWER(:name)
            LIMIT 1
        """),
        {"name": hotel_name},
    ).fetchone()

    if not row:
        return JSONResponse(content=None, status_code=404)

    def parse_jsonb(val):
        if val is None:
            return []
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return []
        return val

    return {
        "hotel_name": row.hotel_name,
        "resort_size": row.resort_size,
        "adults_only": row.adults_only,
        "kids_club": row.kids_club,
        "teen_club": row.teen_club,
        "waterpark": row.waterpark,
        "transfer_time_minutes": row.transfer_time_minutes,
        "sargassum_risk": row.sargassum_risk,
        "sargassum_notes": row.sargassum_notes,
        "red_flags": parse_jsonb(row.red_flags),
        "top_complaints": parse_jsonb(row.top_complaints),
        "vibe": row.vibe,
        "total_rooms": row.total_rooms,
        "accommodates_5": row.accommodates_5,
        "star_rating": float(row.star_rating) if row.star_rating else None,
        "official_website": row.official_website,
        "resort_chain": row.resort_chain,
        "room_fit_for_5_type": row.room_fit_for_5_type,
        "room_types_for_5": parse_jsonb(row.room_types_for_5),
        "max_occupancy_standard_room": row.max_occupancy_standard_room,
        "connecting_rooms_available": row.connecting_rooms_available,
        "kids_club_ages": row.kids_club_ages,
        "kids_pool": row.kids_pool,
        "waterpark_notes": row.waterpark_notes,
        "babysitting_available": row.babysitting_available,
        "beach_access": row.beach_access,
        "beach_type": row.beach_type,
        "beach_description": row.beach_description,
        "pool_count": row.pool_count,
        "nearest_airport_code": row.nearest_airport_code,
        "airport_transfer_included": row.airport_transfer_included,
        "tripadvisor_rating": float(row.tripadvisor_rating) if row.tripadvisor_rating else None,
        "tripadvisor_review_count": row.tripadvisor_review_count,
        "restaurant_names": parse_jsonb(row.restaurant_names),
        "best_time_to_visit": row.best_time_to_visit,
        "top_praise": parse_jsonb(row.top_praise),
        "primary_demographics": row.primary_demographics,
        "resort_layout": row.resort_layout,
    }
