"""Public stats endpoints (no auth required)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.session import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/active-deals")
def get_active_deal_count(db: Session = Depends(get_db)):
    """Return count of currently active deals and unique hotels. Public endpoint, no auth."""
    count = db.query(func.count(Deal.id)).filter(Deal.is_active == True).scalar() or 0
    hotels = (
        db.query(func.count(func.distinct(Deal.hotel_id)))
        .filter(Deal.is_active == True, Deal.hotel_id.isnot(None))
        .scalar()
    ) or 0
    return {"count": count, "hotels": hotels}
