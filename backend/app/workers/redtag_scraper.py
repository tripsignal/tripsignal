"""RedTag.ca vacation package extractor — compliant listing-page approach.

Fetches public marketing pages at www.redtag.ca/deals/{city}/ and extracts
structured deal data from data-deal JSON attributes on Continue buttons.

Only targets pages allowed by robots.txt.

Usage:
  python -m app.workers.redtag_scraper --dry-run --once   # test one city, print results
  python -m app.workers.redtag_scraper --once              # full cycle with DB, then exit
  python -m app.workers.redtag_scraper                     # continuous daemon (via orchestrator)
"""
import html as html_module
import json
import logging
import os
import random
import re
import signal as _signal
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from curl_cffi.requests import Session as CffiSession
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.session import get_db
from app.workers.shared.regions import map_destination_to_region
from app.workers.shared.matching import match_deal_to_signals
from app.workers.shared.upsert import upsert_deal
from app.workers.shared.browser_profiles import (
    check_ua_staleness,
    pick_cycle_profile,
    build_request_headers,
    human_delay,
)

logger = logging.getLogger("redtag_extractor")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.redtag.ca"
_TIMEOUT = 30
_SYSTEM_API_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}

# City slugs with /deals/{slug}/ listing pages on redtag.ca
# Verified against RedTag sitemap — only cities with real deal inventory included.
# Note: sydney=YQY (Cape Breton) and timmins=YTS are RedTag-only cities not in SellOff.
REDTAG_DEAL_CITIES = {
    "toronto": "YYZ",
    "montreal": "YUL",
    "calgary": "YYC",
    "vancouver": "YVR",
    "edmonton": "YEG",
    "winnipeg": "YWG",
    "ottawa": "YOW",
    "st-johns": "YYT",
    "saskatoon": "YXE",
    "regina": "YQR",
    "halifax": "YHZ",
    "quebec-city": "YQB",
    "fredericton": "YFC",
    "moncton": "YQM",
    "victoria": "YYJ",
    "kelowna": "YLW",
    "abbotsford": "YXX",
    "grande-prairie": "YQU",
    "nanaimo": "YCD",
    "comox": "YQQ",
    "hamilton": "YHM",
    "london": "YXU",
    "kitchener": "YKF",
    "deer-lake": "YDF",
    "fort-mcmurray": "YMM",
    "prince-george": "YXS",
    "sydney": "YQY",
    "gander": "YQX",
    "kamloops": "YKA",
    "thunder-bay": "YQT",
    "charlottetown": "YYG",
    "sault-ste-marie": "YAM",
    "saint-john": "YSJ",
    "timmins": "YTS",
    "windsor": "YQG",
}

# Regex to extract data-deal JSON from Continue buttons
_DATA_DEAL_RE = re.compile(r'data-deal="([^"]+)"')

# Block detection: HTTP codes and page body markers
_BLOCK_STATUS_CODES = {403, 429, 503}
_BLOCK_MARKERS = [
    "captcha", "are you a robot", "access denied", "rate limit",
    "too many requests", "blocked", "unusual traffic",
]

# Rate limiting
_MAX_PAGES_PER_RUN = 40

# Staleness threshold (RedTag runs ~1x/day, so higher threshold)
DEACTIVATION_THRESHOLD = 5

# Graceful shutdown
_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown signal received, will finish current cycle")


