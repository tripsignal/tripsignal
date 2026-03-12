"""Find TripAdvisor URLs for hotels missing them, via DuckDuckGo search.

Searches DDG for 'site:tripadvisor.com [hotel name] [destination]' and
extracts the Hotel_Review URL from results. Updates the DB directly.

Usage:
    docker exec tripsignal-api python -m scripts.find_ta_urls [--dry-run] [--limit N]
"""

import argparse
import re
import time
import random
import urllib.parse

import requests
from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.db.models.hotel_link import HotelLink


DDG_URL = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Match TripAdvisor Hotel_Review URLs
TA_URL_RE = re.compile(
    r'(https?://(?:www\.)?tripadvisor\.(?:com|ca)/Hotel_Review-[^\s"\'<>&]+)',
    re.IGNORECASE,
)


def search_ddg(query: str) -> str | None:
    """Search DuckDuckGo and return the first TripAdvisor Hotel_Review URL found."""
    params = {"q": query}
    try:
        resp = requests.get(DDG_URL, params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        matches = TA_URL_RE.findall(resp.text)
        if matches:
            # Clean the URL — remove tracking params and fragments
            url = matches[0].split("&amp;")[0].split("?")[0]
            # Normalize to www.tripadvisor.com
            url = re.sub(r"tripadvisor\.ca", "tripadvisor.com", url)
            return url
    except Exception as e:
        print(f"  Search error: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description="Find TripAdvisor URLs for hotels")
    parser.add_argument("--dry-run", action="store_true", help="Don't update DB")
    parser.add_argument("--limit", type=int, default=0, help="Max hotels to process")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Get hotels without a TripAdvisor URL
        query = (
            select(HotelLink)
            .where(HotelLink.tripadvisor_url.is_(None))
            .order_by(HotelLink.hotel_name)
        )
        hotels = db.execute(query).scalars().all()

        if args.limit:
            hotels = hotels[: args.limit]

        print(f"Found {len(hotels)} hotels without TripAdvisor URLs\n")

        found = 0
        not_found = 0

        for i, hotel in enumerate(hotels, 1):
            name = hotel.hotel_name
            dest = hotel.destination or ""
            # Build search query
            search_query = f"site:tripadvisor.com {name} {dest}".strip()

            print(f"[{i}/{len(hotels)}] {name} ({dest})")
            url = search_ddg(search_query)

            if url:
                print(f"  Found: {url}")
                found += 1
                if not args.dry_run:
                    hotel.tripadvisor_url = url
                    db.commit()
            else:
                print("  Not found")
                not_found += 1

            # Rate limit: 2-4 seconds between requests
            if i < len(hotels):
                delay = 2 + random.random() * 2
                time.sleep(delay)

        print(f"\nDone: {found} found, {not_found} not found")
        if args.dry_run:
            print("(dry run — no DB changes made)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
