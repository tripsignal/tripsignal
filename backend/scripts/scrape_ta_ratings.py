#!/usr/bin/env python3
"""Scrape TripAdvisor ratings via DuckDuckGo search snippets.

For each hotel with a tripadvisor_url but no ta_rating, searches DuckDuckGo
for the TripAdvisor page and extracts rating, review count, and ranking
from the snippet.

Usage:
    cd backend
    python -m scripts.scrape_ta_ratings
    python -m scripts.scrape_ta_ratings --limit 50 --dry-run

Uses DataImpulse residential proxy when PROXY_ENABLED=true (same env vars
as scrape_orchestrator). Falls back to direct connection otherwise.

Requires DATABASE_URL or individual POSTGRES_* env vars.
"""

import argparse
import json
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("scrape_ta_ratings")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Delay between requests to avoid rate limiting
MIN_DELAY = 4.0
MAX_DELAY = 8.0

# Back off aggressively on rate limits
RATE_LIMIT_BACKOFF = 60.0


def _build_proxy_config() -> dict | None:
    """Build requests-compatible proxies dict from env vars. Returns None if disabled."""
    if os.getenv("PROXY_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return None
    proxy_user = os.getenv("PROXY_USER", "")
    if not proxy_user:
        return None
    proxy_host = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
    proxy_port = os.getenv("PROXY_PORT", "823")
    proxy_pass = os.getenv("PROXY_PASS", "")
    proxy_country = os.getenv("PROXY_COUNTRY", "cr.ca")
    proxy_url = f"http://{proxy_user}__{proxy_country}:{proxy_pass}@{proxy_host}:{proxy_port}"
    return {"http": proxy_url, "https": proxy_url}


_PROXIES = None  # initialized lazily


def _get_proxies() -> dict | None:
    global _PROXIES
    if _PROXIES is None:
        _PROXIES = _build_proxy_config() or {}
    return _PROXIES or None


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


def _search_ddg(query: str) -> str | None:
    """Search DuckDuckGo HTML and return the text content of the results page."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    proxies = _get_proxies()
    try:
        resp = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
        if resp.status_code in (429, 403, 202):
            logger.warning("DuckDuckGo rate-limited (%d) — backing off %.0fs",
                           resp.status_code, RATE_LIMIT_BACKOFF)
            time.sleep(RATE_LIMIT_BACKOFF)
            # Retry once after backoff
            resp = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
            if resp.status_code != 200:
                logger.warning("Still rate-limited after backoff (%d)", resp.status_code)
                return None
        if resp.status_code != 200:
            logger.warning("DuckDuckGo returned status %d", resp.status_code)
            return None
        # Strip HTML tags to get plain text
        text = re.sub(r"<[^>]+>", " ", resp.text)
        return re.sub(r"\s+", " ", text)
    except requests.RequestException as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return None


def _extract_from_ddg(text: str) -> dict:
    """Extract rating, review count, and ranking from DDG search result text.

    DDG snippets for TripAdvisor pages typically contain:
    - "rated 4 of 5 at Tripadvisor"
    - "42,791 traveller reviews"
    - "ranked #164 of 627 hotels in Bavaro"
    """
    result = {"ta_rating": None, "ta_review_count": None, "ta_ranking_text": None}

    # Rating: "rated X of 5"
    m = re.search(r"rated?\s+(\d\.?\d?)\s+of\s+5", text, re.IGNORECASE)
    if m:
        rating = float(m.group(1))
        if 1.0 <= rating <= 5.0:
            result["ta_rating"] = rating

    # Review count: "X,XXX traveller/traveler reviews"
    m = re.search(r"(\d[\d,]*)\s+(?:travell?er\s+)?reviews", text, re.IGNORECASE)
    if m:
        count_str = m.group(1).replace(",", "")
        if count_str.isdigit():
            count = int(count_str)
            if 0 < count < 500_000:
                result["ta_review_count"] = count

    # Ranking: "ranked #X of Y hotels in Z" — stop at common delimiters
    m = re.search(
        r"ranked?\s+#(\d+)\s+of\s+(\d+)\s+hotels?\s+in\s+([A-Za-z\s]+?)(?:\s+and\s+|\s*[,.]|\s*$)",
        text, re.IGNORECASE,
    )
    if m:
        result["ta_ranking_text"] = f"#{m.group(1)} of {m.group(2)} hotels in {m.group(3).strip()}"

    return result


def scrape_hotel_rating(hotel_name: str) -> dict:
    """Search DuckDuckGo for a hotel's TripAdvisor data.

    Returns dict with keys: ta_rating, ta_review_count, ta_ranking_text
    """
    query = f"tripadvisor {hotel_name}"
    text = _search_ddg(query)
    if not text:
        return {"ta_rating": None, "ta_review_count": None, "ta_ranking_text": None}
    return _extract_from_ddg(text)


def _run_from_json(args):
    """Run scraper from a JSON input file (no DB required).

    Input: JSON array of {"hotel_id": "...", "hotel_name": "...", "tripadvisor_url": "..."}
    Output: JSON file with scraped data added.
    """
    with open(args.input, "r", encoding="utf-8") as f:
        hotels = json.load(f)

    if args.limit:
        hotels = hotels[:args.limit]

    logger.info("Loaded %d hotels from %s", len(hotels), args.input)
    stats = {"scraped": 0, "found_rating": 0, "found_reviews": 0, "no_data": 0, "errors": 0}
    results = []

    for i, hotel in enumerate(hotels):
        name = hotel.get("hotel_name", "")
        logger.info("[%d/%d] %s", i + 1, len(hotels), name)

        try:
            data = scrape_hotel_rating(name)
        except Exception:
            logger.exception("Error scraping %s", name)
            stats["errors"] += 1
            results.append({**hotel, "ta_rating": None, "ta_review_count": None, "ta_ranking_text": None})
            continue

        found = data["ta_rating"] or data["ta_review_count"]
        if found:
            if data["ta_rating"]:
                stats["found_rating"] += 1
            if data["ta_review_count"]:
                stats["found_reviews"] += 1
            logger.info("  → %s stars, %s reviews, %s",
                        data["ta_rating"] or "?",
                        f"{data['ta_review_count']:,}" if data["ta_review_count"] else "?",
                        data["ta_ranking_text"] or "no ranking")
        else:
            stats["no_data"] += 1
            logger.info("  → No data found")

        results.append({**hotel, **data})
        stats["scraped"] += 1

        # Save progress periodically
        if stats["scraped"] % 25 == 0:
            _save_results(results, args.output)
            logger.info("Saved progress (%d scraped)", stats["scraped"])

        if i < len(hotels) - 1:
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            time.sleep(delay)

    _save_results(results, args.output)
    _print_stats(stats)


def _run_from_db(args):
    """Run scraper against the database directly."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db.models.hotel_link import HotelLink

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

        if not hotels:
            print("Nothing to scrape.")
            return

        stats = {"scraped": 0, "found_rating": 0, "found_reviews": 0, "no_data": 0, "errors": 0}

        for i, hotel in enumerate(hotels):
            logger.info("[%d/%d] %s", i + 1, len(hotels), hotel.hotel_name)

            try:
                data = scrape_hotel_rating(hotel.hotel_name)
            except Exception:
                logger.exception("Error scraping %s", hotel.hotel_name)
                stats["errors"] += 1
                continue

            found = data["ta_rating"] or data["ta_review_count"]
            if found:
                if data["ta_rating"]:
                    stats["found_rating"] += 1
                if data["ta_review_count"]:
                    stats["found_reviews"] += 1
                logger.info("  → %s stars, %s reviews, %s",
                            data["ta_rating"] or "?",
                            f"{data['ta_review_count']:,}" if data["ta_review_count"] else "?",
                            data["ta_ranking_text"] or "no ranking")
            else:
                stats["no_data"] += 1
                logger.info("  → No data found")

            if not args.dry_run:
                if data["ta_rating"]:
                    hotel.ta_rating = data["ta_rating"]
                if data["ta_review_count"]:
                    hotel.ta_review_count = data["ta_review_count"]
                if data["ta_ranking_text"]:
                    hotel.ta_ranking_text = data["ta_ranking_text"]
                hotel.ta_data_fetched_at = datetime.now(timezone.utc)

            stats["scraped"] += 1

            if not args.dry_run and stats["scraped"] % 50 == 0:
                db.commit()
                logger.info("Committed batch (%d scraped so far)", stats["scraped"])

            if i < len(hotels) - 1:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                time.sleep(delay)

        if not args.dry_run:
            db.commit()
            logger.info("Final commit done")
        else:
            db.rollback()
            logger.info("Dry run — no changes committed")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    _print_stats(stats)


def _save_results(results: list, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def _print_stats(stats: dict):
    logger.info("Results: %s", stats)
    print(f"\n  Scraped:        {stats['scraped']}")
    print(f"  Found rating:   {stats['found_rating']}")
    print(f"  Found reviews:  {stats['found_reviews']}")
    print(f"  No data:        {stats['no_data']}")
    print(f"  Errors:         {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(description="Scrape TripAdvisor ratings via DuckDuckGo")
    parser.add_argument("--input", help="JSON file of hotels (offline mode, no DB needed)")
    parser.add_argument("--output", default="data/tripadvisor/ta_ratings.json",
                        help="Output JSON file (offline mode)")
    parser.add_argument("--limit", type=int, default=0, help="Max hotels to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing (DB mode)")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if already fetched")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.input:
        _run_from_json(args)
    else:
        _run_from_db(args)


if __name__ == "__main__":
    main()
