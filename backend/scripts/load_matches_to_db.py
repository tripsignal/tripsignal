#!/usr/bin/env python3
"""Load TripAdvisor match results into the hotel_links table.

For "matched" results: sets tripadvisor_url, tripadvisor_id, and metadata.
For review items: sets suggested_url/suggested_name so admin can approve in UI.

Usage:
    cd backend
    python -m scripts.load_matches_to_db --input data/tripadvisor/match_results.json

    # Dry run:
    python -m scripts.load_matches_to_db --input data/tripadvisor/match_results.json --dry-run

Requires DATABASE_URL or individual POSTGRES_* env vars.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.hotel_link import HotelLink

logger = logging.getLogger("load_matches")


def get_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "postgres")
        pw = os.getenv("POSTGRES_PASSWORD", "postgres")
        db = os.getenv("POSTGRES_DB", "tripsignal")
        url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"
    return create_engine(url)


def main():
    parser = argparse.ArgumentParser(description="Load match results into hotel_links")
    parser.add_argument("--input", required=True, help="Path to match_results.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    with open(args.input, "r", encoding="utf-8") as f:
        results = json.load(f)

    logger.info("Loaded %d match results from %s", len(results), args.input)

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    stats = {"updated": 0, "suggested": 0, "skipped_no_hotel_id": 0, "skipped_not_in_db": 0}

    try:
        for r in results:
            hotel_id = r.get("source_hotel_id", "").strip()
            if not hotel_id:
                stats["skipped_no_hotel_id"] += 1
                continue

            hotel = db.execute(
                select(HotelLink).where(HotelLink.hotel_id == hotel_id)
            ).scalar_one_or_none()

            if not hotel:
                stats["skipped_not_in_db"] += 1
                continue

            status = r.get("review_status", "not_found")
            confidence = r.get("match_confidence")
            method = r.get("match_method")
            ta_url = r.get("tripadvisor_url")
            ta_id = r.get("tripadvisor_id")
            ta_name = r.get("tripadvisor_matched_name")
            notes = r.get("notes")

            if args.dry_run:
                action = "SET URL" if status == "matched" else "SUGGEST"
                logger.info("[DRY RUN] %s %s → %s (%.2f, %s)",
                            action, hotel.hotel_name, ta_url or "(none)",
                            confidence or 0, status)

            if status == "matched" and ta_url:
                # High-confidence match — set the URL directly
                if not hotel.tripadvisor_url:
                    hotel.tripadvisor_url = ta_url
                hotel.tripadvisor_id = ta_id
                hotel.match_confidence = confidence
                hotel.match_method = method
                hotel.review_status = "matched"
                hotel.match_notes = notes
                hotel.suggested_url = None
                hotel.suggested_name = None
                hotel.updated_at = datetime.now(timezone.utc)
                stats["updated"] += 1
            else:
                # Needs review — store suggestion, don't overwrite existing URL
                hotel.match_confidence = confidence
                hotel.match_method = method
                hotel.review_status = status
                hotel.match_notes = notes
                if ta_url:
                    hotel.suggested_url = ta_url
                    hotel.suggested_name = ta_name
                    hotel.tripadvisor_id = ta_id
                stats["suggested"] += 1

        if not args.dry_run:
            db.commit()
            logger.info("Committed changes to database")
        else:
            db.rollback()
            logger.info("Dry run — no changes committed")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info("Results: %s", stats)
    print(f"\n  Updated (URL set):    {stats['updated']}")
    print(f"  Suggested (review):   {stats['suggested']}")
    print(f"  Skipped (no ID):      {stats['skipped_no_hotel_id']}")
    print(f"  Skipped (not in DB):  {stats['skipped_not_in_db']}")


if __name__ == "__main__":
    main()
