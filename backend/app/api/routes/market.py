"""Market intelligence endpoints."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory
from app.db.session import get_db

logger = logging.getLogger("market")

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview")
async def market_overview(db: Session = Depends(get_db)):
    """Public market overview metrics for the signals page header."""

    # Total active packages
    total_packages = db.execute(
        select(func.count(Deal.id)).where(Deal.is_active == True)
    ).scalar() or 0

    # Unique resorts (by hotel_name, excluding nulls)
    total_resorts = db.execute(
        select(func.count(func.distinct(Deal.hotel_name)))
        .where(Deal.is_active == True)
        .where(Deal.hotel_name.isnot(None))
    ).scalar() or 0

    # Price drops in the last 24 hours:
    # Find deals where the latest price history entry is lower than the previous one
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    price_drops = db.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT deal_id
            FROM (
                SELECT
                    deal_id,
                    price_cents,
                    LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                    recorded_at,
                    ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
                FROM deal_price_history
                WHERE recorded_at >= :cutoff
            ) sub
            WHERE rn = 1 AND prev_price IS NOT NULL AND price_cents < prev_price
        ) drops
    """), {"cutoff": cutoff}).scalar() or 0

    return {
        "total_packages": total_packages,
        "total_resorts": total_resorts,
        "price_drops_today": price_drops,
    }
