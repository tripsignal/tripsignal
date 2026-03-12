#!/usr/bin/env python3
"""Search TripAdvisor for unmatched hotels via DuckDuckGo.

For hotels with no tripadvisor_url and review_status in
(not_found, needs_manual_review, NULL), searches DuckDuckGo for
their TripAdvisor page and stores the best candidate URL as a suggestion.

Usage:
    cd backend
    python -m scripts.search_ta_unmatched --limit 20 --dry-run
    python -m scripts.search_ta_unmatched

Uses DataImpulse residential proxy when PROXY_ENABLED=true (same env vars
as scrape_orchestrator). Falls back to direct connection otherwise.

Requires DATABASE_URL or individual POSTGRES_* env vars.
Can also run in offline JSON mode with --input/--output.
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
from urllib.parse import quote_plus, unquote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("search_ta_unmatched")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_DELAY = 4.0
MAX_DELAY = 8.0
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


_PROXIES = None


def _get_proxies() -> dict | None:
    global _PROXIES
    if _PROXIES is None:
        _PROXIES = _build_proxy_config() or {}
    return _PROXIES or None


def get_engine():
    from sqlalchemy import create_engine
    url = os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "postgres")
        pw = os.getenv("POSTGRES_PASSWORD", "postgres")
        db = os.getenv("POSTGRES_DB", "tripsignal")
        url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"
    return create_engine(url)


def _search_ddg_html(query: str) -> str | None:
    """Search DuckDuckGo and return raw HTML."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    proxies = _get_proxies()
    try:
        resp = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
        if resp.status_code in (429, 403, 202):
            logger.warning("DDG rate-limited (%d) — backing off %.0fs",
                           resp.status_code, RATE_LIMIT_BACKOFF)
            time.sleep(RATE_LIMIT_BACKOFF)
            resp = requests.get(url, headers=_HEADERS, proxies=proxies, timeout=15)
            if resp.status_code != 200:
                logger.warning("Still rate-limited after backoff (%d)", resp.status_code)
                return None
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException as e:
        logger.warning("DDG search failed: %s", type(e).__name__)
        return None


def _extract_ta_urls(html: str) -> list[dict]:
    """Extract TripAdvisor Hotel_Review URLs from DDG search results."""
    # DDG wraps URLs in redirects: //duckduckgo.com/l/?uddg=ENCODED_URL
    # Decode those first, then search for TA URLs in both raw and decoded content
    decoded_urls = []
    for m in re.finditer(r'uddg=([^&"]+)', html):
        decoded_urls.append(unquote(m.group(1)))
    searchable = html + " " + " ".join(decoded_urls)

    pattern = r'https?://(?:www\.)?tripadvisor\.(?:com|ca|co\.uk)/Hotel_Review-g\d+-d(\d+)-Reviews[^"&\s]*'
    results = []
    seen_ids = set()

    for m in re.finditer(pattern, searchable):
        url = m.group(0)
        ta_id = int(m.group(1))
        if ta_id in seen_ids:
            continue
        seen_ids.add(ta_id)

        # Clean the URL
        url = url.split("?")[0]  # strip query params
        if not url.endswith(".html"):
            url += ".html"

        # Try to extract hotel name from URL slug
        name_match = re.search(r"Reviews-(.+?)(?:-[A-Z][a-z])", url)
        name = ""
        if name_match:
            name = name_match.group(1).replace("_", " ")

        results.append({
            "tripadvisor_url": url,
            "tripadvisor_id": ta_id,
            "tripadvisor_name": name,
        })

    return results


def search_hotel(hotel_name: str, destination: str = "") -> dict | None:
    """Search DuckDuckGo for a hotel's TripAdvisor page.

    Returns the best candidate URL or None.
    """
    # Build search query
    parts = ["tripadvisor", hotel_name]
    if destination:
        parts.append(destination)
    query = " ".join(parts)

    html = _search_ddg_html(query)
    if not html:
        return None

    candidates = _extract_ta_urls(html)
    if not candidates:
        return None

    # Return the first (most relevant) result
    return candidates[0]


