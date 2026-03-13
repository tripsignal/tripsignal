"""Public stats endpoints (no auth required)."""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.session import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])

# In-memory cache: avoids repeated DB queries regardless of caller behaviour.
# Stores (timestamp, result_dict). TTL = 10 minutes.
_cache: tuple[float, dict] | None = None
_CACHE_TTL = 600


@router.get("/active-deals")
@limiter.limit("30/minute")
def get_active_deal_count(request: Request, db: Session = Depends(get_db)):
    """Return count of currently active deals and unique hotels. Public endpoint, no auth."""
    global _cache

    now = time.monotonic()
    if _cache and (now - _cache[0]) < _CACHE_TTL:
        return JSONResponse(
            content=_cache[1],
            headers={"Cache-Control": "public, max-age=600"},
        )

    row = db.query(
        func.count(Deal.id),
        func.count(func.distinct(Deal.hotel_id)),
    ).filter(Deal.is_active == True).one()

    count, hotels = row
    result = {"count": count or 0, "hotels": hotels or 0}
    _cache = (now, result)

    return JSONResponse(
        content=result,
        headers={"Cache-Control": "public, max-age=600"},
    )
