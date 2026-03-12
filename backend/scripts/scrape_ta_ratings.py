#!/usr/bin/env python3
"""Scrape TripAdvisor ratings via Google search snippets.

For each hotel with a tripadvisor_url but no ta_rating, searches Google for
the TripAdvisor page and extracts rating + review count from the snippet.

Usage:
    cd backend
    python -m scripts.scrape_ta_ratings
    python -m scripts.scrape_ta_ratings --limit 50 --dry-run

Requires DATABASE_URL or individual POSTGRES_* env vars.
"""

import argparse
import logging
import os
import re
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models.hotel_link import HotelLink

logger = logging.getLogger("scrape_ta_ratings")

# Google search with a browser-like User-Agent
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Delay between Google requests to avoid rate limiting
MIN_DELAY = 3.0
MAX_DELAY = 6.0


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


def _extract_rating_from_snippet(text: str) -> tuple[float | None, int | None]:
    """Extract rating and review count from a Google snippet.

    Google snippets for TripAdvisor pages typically show:
    - "Rating: 4.5 · ‎12,345 reviews"
    - "4.5/5 · 12345 reviews"
    - "Rated 4.5 of 5 · 12,345 reviews"
    - "4.5 (12,345)"
    """
    rating = None
    review_count = None

    # Pattern 1: "Rating: X.X" or "Rated X.X"
    m = re.search(r"(?:Rating|Rated)[:\s]+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        rating = float(m.group(1))

    # Pattern 2: "X.X/5" or "X.X out of 5"
    if not rating:
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", text)
        if m:
            rating = float(m.group(1))

    # Pattern 3: standalone rating-like number near "reviews" or "·"
    if not rating:
        m = re.search(r"(\d\.\d)\s*[·•\-–—]", text)
        if m:
            rating = float(m.group(1))

    # Review count: "X,XXX reviews" or "(X,XXX)"
    m = re.search(r"([\d,]+)\s*reviews", text, re.IGNORECASE)
    if m:
        review_count = int(m.group(1).replace(",", ""))

    if not review_count:
        m = re.search(r"\(([\d,]+)\)", text)
        if m:
            val = int(m.group(1).replace(",", ""))
            if val > 10:  # likely a review count, not a year
                review_count = val

    # Sanity checks
    if rating and (rating < 1.0 or rating > 5.0):
        rating = None
    if review_count and review_count > 500_000:
        review_count = None

    return rating, review_count


def _extract_ranking_from_snippet(text: str) -> str | None:
    """Extract ranking text like '#12 of 50 hotels in Punta Cana'."""
    m = re.search(r"(#\d+\s+of\s+\d+\s+hotels?\s+in\s+[^.·\n]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _search_google(query: str) -> str | None:
    """Perform a Google search and return the raw HTML of the results page."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&num=3"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        if resp.status_code == 429:
            logger.warning("Google rate-limited (429) — backing off")
            return None
        if resp.status_code != 200:
            logger.warning("Google returned status %d", resp.status_code)
            return None
        return resp.text
    except requests.RequestException as e:
        logger.warning("Google search failed: %s", e)
        return None


def scrape_hotel_rating(hotel_name: str, tripadvisor_url: str) -> dict:
    """Search Google for a hotel's TripAdvisor page and extract rating data.

    Returns dict with keys: ta_rating, ta_review_count, ta_ranking_text
    """
    result = {"ta_rating": None, "ta_review_count": None, "ta_ranking_text": None}

    # Search for the specific TripAdvisor URL
    query = f'site:tripadvisor.com "{hotel_name}"'
    html = _search_google(query)
    if not html:
        return result

    # Extract text content from the search results (strip HTML tags)
    # Focus on the snippet area
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    rating, review_count = _extract_rating_from_snippet(text)
    ranking = _extract_ranking_from_snippet(text)

    result["ta_rating"] = rating
    result["ta_review_count"] = review_count
    result["ta_ranking_text"] = ranking

    return result


def main():
    parser = argparse.ArgumentParser(description="Scrape TripAdvisor ratings via Google")
    parser.add_argument("--limit", type=int, default=0, help="Max hotels to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if already fetched")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        query = select(HotelLink).where(
            HotelLink.tripadvisor_url.isnot(None),
            HotelLink.tripadvisor_url != "",
        )
        if not args.force:
            query = query.where(HotelLink.ta_data_fetched_at.is_(None))

        query = query.order_by(HotelLink.hotel_name)
        if args.limit:
            query = query.limit(args.limit)

        hotels = db.execute(query).scalars().all()
        logger.info("Found %d hotels to scrape", len(hotels))

        stats = {"scraped": 0, "found_rating": 0, "no_data": 0, "errors": 0}

        for i, hotel in enumerate(hotels):
            logger.info("[%d/%d] %s", i + 1, len(hotels), hotel.hotel_name)

            try:
                data = scrape_hotel_rating(hotel.hotel_name, hotel.tripadvisor_url)
            except Exception:
                logger.exception("Error scraping %s", hotel.hotel_name)
                stats["errors"] += 1
                continue

            if data["ta_rating"]:
                stats["found_rating"] += 1
                logger.info("  → %.1f stars, %s reviews",
                            data["ta_rating"],
                            f"{data['ta_review_count']:,}" if data["ta_review_count"] else "?")
            else:
                stats["no_data"] += 1
                logger.info("  → No rating found")

            if not args.dry_run:
                hotel.ta_rating = data["ta_rating"]
                hotel.ta_review_count = data["ta_review_count"]
                hotel.ta_ranking_text = data["ta_ranking_text"]
                hotel.ta_data_fetched_at = datetime.now(timezone.utc)

            stats["scraped"] += 1

            # Rate limiting
            if i < len(hotels) - 1:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                time.sleep(delay)

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
    print(f"\n  Scraped:       {stats['scraped']}")
    print(f"  Found rating:  {stats['found_rating']}")
    print(f"  No data:       {stats['no_data']}")
    print(f"  Errors:        {stats['errors']}")


if __name__ == "__main__":
    main()
