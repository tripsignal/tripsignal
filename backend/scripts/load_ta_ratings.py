#!/usr/bin/env python3
"""Load scraped TripAdvisor ratings into the hotel_links table.

Reads the JSON output from scrape_ta_ratings.py (offline mode) and updates
the database.

Usage:
    cd backend
    python -m scripts.load_ta_ratings --input data/tripadvisor/ta_ratings.json
    python -m scripts.load_ta_ratings --input data/tripadvisor/ta_ratings.json --dry-run

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

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models.hotel_link import HotelLink

logger = logging.getLogger("load_ta_ratings")


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
    parser = argparse.ArgumentParser(description="Load scraped TA ratings into DB")
    parser.add_argument("--input", required=True, help="Path to ta_ratings.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    with open(args.input, "r", encoding="utf-8") as f:
        results = json.load(f)

    logger.info("Loaded %d results from %s", len(results), args.input)

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    stats = {"updated": 0, "skipped_no_data": 0, "skipped_not_in_db": 0}

    try:
        for r in results:
            hotel_id = r.get("hotel_id", "").strip()
            if not hotel_id:
                continue

            has_data = r.get("ta_rating") or r.get("ta_review_count")
            if not has_data:
                stats["skipped_no_data"] += 1
                continue

            hotel = db.execute(
                select(HotelLink).where(HotelLink.hotel_id == hotel_id)
            ).scalar_one_or_none()

            if not hotel:
                stats["skipped_not_in_db"] += 1
                continue

            if args.dry_run:
                logger.info("[DRY RUN] %s → rating=%s, reviews=%s, ranking=%s",
                            hotel.hotel_name,
                            r.get("ta_rating"), r.get("ta_review_count"),
                            r.get("ta_ranking_text"))

            if r.get("ta_rating"):
                hotel.ta_rating = r["ta_rating"]
            if r.get("ta_review_count"):
                hotel.ta_review_count = r["ta_review_count"]
            if r.get("ta_ranking_text"):
                hotel.ta_ranking_text = r["ta_ranking_text"]
            hotel.ta_data_fetched_at = datetime.now(timezone.utc)
            stats["updated"] += 1

        if not args.dry_run:
            db.commit()
            logger.info("Committed changes")
        else:
            db.rollback()
            logger.info("Dry run — no changes committed")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info("Results: %s", stats)
    print(f"\n  Updated:          {stats['updated']}")
    print(f"  Skipped (no data): {stats['skipped_no_data']}")
    print(f"  Skipped (not in DB): {stats['skipped_not_in_db']}")


if __name__ == "__main__":
    main()