_signal.signal(_signal.SIGTERM, _handle_signal)
_signal.signal(_signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

def _build_proxy_url() -> Optional[str]:
    """Build proxy URL from env vars. Returns None if not configured."""
    proxy_enabled = os.getenv("PROXY_ENABLED", "false").lower() == "true"
    proxy_user = os.getenv("PROXY_USER", "")
    if not proxy_enabled or not proxy_user:
        return None
    proxy_host = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
    proxy_port = os.getenv("PROXY_PORT", "823")
    proxy_pass = os.getenv("PROXY_PASS", "")
    proxy_country = os.getenv("PROXY_COUNTRY", "cr.ca")
    logger.info("Proxy enabled: %s:%s (country=%s)", proxy_host, proxy_port, proxy_country)
    return f"http://{proxy_user}__{proxy_country}:{proxy_pass}@{proxy_host}:{proxy_port}"


def _create_session(profile: dict, proxy_url: Optional[str] = None) -> CffiSession:
    """Create a curl_cffi session with browser impersonation and optional proxy."""
    session = CffiSession(impersonate=profile["impersonate"])
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


# ---------------------------------------------------------------------------
# Block detection (simple circuit breaker)
# ---------------------------------------------------------------------------

def _is_blocked(status_code: int, body: str = "") -> bool:
    if status_code in _BLOCK_STATUS_CODES:
        return True
    body_lower = body.lower()
    return any(marker in body_lower for marker in _BLOCK_MARKERS)


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------

def fetch_listing_page(city: str, session: Optional[CffiSession] = None, profile: Optional[dict] = None) -> Optional[str]:
    """Fetch a RedTag deals listing page. Returns HTML string or None."""
    url = f"{BASE_URL}/deals/{city}/"
    headers = build_request_headers(profile or {})

    try:
        if session:
            response = session.get(url, headers=headers, timeout=_TIMEOUT)
        else:
            response = CffiSession().get(url, headers=headers, timeout=_TIMEOUT)
    except Exception as e:
        err_str = str(e)
        # Check for block-like status codes in the error
        if any(str(code) in err_str for code in _BLOCK_STATUS_CODES):
            logger.error("BLOCKED: %s fetching %s — stopping", e, url)
            return None
        logger.error("Failed to fetch %s: %s", url, e)
        return None

    if _is_blocked(response.status_code, response.text):
        logger.error("BLOCKED: Block markers detected in response from %s — stopping", url)
        return None

    return response.text


# ---------------------------------------------------------------------------
# Deal parsing from data-deal JSON
# ---------------------------------------------------------------------------

def parse_date(date_str: str) -> Optional[date]:
    """Parse dates in RedTag formats: 20260401, 2026-04-01, Apr 14, 2026."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None



def parse_deals_from_html(html_str: str, city: str) -> list[dict]:
    """Extract deal metadata dicts from data-deal JSON attributes in listing HTML.

    Only returns All Inclusive (MealType == "AI") packages.
    Validates that each deal's DepartureCode matches the expected gateway for this city
    to catch RedTag fallback pages that silently return deals from a different city.
    Deduplicates by dedupe_key.
    """
    raw_matches = _DATA_DEAL_RE.findall(html_str)
    if not raw_matches:
        return []

    expected_gateway = REDTAG_DEAL_CITIES.get(city, "")
    deals = []
    seen_keys = set()
    skipped_count = 0
    total_count = len(raw_matches)
    last_actual_departure = ""

    for encoded_json in raw_matches:
        try:
            deal = json.loads(html_module.unescape(encoded_json))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse data-deal JSON: %s", e)
            continue

        # Validate departure code matches the expected city gateway.
        # RedTag sometimes returns another city's deals (e.g. fallback to Toronto)
        # when a city page has no real inventory.
        if expected_gateway:
            actual_departure_raw = deal.get("DepartureCode", "")
            actual_departure = actual_departure_raw.split(",")[0].strip() if actual_departure_raw else ""
            if actual_departure and actual_departure != expected_gateway:
                logger.warning(
                    "Departure code mismatch for %s: expected %s, got %s — skipping deal (hotel=%s)",
                    city, expected_gateway, actual_departure,
                    deal.get("HotelName", "unknown"),
                )
                skipped_count += 1
                last_actual_departure = actual_departure
                continue

        deal_meta = _parse_single_deal(deal, city)
        if not deal_meta:
            continue

        if deal_meta["dedupe_key"] in seen_keys:
            continue
        seen_keys.add(deal_meta["dedupe_key"])
        deals.append(deal_meta)

    if skipped_count > 0:
        logger.warning(
            "%s: skipped %d/%d deals — departure code mismatch (got %s, expected %s)",
            city, skipped_count, total_count, last_actual_departure, expected_gateway,
        )

    return deals


def _parse_single_deal(deal: dict, city: str) -> Optional[dict]:
    """Parse a single data-deal JSON blob into a deal metadata dict."""
    # Only All Inclusive
    if deal.get("MealType") != "AI":
        return None

    hotel_name = deal.get("HotelName", "").strip()
    if not hotel_name:
        return None

    hotel_id = deal.get("HotelID")
    if hotel_id is None:
        return None

    price_raw = deal.get("TotalPrice")
    if price_raw is None:
        return None
    try:
        price_cents = int(float(str(price_raw)) * 100)
    except (ValueError, TypeError):
        return None
    if price_cents <= 0:
        return None

    depart_date = parse_date(deal.get("DepartureDate", ""))
    if not depart_date:
        return None

    try:
        duration_days = int(deal.get("Duration", "7"))
    except (ValueError, TypeError):
        duration_days = 7

    return_date = depart_date + timedelta(days=duration_days)

    # Gateway normalization: RedTag uses composite codes like "YYZ,YTZ"
    # We take the first code for signal matching
    gateway_raw = deal.get("DepartureCode", REDTAG_DEAL_CITIES.get(city, ""))
    gateway = gateway_raw.split(",")[0].strip() if gateway_raw else ""

    destination_str = deal.get("Destination", "")
    region = map_destination_to_region(destination_str) if destination_str else None

    star_raw = deal.get("Star")
    star_rating = None
    if star_raw is not None:
        try:
            star_rating = float(star_raw)
        except (ValueError, TypeError):
            pass

    dedupe_key = f"redtag:{gateway}:{hotel_id}:{depart_date}:{duration_days}"

    return {
        "gateway": gateway,
        "hotel_name": hotel_name,
        "hotel_id": str(hotel_id),
        "price_cents": price_cents,
        "depart_date": depart_date,
        "return_date": return_date,
        "duration_days": duration_days,
        "destination_str": destination_str,
        "region": region,
        "star_rating": star_rating,
        "deeplink_url": f"{BASE_URL}/deals/{city}/",
        "dedupe_key": dedupe_key,
    }


# ---------------------------------------------------------------------------
# Core scrape cycle
# ---------------------------------------------------------------------------

def run_once(dry_run: bool = False) -> dict:
    """Run a single RedTag scrape cycle.

    Returns a summary dict with cycle statistics. This function is called
    by the orchestrator or directly via CLI.
    """
    mode_label = "DRY-RUN" if dry_run else "LIVE"
    logger.info("RedTag scraper cycle starting [%s]", mode_label)

    cycle_errors: list = []
    total_deals = 0
    total_matches = 0
    deals_deactivated = 0
    seen_dedupe_keys: set[str] = set()
    started_at = datetime.now(timezone.utc)
    blocked = False

    # Accumulator for match alerts (returned to orchestrator)
    v2_signal_deals: dict = defaultdict(list)

    # Browser profile + curl_cffi session for this cycle
    check_ua_staleness()
    cycle_profile = pick_cycle_profile()
    proxy_url = _build_proxy_url()
    session = _create_session(cycle_profile, proxy_url)
    logger.info("Cycle browser profile: %s (%s)", cycle_profile["impersonate"], cycle_profile["platform"])

    # Shuffle city order each cycle for unpredictable access pattern
    city_items = list(REDTAG_DEAL_CITIES.items())
    random.shuffle(city_items)

    pages_fetched = 0
    for city, default_gateway in city_items:
        if _shutdown_requested or blocked:
            break
        if pages_fetched >= _MAX_PAGES_PER_RUN:
            logger.info("Reached max pages per run (%d), stopping", _MAX_PAGES_PER_RUN)
            break

        logger.info("Fetching %s deals page", city)
        html = fetch_listing_page(city, session=session, profile=cycle_profile)
        pages_fetched += 1

        if html is None:
            # Possible block — stop all fetching
            blocked = True
            cycle_errors.append({"city": city, "error": "Fetch failed or blocked", "type": "block"})
            break

        deals = parse_deals_from_html(html, city)
        logger.info("Parsed %d AI deals from %s", len(deals), city)

        if not deals:
            continue

        if dry_run:
            for deal_meta in deals:
                total_deals += 1
                logger.info(
                    "[DRY-RUN] #%d %s | %s→%s | %s | %d nights | $%d/pp",
                    total_deals, deal_meta["hotel_name"], deal_meta["gateway"],
                    deal_meta["destination_str"], deal_meta["depart_date"],
                    deal_meta["duration_days"], deal_meta["price_cents"] // 100,
                )
        else:
            with next(get_db()) as db:
                for deal_meta in deals:
                    try:
                        deal_obj = upsert_deal(db, "redtag", deal_meta)
                        if not deal_obj:
                            continue

                        seen_dedupe_keys.add(deal_obj.dedupe_key)
                        total_deals += 1

                        matched_signals = match_deal_to_signals(db, deal_obj, deal_meta)
                        for signal in matched_signals:
                            existing = db.execute(
                                select(DealMatch).where(
                                    DealMatch.signal_id == signal.id,
                                    DealMatch.deal_id == deal_obj.id,
                                )
                            ).scalar_one_or_none()

                            if existing:
                                continue

                            duration_days = deal_meta.get("duration_days", 7)
                            ppn = deal_obj.price_cents // duration_days if duration_days > 0 else None
                            match = DealMatch(
                                signal_id=signal.id,
                                deal_id=deal_obj.id,
                                price_per_night_cents=ppn,
                            )
                            db.add(match)
                            db.commit()
                            total_matches += 1
                            logger.info(
                                "Match: %s -> %s %s $%d",
                                signal.name, deal_obj.destination,
                                deal_obj.depart_date, deal_obj.price_cents // 100,
                            )

                            # Accumulate for match alerts
                            sig_key = str(signal.id)
                            v2_signal_deals[sig_key].append({
                                "deal_id": str(deal_obj.id),
                                "price_cents": deal_obj.price_cents,
                                "price_dropped": getattr(deal_obj, "_price_dropped", False),
                                "price_delta": getattr(deal_obj, "_price_delta", 0),
                                "hotel_name": deal_obj.hotel_name or "",
                                "hotel_id": deal_obj.hotel_id or "",
                                "star_rating": deal_obj.star_rating,
                                "depart_date": deal_obj.depart_date,
                                "return_date": deal_obj.return_date,
                                "duration_nights": duration_days,
                                "destination": deal_obj.destination or "",
                                "destination_str": deal_obj.destination_str or deal_obj.destination or "",
                                "origin": deal_obj.origin or "",
                                "deeplink_url": deal_obj.deeplink_url or "",
                                "provider": "redtag",
                            })

                    except Exception as e:
                        logger.error("Error processing deal: %s", e)
                        db.rollback()
                        cycle_errors.append({"city": city, "error": str(e), "type": "error"})
                        continue

        # Human-like delay between pages
        if pages_fetched < len(REDTAG_DEAL_CITIES):
            delay = human_delay()
            logger.debug("Sleeping %.1fs before next page", delay)
            time.sleep(delay)

    # Graduated staleness for RedTag deals only
    if not dry_run and seen_dedupe_keys and not blocked:
        try:
            with next(get_db()) as db:
                unseen = db.query(Deal).filter(
                    Deal.is_active,
                    Deal.provider == "redtag",
                    Deal.dedupe_key.notin_(seen_dedupe_keys),
                ).all()
                deactivated_now = datetime.now(timezone.utc)
                newly_deactivated = 0
                for deal in unseen:
                    deal.missed_cycles = (deal.missed_cycles or 0) + 1
                    if deal.missed_cycles >= DEACTIVATION_THRESHOLD:
                        deal.is_active = False
                        deal.deactivated_at = deactivated_now
                        newly_deactivated += 1
                db.commit()
                deals_deactivated = newly_deactivated
                if unseen:
                    logger.info(
                        "Staleness: %d unseen (%d incremented, %d deactivated after %d+ misses)",
                        len(unseen), len(unseen) - newly_deactivated,
                        newly_deactivated, DEACTIVATION_THRESHOLD,
                    )
        except Exception as e:
            logger.error("Stale deal deactivation failed: %s", e)
            cycle_errors.append({"error": str(e), "type": "stale_deactivation"})

    # Mark expired RedTag deals inactive
    if not dry_run:
        try:
            with next(get_db()) as db:
                expired = db.query(Deal).filter(
                    Deal.is_active,
                    Deal.provider == "redtag",
                    Deal.depart_date < date.today(),
                ).all()
                if expired:
                    deactivated_now = datetime.now(timezone.utc)
                    for deal in expired:
                        deal.is_active = False
                        deal.deactivated_at = deactivated_now
                    db.commit()
                    logger.info("Marked %d expired RedTag deals inactive", len(expired))
        except Exception as e:
            logger.error("Expired deal cleanup failed: %s", e)
            cycle_errors.append({"error": str(e), "type": "expired_cleanup"})

    # Clean up session
    try:
        session.close()
    except Exception:
        pass

    completed_at = datetime.now(timezone.utc)
    elapsed = (completed_at - started_at).total_seconds()
    logger.info(
        "RedTag cycle complete. Deals: %d, Matches: %d, Pages: %d, Errors: %d, Elapsed: %.0fs",
        total_deals, total_matches, pages_fetched, len(cycle_errors), elapsed,
    )

    return {
        "provider": "redtag",
        "started_at": started_at,
        "completed_at": completed_at,
        "total_deals": total_deals,
        "total_matches": total_matches,
        "pages_fetched": pages_fetched,
        "deals_deactivated": deals_deactivated,
        "error_count": len(cycle_errors),
        "errors": cycle_errors,
        "blocked": blocked,
        "v2_signal_deals": dict(v2_signal_deals),
    }


# ---------------------------------------------------------------------------
# Standalone daemon mode (used when not running via orchestrator)
# ---------------------------------------------------------------------------

def run_scraper(once: bool = True, dry_run: bool = False) -> None:
    """Run the RedTag scraper as a standalone process."""
    # Check feature flag
    if not dry_run:
        try:
            with next(get_db()) as db:
                from sqlalchemy import text
                row = db.execute(
                    text("SELECT value FROM system_config WHERE key = 'redtag_scraper_enabled'")
                ).scalar_one_or_none()
                if row and row.lower() != "true":
                    logger.info("RedTag scraper is disabled via system_config, exiting")
                    return
        except Exception:
            pass  # If table doesn't exist yet, proceed

    while True:
        try:
            result = run_once(dry_run=dry_run)

            # In standalone mode, send alerts directly
            if not dry_run and result.get("v2_signal_deals"):
                try:
                    from app.workers.selloff_scraper import _send_cycle_alerts
                    _send_cycle_alerts(result["v2_signal_deals"], defaultdict(dict))
                except Exception as e:
                    logger.error("Match alert sending failed: %s", e)

            # Report to API
            if not dry_run:
                try:
                    import requests as _req
                    _req.post("http://api:8000/api/system/collection-complete", json={
                        "started_at": result["started_at"].isoformat(),
                        "completed_at": result["completed_at"].isoformat(),
                        "total_deals": result["total_deals"],
                        "total_matches": result["total_matches"],
                        "error_count": result["error_count"],
                        "errors": result["errors"][:50],
                        "deals_deactivated": result["deals_deactivated"],
                        "provider": "redtag",
                        "status": "completed",
                    }, headers=_SYSTEM_API_HEADERS, timeout=5)
                except Exception as e:
                    logger.warning("Failed to post collection-complete: %s", e)

        except Exception as e:
            logger.error("SCRAPE CYCLE CRASHED: %s\n%s", e, traceback.format_exc())

        if once or _shutdown_requested:
            if _shutdown_requested:
                logger.info("Shutting down gracefully after completed cycle")
            return

        # Sleep ~24h with jitter
        jitter = random.randint(-3600, 3600)
        sleep_seconds = 24 * 60 * 60 + jitter
        logger.info("Sleeping %.1f hours before next cycle", sleep_seconds / 3600)
        time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RedTag.ca vacation package scraper")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print results without DB writes")
    args = parser.parse_args()

    run_scraper(once=args.once or args.dry_run, dry_run=args.dry_run)