def _run_from_db(args):
    """Search for unmatched hotels in the database."""
    from sqlalchemy import create_engine, select, or_
    from sqlalchemy.orm import sessionmaker
    from app.db.models.hotel_link import HotelLink

    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        query = select(HotelLink).where(
            HotelLink.tripadvisor_url.is_(None),
            or_(
                HotelLink.review_status == "not_found",
                HotelLink.review_status == "needs_manual_review",
                HotelLink.review_status.is_(None),
            ),
        ).order_by(HotelLink.hotel_name)

        if args.limit:
            query = query.limit(args.limit)

        hotels = db.execute(query).scalars().all()
        logger.info("Found %d unmatched hotels to search", len(hotels))

        if not hotels:
            print("No unmatched hotels to search.")
            return

        stats = {"searched": 0, "found": 0, "not_found": 0, "errors": 0}

        for i, hotel in enumerate(hotels):
            logger.info("[%d/%d] %s (%s)", i + 1, len(hotels),
                        hotel.hotel_name, hotel.destination or "no dest")

            try:
                candidate = search_hotel(hotel.hotel_name, hotel.destination or "")
            except Exception:
                logger.exception("Error searching %s", hotel.hotel_name)
                stats["errors"] += 1
                continue

            if candidate:
                stats["found"] += 1
                logger.info("  → Found: %s (d%d)",
                            candidate["tripadvisor_name"] or candidate["tripadvisor_url"][:60],
                            candidate["tripadvisor_id"])
                if not args.dry_run:
                    hotel.suggested_url = candidate["tripadvisor_url"]
                    hotel.suggested_name = candidate["tripadvisor_name"]
                    hotel.tripadvisor_id = candidate["tripadvisor_id"]
                    hotel.review_status = "needs_manual_review"
                    hotel.match_method = "ddg_search"
                    hotel.match_notes = "Found via DuckDuckGo search"
                    hotel.updated_at = datetime.now(timezone.utc)
            else:
                stats["not_found"] += 1
                logger.info("  → No TripAdvisor result found")
                if not args.dry_run:
                    hotel.review_status = "not_found"
                    hotel.match_notes = "No TripAdvisor page found via search"
                    hotel.updated_at = datetime.now(timezone.utc)

            stats["searched"] += 1

            if not args.dry_run and stats["searched"] % 25 == 0:
                db.commit()

            if i < len(hotels) - 1:
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

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

    _print_stats(stats)


def _run_from_json(args):
    """Search for unmatched hotels from a JSON file."""
    with open(args.input, "r", encoding="utf-8") as f:
        hotels = json.load(f)

    if args.limit:
        hotels = hotels[:args.limit]

    logger.info("Loaded %d hotels from %s", len(hotels), args.input)
    stats = {"searched": 0, "found": 0, "not_found": 0, "errors": 0}
    results = []

    for i, hotel in enumerate(hotels):
        name = hotel.get("hotel_name", "")
        dest = hotel.get("destination", "")
        logger.info("[%d/%d] %s (%s)", i + 1, len(hotels), name, dest or "no dest")

        try:
            candidate = search_hotel(name, dest)
        except Exception:
            logger.exception("Error searching %s", name)
            stats["errors"] += 1
            results.append(hotel)
            continue

        if candidate:
            stats["found"] += 1
            logger.info("  → Found: %s (d%d)",
                        candidate["tripadvisor_name"] or candidate["tripadvisor_url"][:60],
                        candidate["tripadvisor_id"])
            results.append({**hotel, **candidate})
        else:
            stats["not_found"] += 1
            logger.info("  → No TripAdvisor result found")
            results.append(hotel)

        stats["searched"] += 1

        if stats["searched"] % 25 == 0:
            _save_json(results, args.output)

        if i < len(hotels) - 1:
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    _save_json(results, args.output)
    _print_stats(stats)


def _save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _print_stats(stats):
    logger.info("Results: %s", stats)
    print(f"\n  Searched:    {stats['searched']}")
    print(f"  Found:       {stats['found']}")
    print(f"  Not found:   {stats['not_found']}")
    print(f"  Errors:      {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(description="Search TripAdvisor for unmatched hotels")
    parser.add_argument("--input", help="JSON file of unmatched hotels (offline mode)")
    parser.add_argument("--output", default="data/tripadvisor/ta_search_results.json",
                        help="Output JSON (offline mode)")
    parser.add_argument("--limit", type=int, default=0, help="Max hotels (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.input:
        _run_from_json(args)
    else:
        _run_from_db(args)


if __name__ == "__main__":
    main()
