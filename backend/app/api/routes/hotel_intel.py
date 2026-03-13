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
                   accommodates_5
            FROM hotel_intel
            WHERE LOWER(hotel_name) = LOWER(:name)
            LIMIT 1
        """),
        {"name": hotel_name},
    ).fetchone()

    if not row:
        return JSONResponse(content=None, status_code=404)

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
        "red_flags": row.red_flags if isinstance(row.red_flags, list) else (json.loads(row.red_flags) if row.red_flags else []),
        "top_complaints": row.top_complaints if isinstance(row.top_complaints, list) else (json.loads(row.top_complaints) if row.top_complaints else []),
        "vibe": row.vibe,
        "total_rooms": row.total_rooms,
        "accommodates_5": row.accommodates_5,
    }
