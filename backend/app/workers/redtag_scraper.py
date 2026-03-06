"""RedTag.ca vacation package extractor — compliant listing-page approach.

Fetches public marketing pages at www.redtag.ca/deals/{city}/ and extracts
structured deal data from data-deal JSON attributes on Continue buttons.

Only targets pages allowed by robots.txt. No session-based APIs, no browser
spoofing, no booking engine access.

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
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.session import get_db
from app.workers.shared.regions import map_destination_to_region
from app.workers.shared.matching import match_deal_to_signals
from app.workers.shared.upsert import upsert_deal

logger = logging.getLogger("redtag_extractor")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = "TripSignal-IngestionLab/0.1 (contact: hello@tripsignal.com)"
BASE_URL = "https://www.redtag.ca"
_TIMEOUT = 30
_SYSTEM_API_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}

# City slugs with /deals/{slug}/ listing pages on redtag.ca
REDTAG_DEAL_CITIES = {
    "toronto": "YYZ",
    "montreal": "YUL",
    "calgary": "YYC",
    "vancouver": "YVR",
    "edmonton": "YEG",
    "winnipeg": "YWG",
}

# Regex to extract data-deal JSON from Continue buttons
_DATA_DEAL_RE = re.compile(r'data-deal="([^"]+)"')
# Regex to extract hotel detail page URLs
_HOTEL_URL_RE = re.compile(r'href="(/hotel-resorts/[^"]+/)"')

# Block detection: HTTP codes and page body markers
_BLOCK_STATUS_CODES = {403, 429, 503}
_BLOCK_MARKERS = [
    "captcha", "are you a robot", "access denied", "rate limit",
    "too many requests", "blocked", "unusual traffic",
]

# Rate limiting
_DELAY_MIN = 10.0
_DELAY_MAX = 25.0
_MAX_PAGES_PER_RUN = 10

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

def _build_proxy_opener() -> Optional[urllib.request.OpenerDirector]:
    """Build a urllib opener routed through DataImpulse proxy, or None if disabled."""
    proxy_enabled = os.getenv("PROXY_ENABLED", "false").lower() == "true"
    proxy_user = os.getenv("PROXY_USER", "")
    if not proxy_enabled or not proxy_user:
        return None
    proxy_host = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
    proxy_port = os.getenv("PROXY_PORT", "823")
    proxy_pass = os.getenv("PROXY_PASS", "")
    proxy_country = os.getenv("PROXY_COUNTRY", "cr.ca")
    proxy_url = f"http://{proxy_user}__{proxy_country}:{proxy_pass}@{proxy_host}:{proxy_port}"
    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    logger.info("Proxy enabled: %s:%s (country=%s)", proxy_host, proxy_port, proxy_country)
    return urllib.request.build_opener(proxy_handler)


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

def fetch_listing_page(city: str, opener: Optional[urllib.request.OpenerDirector] = None) -> Optional[str]:
    """Fetch a RedTag deals listing page. Returns HTML string or None."""
    url = f"{BASE_URL}/deals/{city}/"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
        },
    )

    try:
        if opener:
            response = opener.open(req, timeout=_TIMEOUT)
        else:
            response = urllib.request.urlopen(req, timeout=_TIMEOUT)
    except urllib.error.HTTPError as e:
        if _is_blocked(e.code):
            logger.error("BLOCKED: HTTP %d fetching %s — stopping", e.code, url)
            return None
        logger.error("HTTP %d fetching %s", e.code, url)
        return None
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None

    body = response.read().decode("utf-8", "ignore")

    if _is_blocked(response.status, body):
        logger.error("BLOCKED: Block markers detected in response from %s — stopping", url)
        return None

    return body


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


def _build_hotel_url_map(html_str: str) -> dict[str, str]:
    """Build a map from hotel detail URL slug to full path.

    Each deal card has an <a href="/hotel-resorts/{country}/{city}/{slug}/">
    link near the hotel name, followed by a <button data-deal="..."> further
    down. We collect all hotel-resorts URLs and map by slug for lookup.
    Since slugs aren't in the data-deal JSON, we also build a positional
    map: for each data-deal button position, find the nearest preceding
    hotel-resorts URL.
    """
    hotel_urls: list[tuple[int, str]] = []
    for m in _HOTEL_URL_RE.finditer(html_str):
        hotel_urls.append((m.start(), m.group(1)))

    deal_positions: list[tuple[int, str]] = []
    for m in _DATA_DEAL_RE.finditer(html_str):
        deal_positions.append((m.start(), m.group(1)))

    # For each data-deal position, find the nearest preceding hotel URL
    position_map: dict[str, str] = {}  # encoded_json -> hotel_url
    for deal_pos, encoded_json in deal_positions:
        best_url = None
        for url_pos, url_path in hotel_urls:
            if url_pos < deal_pos:
                best_url = url_path
            else:
                break
        if best_url:
            position_map[encoded_json] = best_url

    return position_map


def parse_deals_from_html(html_str: str, city: str) -> list[dict]:
    """Extract deal metadata dicts from data-deal JSON attributes in listing HTML.

    Only returns All Inclusive (MealType == "AI") packages.
    Deduplicates by dedupe_key.
    Pairs each deal with its hotel detail page URL from the same card.
    """
    raw_matches = _DATA_DEAL_RE.findall(html_str)
    if not raw_matches:
        return []

    hotel_url_map = _build_hotel_url_map(html_str)

    deals = []
    seen_keys = set()

    for encoded_json in raw_matches:
        try:
            deal = json.loads(html_module.unescape(encoded_json))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse data-deal JSON: %s", e)
            continue

        hotel_path = hotel_url_map.get(encoded_json)
        deal_meta = _parse_single_deal(deal, city, hotel_path)
        if not deal_meta:
            continue

        if deal_meta["dedupe_key"] in seen_keys:
            continue
        seen_keys.add(deal_meta["dedupe_key"])
        deals.append(deal_meta)

    return deals


def _parse_single_deal(deal: dict, city: str, hotel_path: Optional[str] = None) -> Optional[dict]:
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
        "deeplink_url": f"{BASE_URL}{hotel_path}" if hotel_path else f"{BASE_URL}/deals/{city}/",
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

    opener = _build_proxy_opener()

    pages_fetched = 0
    for city, default_gateway in REDTAG_DEAL_CITIES.items():
        if _shutdown_requested or blocked:
            break
        if pages_fetched >= _MAX_PAGES_PER_RUN:
            logger.info("Reached max pages per run (%d), stopping", _MAX_PAGES_PER_RUN)
            break

        logger.info("Fetching %s deals page", city)
        html = fetch_listing_page(city, opener)
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

        # Polite delay between pages
        if pages_fetched < len(REDTAG_DEAL_CITIES):
            delay = random.uniform(_DELAY_MIN, _DELAY_MAX)
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
