"""Auto-match newly added hotels against the TripAdvisor seed dataset.

Called after the hotel_links auto-sync in the admin hotels endpoint.
Only processes hotels that haven't been through the matcher yet
(review_status IS NULL).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.hotel_link import HotelLink
from app.enrichment.tripadvisor_matcher import (
    SeedHotel,
    SourceHotel,
    TripAdvisorMatcher,
    extract_tripadvisor_id,
)

logger = logging.getLogger(__name__)

# Lazy-loaded singleton — seed data doesn't change at runtime
_matcher: TripAdvisorMatcher | None = None

# Path to seed CSV inside the Docker container
_SEED_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "tripadvisor" / "tripadvisor_seed.csv"


def _get_matcher() -> TripAdvisorMatcher | None:
    """Load the TripAdvisor matcher singleton from the seed CSV."""
    global _matcher
    if _matcher is not None:
        return _matcher
    if not _SEED_CSV.exists():
        logger.warning("Seed CSV not found at %s — auto-matching disabled", _SEED_CSV)
        return None
    _matcher = TripAdvisorMatcher.from_csv(_SEED_CSV)
    return _matcher


def auto_match_new_hotels(db: Session) -> int:
    """Match any hotel_links rows that haven't been through the matcher yet.

    Returns the number of hotels processed.
    """
    unmatched = db.execute(
        select(HotelLink).where(HotelLink.review_status.is_(None))
    ).scalars().all()

    if not unmatched:
        return 0

    matcher = _get_matcher()
    if not matcher:
        return 0

    logger.info("Auto-matching %d new hotels", len(unmatched))
    count = 0

    for hotel in unmatched:
        source = SourceHotel(
            hotel_name=hotel.hotel_name or "",
            hotel_id=hotel.hotel_id or "",
            destination_str=hotel.destination or "",
        )
        result = matcher.match(source)

        hotel.match_confidence = result.match_confidence
        hotel.match_method = result.match_method
        hotel.review_status = result.review_status
        hotel.match_notes = result.notes

        if result.review_status == "matched" and result.tripadvisor_url:
            hotel.tripadvisor_url = result.tripadvisor_url
            hotel.tripadvisor_id = result.tripadvisor_id
        elif result.tripadvisor_url:
            hotel.suggested_url = result.tripadvisor_url
            hotel.suggested_name = result.tripadvisor_matched_name
            hotel.tripadvisor_id = result.tripadvisor_id

        hotel.updated_at = datetime.now(timezone.utc)
        count += 1

    try:
        db.commit()
        logger.info("Auto-matched %d hotels", count)
    except Exception:
        db.rollback()
        logger.exception("Failed to commit auto-match results")
        raise

    return count
