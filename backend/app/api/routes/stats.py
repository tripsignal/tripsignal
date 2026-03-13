"""Public stats endpoints (no auth required)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.session import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/active-deals")
@limiter.limit("30/minute")
def get_active_deal_count(request: Request, db: Session = Depends(get_db)):
    """Return count of currently active deals and unique hotels. Public endpoint, no auth."""
    row = db.query(
        func.count(Deal.id),
        func.count(func.distinct(Deal.hotel_id)),
    ).filter(Deal.is_active == True).one()

    count, hotels = row
    return JSONResponse(
        content={"count": count or 0, "hotels": hotels or 0},
        headers={"Cache-Control": "public, max-age=600"},
    )
