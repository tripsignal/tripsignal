"""Health check endpoints."""
import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint with DB connectivity test."""
    db_status = "ok"
    try:
        db = next(get_db())
        db.execute(text("SELECT 1"))
        db.close()
    except Exception:
        logger.exception("Health check DB ping failed")
        db_status = "error"

    status = "ok" if db_status == "ok" else "degraded"
    return {"status": status, "db": db_status}
