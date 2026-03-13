"""SellOff Vacations scraper and signal matcher."""
import json
import logging
import os
import random
import re
import signal as _signal
import time
import traceback
import ipaddress
import socket
from collections import defaultdict
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from curl_cffi.requests import Session as CffiSession
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db
from app.workers.shared.browser_profiles import (
    check_ua_staleness,
    pick_cycle_profile,
    build_request_headers,
    human_delay,
    category_pause,
    select_cycle_destinations,
    select_cycle_gateways,
    SELLOFF_NAV_PAGES,
    SELLOFF_WARMUP_PAGES,
)

logger = logging.getLogger("selloff_scraper")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
NEXT_SCAN_FILE = "/tmp/next_scan.json"
_SYSTEM_API_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}
MAX_CYCLE_SECONDS = int(os.getenv("MAX_CYCLE_SECONDS", "18000"))  # 5 hours default

# Postgres advisory lock key — prevents concurrent scrape cycles
_SCRAPE_ADVISORY_LOCK_KEY = 8675309  # arbitrary unique integer

# Graceful shutdown — finish current cycle before exiting
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("SIGTERM received — will finish current cycle then exit")


# Only register signal handlers when running as the main process.
# When imported by the orchestrator, it manages shutdown via the module flag directly.
if __name__ == "__main__":
    _signal.signal(_signal.SIGTERM, _handle_sigterm)
    _signal.signal(_signal.SIGINT, _handle_sigterm)


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 1-second increments so SIGTERM is respected promptly."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _shutdown_requested:
        time.sleep(min(1.0, end - time.monotonic()))

# Proxy configuration (DataImpulse residential proxy)
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1", "yes")
PROXY_HOST = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
PROXY_PORT = os.getenv("PROXY_PORT", "823")
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_COUNTRY = os.getenv("PROXY_COUNTRY", "cr.ca")

# curl_cffi session — one per IP rotation segment, set in _run_scraper_inner
_cycle_session: Optional[CffiSession] = None

# Set to True when last fetch was a 404 — skips rate-limit sleep in the scrape loop
_last_fetch_was_404: bool = False

# Referer chain: tracks the last successfully fetched URL
_last_page_url: Optional[str] = None

# Browser profile for this cycle (set at cycle start)
_cycle_profile: Optional[dict] = None

# IP rotation: rotate proxy IP every N pages to avoid single-IP detection
_pages_on_current_ip: int = 0
_ip_rotation_threshold: int = 0


def _build_proxy_url() -> Optional[str]:
    """Build proxy URL from env vars. Returns None if not configured."""
    if not PROXY_ENABLED or not PROXY_USER:
        return None
    return f"http://{PROXY_USER}__{PROXY_COUNTRY}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"


def _create_session(proxy_url: Optional[str] = None) -> CffiSession:
    """Create a new curl_cffi session with the cycle's browser profile.

    Each session gets a fresh cookie jar and, if proxied, a new IP from
    DataImpulse's rotating residential pool.
    """
    profile = _cycle_profile or pick_cycle_profile()
    session = CffiSession(impersonate=profile["impersonate"])
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def _maybe_rotate_ip(proxy_url: Optional[str]) -> None:
    """Rotate proxy IP by creating a fresh session after N pages.

    DataImpulse residential proxies assign a new IP per connection by default.
    Creating a new session forces a new connection = new IP. Costs nothing extra
    (DataImpulse charges per GB, not per IP).
    """
    global _cycle_session, _pages_on_current_ip, _ip_rotation_threshold
    _pages_on_current_ip += 1
    if _pages_on_current_ip >= _ip_rotation_threshold:
        old_session = _cycle_session
        _cycle_session = _create_session(proxy_url)
        if old_session:
            try:
                old_session.close()
            except Exception:
                pass
        _ip_rotation_threshold = random.randint(30, 80)
        _pages_on_current_ip = 0
        logger.debug("Rotated proxy IP (new session, next rotation in %d pages)", _ip_rotation_threshold)


DESTINATION_SLUGS = [
    # Mexico (12 sub-destinations)
    "mexico/cancun",
    "mexico/riviera-maya",
    "mexico/puerto-vallarta",
    "mexico/los-cabos",
    "mexico/mazatlan",
    "mexico/huatulco",
    "mexico/ixtapa-zihuatanejo",
    "mexico/cozumel",
    "mexico/playa-mujeres",
    "mexico/riviera-nayarit",
    "mexico/tulum",
    "mexico/isla-holbox",
    # Dominican Republic (7 sub-destinations)
    "dominican-republic/punta-cana",
    "dominican-republic/puerto-plata",
    "dominican-republic/la-romana",
    "dominican-republic/samana",
    "dominican-republic/santo-domingo",
    "dominican-republic/cabarete",
    "dominican-republic/sosua",
    # Jamaica (3 sub-destinations)
    "jamaica/montego-bay",
    "jamaica/negril",
    "jamaica/ocho-rios",
    # Cuba (varadero page returns all Cuban destinations)
    "cuba/varadero",
    # Caribbean & Central America (10 standalone)
    "costa-rica",
    "aruba",
    "barbados",
    "saint-lucia",
    "antigua",
    "panama",
    "grenada",
    "cayman-islands",
    "st-maarten",
    "bermuda",
    # Honduras
    "honduras/roatan",
]

GATEWAY_SLUGS = {
    "YXX": "abbotsford",
    "YVR": "vancouver",
    "YYJ": "victoria",
    "YLW": "kelowna",
    "YKA": "kamloops",
    "YXS": "prince-george",
    "YYC": "calgary",
    "YEG": "edmonton",
    "YMM": "fort-mcmurray",
    "YQU": "grande-prairie",
    "YQL": "lethbridge",
    "YQR": "regina",
    "YXE": "saskatoon",
    "YWG": "winnipeg",
    "YYZ": "toronto",
    "YHM": "hamilton",
    "YKF": "kitchener",
    "YXU": "london",
    "YQT": "thunder-bay",
    "YOW": "ottawa",
    "YQG": "windsor",
    "YUL": "montreal",
    "YQB": "quebec-city",
    "YBG": "bagotville",
    "YHZ": "halifax",
    "YDF": "deer-lake",
    "YQX": "gander",
    "YYT": "st-johns",
    "YQM": "moncton",
    "YFC": "fredericton",
    "YSJ": "saint-john",
    "YYG": "charlottetown",
    "YSB": "sudbury",
    "YAM": "sault-ste-marie",
}

# Readable city names for email display
AIRPORT_CITY_MAP = {
    "YXX": "Abbotsford", "YVR": "Vancouver", "YYJ": "Victoria",
    "YLW": "Kelowna", "YKA": "Kamloops", "YXS": "Prince George",
    "YYC": "Calgary", "YEG": "Edmonton", "YMM": "Fort McMurray",
    "YQU": "Grande Prairie", "YQL": "Lethbridge", "YQR": "Regina",
    "YXE": "Saskatoon", "YWG": "Winnipeg", "YYZ": "Toronto",
    "YHM": "Hamilton", "YKF": "Kitchener", "YXU": "London",
    "YQT": "Thunder Bay", "YOW": "Ottawa", "YQG": "Windsor",
    "YUL": "Montreal", "YQB": "Quebec City", "YBG": "Bagotville",
    "YHZ": "Halifax", "YDF": "Deer Lake", "YQX": "Gander",
    "YYT": "St. John's", "YQM": "Moncton", "YFC": "Fredericton",
    "YSJ": "Saint John", "YYG": "Charlottetown", "YSB": "Sudbury",
    "YAM": "Sault Ste. Marie",
}

from app.workers.shared.regions import (
    DESTINATION_REGION_MAP,
    PARENT_REGION_MAP,
    deal_matches_signal_region,
    map_destination_to_region,
)
from app.workers.shared.matching import match_deal_to_signals as _shared_match_deal_to_signals, load_active_signals
from app.workers.shared.upsert import upsert_deal as _shared_upsert_deal
from app.services.market_intel import score_deal_for_match


def parse_duration_days(duration_str: str) -> int:
    match = re.search(r"(\d+)", duration_str)
    return int(match.group(1)) if match else 7


def parse_date(date_str: str) -> Optional[date]:
    date_str = date_str.strip()
    for fmt in ("%b %d, %Y", "%Y%m%d", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def clean_url(url: str) -> str:
    return url.replace("&amp;", "&")


_SCRAPER_ALLOWED_DOMAINS = {"www.selloffvacations.com", "selloffvacations.com"}


def _assert_safe_url(url: str) -> None:
    """Raise ValueError if url is not an allowed domain or resolves to a private IP."""
    parsed = urlparse(url)
    if parsed.hostname not in _SCRAPER_ALLOWED_DOMAINS:
        raise ValueError(f"Blocked: domain '{parsed.hostname}' not in scraper allowlist")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked: scheme '{parsed.scheme}' not allowed")
    try:
        for addr_info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(addr_info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"Blocked: '{parsed.hostname}' resolves to private IP {addr_info[4][0]}")
    except (socket.gaierror, ValueError):
        raise


def fetch_deals_from_page(url: str) -> list[dict]:
    global _last_fetch_was_404, _last_page_url
    _last_fetch_was_404 = False
    try:
        _assert_safe_url(url)
    except ValueError as e:
        logger.warning("fetch_deals_from_page blocked: %s", e)
        return []
    try:
        headers = build_request_headers(_cycle_profile or {}, referer=_last_page_url)
        resp = _cycle_session.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            logger.debug("404 (no flights) for %s — skipping", url)
            _last_fetch_was_404 = True
            return []
        resp.raise_for_status()
        html = resp.text
        _last_page_url = url
    except Exception as e:
        err_str = str(e)
        if "404" in err_str:
            logger.debug("404 (no flights) for %s — skipping", url)
            _last_fetch_was_404 = True
        else:
            logger.warning("Failed to fetch %s: %s", url, e)
        return []

    destinations = re.findall(r'adModuleHeading--\w+\">([^<]+)</h2>', html)
    hotels = re.findall(r'adModuleSubheading--\w+\">([^<]+)</p>', html)
    dates = re.findall(r'adModuleDetailsDays--\w+\"><span>([^<]+)</span>', html)
    prices = re.findall(r'adModuleDetailsAmount--\w+\">[$](\d+)<', html)
    discounts = re.findall(r'Save up to (\d+)%', html)
    links = re.findall(r'href=\"(https://shopping\.selloffvacations\.com/cgi-bin/handler\.cgi\?[^\"]+)\"', html)
    star_ratings = re.findall(r'StarRating-module--rating--\w+\" rating=\"([\d.]+)\"', html)

    deals = []
    for i in range(len(prices)):
        try:
            link = links[i] if i < len(links) else ""
            clean_link = clean_url(link)

            gateway_match = re.search(r'gateway_dep=([A-Z]+)', clean_link)
            hotel_match = re.search(r'no_hotel=(\d+)', clean_link)
            date_param_match = re.search(r'date_dep=(\d+)', clean_link)
            duration_param_match = re.search(r'duration=([A-Z0-9]+)', clean_link)

            gateway = gateway_match.group(1) if gateway_match else ""
            hotel_id = hotel_match.group(1) if hotel_match else str(i)
            depart_date_str = date_param_match.group(1) if date_param_match else (dates[i] if i < len(dates) else "")
            duration_str = duration_param_match.group(1) if duration_param_match else "7DAYS"

            depart_date = parse_date(depart_date_str)
            if not depart_date:
                continue

            duration_days = parse_duration_days(duration_str)
            return_date = depart_date + timedelta(days=duration_days)
            price_cents = int(prices[i]) * 100
            destination_str = destinations[i].strip() if i < len(destinations) else ""
            hotel_name = hotels[i].replace("&amp;", "&").strip() if i < len(hotels) else ""
            star_rating = float(star_ratings[i]) if i < len(star_ratings) else None
            region = map_destination_to_region(destination_str)

            if not hotel_name or star_rating is None:
                continue

            deals.append({
                "gateway": gateway,
                "destination_str": destination_str,
                "hotel_name": hotel_name,
                "region": region,
                "depart_date": depart_date,
                "return_date": return_date,
                "duration_days": duration_days,
                "price_cents": price_cents,
                "discount_pct": int(discounts[i]) if i < len(discounts) else 0,
                "deeplink_url": clean_link,
                "hotel_id": hotel_id,
                "star_rating": star_rating,
            })
        except Exception as e:
            logger.warning("Failed to parse deal %d: %s", i, e)
            continue

    return deals


def _warmup_session() -> None:
    """Hit a few top-level pages to establish a realistic browsing session and collect cookies."""
    global _last_page_url
    # Pick 1-2 random warmup pages
    selected = random.sample(SELLOFF_WARMUP_PAGES, k=random.randint(1, 2))
    for url in selected:
        try:
            headers = build_request_headers(_cycle_profile or {}, referer=_last_page_url)
            _cycle_session.get(url, headers=headers, timeout=15)
            _last_page_url = url
            logger.debug("Warmup: visited %s", url)
            time.sleep(random.uniform(3, 8))
        except Exception as e:
            logger.debug("Warmup failed for %s: %s (continuing)", url, e)


def _visit_nav_page() -> None:
    """Occasionally visit a non-deal page to make browsing pattern more realistic."""
    global _last_page_url
    url = random.choice(SELLOFF_NAV_PAGES)
    try:
        headers = build_request_headers(_cycle_profile or {}, referer=_last_page_url)
        _cycle_session.get(url, headers=headers, timeout=15)
        _last_page_url = url
        logger.debug("Nav visit: %s", url)
    except Exception as e:
        logger.debug("Nav visit failed for %s: %s (continuing)", url, e)


def upsert_deal(db: Session, deal: dict) -> Optional[Deal]:
    deal["dedupe_key"] = f"selloff:{deal['gateway']}:{deal['hotel_id']}:{deal['depart_date']}:{deal['duration_days']}"
    return _shared_upsert_deal(db, "selloff", deal)


def match_deal_to_signals(db: Session, deal: Deal, deal_meta: dict, signals=None) -> list[Signal]:
    return _shared_match_deal_to_signals(db, deal, deal_meta, signals=signals)



def validate_user_for_email(db: Session, user_email: str) -> tuple[bool, bool]:
    """Returns (can_send, is_pro). Checks plan, opt-out, and delivery frequency."""
    if not user_email:
        return False, False
    user = db.execute(
        select(User).where(User.email == user_email)
    ).scalar_one_or_none()
    if not user:
        logger.info("No user found for email %s, skipping", user_email)
        return False, False
    if user.email_opt_out:
        logger.info("User %s has opted out of emails, skipping", user_email)
        return False, False
    if user.email_suppressed:
        logger.info("User %s is suppressed (bounce/complaint), skipping", user_email)
        return False, False
    is_pro = user.plan_type == "pro"
    is_trial_active = user.plan_status == "active" and user.plan_type == "free"
    if not is_pro and not is_trial_active:
        logger.info("Skipping digest for expired/inactive user %s", user_email)
        return False, False

    return True, is_pro


def _format_date_range(dep, ret) -> str:
    """Format 'Apr 3–10' or 'Mar 28 – Apr 4'."""
    if not dep or not ret:
        return ""
    if dep.month == ret.month and dep.year == ret.year:
        return f"{dep.strftime('%b')} {dep.day}–{ret.day}"
    return f"{dep.strftime('%b')} {dep.day} – {ret.strftime('%b')} {ret.day}"


def _city_from_destination(destination_str: str) -> str:
    """Extract city name from 'City, Country' string."""
    if not destination_str:
        return ""
    return destination_str.split(",")[0].strip()


def _star_display(rating) -> str:
    """Format star rating like '★ 4.2'."""
    if rating is None:
        return ""
    return f"★ {rating:.1f}"


def _create_deal_match(
    db: Session,
    signal: Signal,
    deal: Deal,
    duration_days: int,
    price_delta_cents: int | None,
    *,
    stats_cache: dict | None = None,
) -> DealMatch:
    """Create a DealMatch, score it, persist, and return it.

    price_delta_cents uses standard delta convention:
    negative = price dropped, positive = price increased, None = unknown.
    """
    ppn = deal.price_cents // duration_days if duration_days > 0 else None
    try:
        vlabel = score_deal_for_match(db, deal, stats_cache=stats_cache or {})
    except Exception as exc:
        logger.warning("Failed to score deal %s: %s", deal.id, exc)
        vlabel = None
    match = DealMatch(
        signal_id=signal.id,
        deal_id=deal.id,
        price_per_night_cents=ppn,
        value_label=vlabel,
        price_delta_cents=price_delta_cents,
    )
    db.add(match)
    db.commit()
    return match


def _build_price_delta_map(db: Session) -> dict:
    """Query price history to find the most recent price change per deal.

    Returns {deal_id_uuid: delta_cents} where delta > 0 means a drop,
    delta < 0 means an increase, and delta == 0 means unchanged.
    Only includes deals with at least two price history entries.
    """
    rows = db.execute(text("""
        WITH recent AS (
            SELECT deal_id, price_cents,
                   LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at ASC) as prev_price,
                   ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) as rn
            FROM deal_price_history
        )
        SELECT deal_id, (prev_price - price_cents) as delta
        FROM recent
        WHERE rn = 1 AND prev_price IS NOT NULL
    """)).fetchall()
    return {row[0]: row[1] for row in rows}


def _send_cycle_alerts(
    v2_signal_deals: dict,
    user_digest: dict,
    db_override: Optional[Session] = None,
) -> None:
    """Send match alert emails for a scrape cycle.

    Creates SignalRun records and calls process_signal_matches which
    groups signals by user and sends one consolidated email per user.
    """
    if not v2_signal_deals:
        return

    from app.db.models.signal_run import SignalRun, SignalRunStatus, SignalRunType
    from app.services.match_alert import process_signal_matches

    def _process(db: Session) -> None:
        now = datetime.now(timezone.utc)
        run_map: dict[str, tuple[str, list]] = {}
        for signal_id_str, deals in v2_signal_deals.items():
            if not deals:
                continue
            # Create a SignalRun record for this signal in this cycle
            run = SignalRun(
                signal_id=signal_id_str,
                run_type=SignalRunType.morning,
                status=SignalRunStatus.success,
                started_at=now,
                completed_at=now,
                matches_created_count=len(deals),
            )
            db.add(run)
            db.flush()

            # Update DealMatch rows with run_id
            for deal_dict in deals:
                deal_id = deal_dict.get("deal_id")
                if deal_id:
                    dm = db.execute(
                        select(DealMatch).where(
                            DealMatch.signal_id == signal_id_str,
                            DealMatch.deal_id == deal_id,
                            DealMatch.run_id.is_(None),
                        )
                    ).scalar_one_or_none()
                    if dm:
                        dm.run_id = run.id

            db.flush()
            run_map[signal_id_str] = (str(run.id), deals)

        # Call process_signal_matches ONCE with ALL signals (consolidated per-user emails)
        all_deals = {sig_id: deals for sig_id, (_, deals) in run_map.items()}
        all_run_ids = {sig_id: rid for sig_id, (rid, _) in run_map.items()}
        process_signal_matches(
            db=db,
            signal_deals=all_deals,
            run_ids=all_run_ids,
        )

    if db_override:
        _process(db_override)
    else:
        with next(get_db()) as db:
            _process(db)

    logger.info("Match alerts sent for %d signals", len(v2_signal_deals))


def run_matching_only(db: Session) -> None:
    logger.info("Running match-only mode against existing deals")
    deals = db.execute(select(Deal).where(Deal.is_active)).scalars().all()
    logger.info("Matching %d active deals against active signals", len(deals))

    # Pre-compute price deltas from history for all deals
    price_delta_map = _build_price_delta_map(db)

    user_digest: dict = defaultdict(dict)
    v2_signal_deals: dict = defaultdict(list)
    total_matches = 0
    value_stats_cache: dict = {}
    for deal in deals:
        duration_days = (deal.return_date - deal.depart_date).days if deal.return_date else 7
        deal_meta = {
            "gateway": deal.origin,
            "region": deal.destination,
            "destination_str": deal.destination_str or deal.destination or "",
            "hotel_name": deal.hotel_name or "",
            "duration_days": duration_days,
            "discount_pct": deal.discount_pct or 0,
        }

        matched_signals = match_deal_to_signals(db, deal, deal_meta)
        for signal in matched_signals:
            existing = db.execute(
                select(DealMatch).where(
                    DealMatch.signal_id == signal.id,
                    DealMatch.deal_id == deal.id,
                )
            ).scalar_one_or_none()

            if existing:
                continue

            # price_delta_map stores drop amounts (positive = drop);
            # negate to standard delta convention (negative = drop)
            drop = price_delta_map.get(deal.id)
            match = _create_deal_match(
                db, signal, deal, duration_days,
                -drop if drop is not None else None,
                stats_cache=value_stats_cache,
            )
            total_matches += 1

            delta = drop if drop is not None else 0
            sig_key = str(signal.id)

            # Accumulate for V2 match alerts
            v2_signal_deals[sig_key].append({
                "deal_id": str(deal.id),
                "price_cents": deal.price_cents,
                "price_dropped": delta > 0,
                "price_delta": delta,
                "hotel_name": deal.hotel_name or "",
                "hotel_id": deal.hotel_id or "",
                "star_rating": deal.star_rating,
                "depart_date": deal.depart_date,
                "return_date": deal.return_date,
                "duration_nights": duration_days,
                "destination": deal.destination or "",
                "destination_str": deal.destination_str or deal.destination or "",
                "origin": deal.origin or "",
                "deeplink_url": deal.deeplink_url or "",
                "provider": "selloff",
                "value_label": vlabel,
            })

    # Send match alert emails
    _send_cycle_alerts(v2_signal_deals, user_digest, db_override=db)

    # Refresh signal + route intelligence caches
    try:
        from app.services.signal_intel import refresh_all_active_signal_caches, refresh_route_intel_cache
        refresh_all_active_signal_caches(db)
        refresh_route_intel_cache(db)
    except Exception as e:
        logger.warning("Intel cache refresh failed: %s", e)

    logger.info("Match-only complete. New matches: %d", total_matches)


def _acquire_scrape_lock(db: Session) -> bool:
    """Try to acquire a Postgres advisory lock (non-blocking).

    Returns True if the lock was acquired, False if another scraper holds it.
    The lock is held for the lifetime of the DB session/connection and is
    automatically released on disconnect, crash, or session close.
    """
    result = db.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": _SCRAPE_ADVISORY_LOCK_KEY},
    ).scalar()
    return bool(result)


def run_scraper(once: bool = True, defer_alerts: bool = False) -> dict | None:
    """Run the SellOff scraper.

    Args:
        once: If True, run a single cycle and return.
        defer_alerts: If True, skip sending match alert emails and return
            the v2_signal_deals dict for the caller to consolidate.

    Returns:
        When defer_alerts=True, returns the v2_signal_deals dict.
        Otherwise returns None.
    """
    logger.info("SellOff scraper starting")

    # Acquire advisory lock — prevents concurrent scrape cycles.
    # Hold a reference to the generator so GC doesn't close the session
    # (and release the lock) prematurely.
    _lock_gen = get_db()
    lock_db = next(_lock_gen)
    if not _acquire_scrape_lock(lock_db):
        logger.error(
            "SCRAPE BLOCKED: another scraper is already running (advisory lock held). Exiting."
        )
        _lock_gen.close()
        return {} if defer_alerts else None
    logger.info("Advisory lock acquired — this is the only running scraper")

    try:
        return _run_scraper_inner(once, defer_alerts=defer_alerts)
    finally:
        # Release the advisory lock by closing the generator (which closes the session)
        try:
            _lock_gen.close()
            logger.info("Advisory lock released")
        except Exception:
            pass


def _run_scraper_inner(once: bool, defer_alerts: bool = False) -> dict | None:
    """Run scrape cycles.

    Args:
        once: If True, run a single cycle and return.
        defer_alerts: If True, skip sending match alert emails and return
            the v2_signal_deals dict so the caller can consolidate alerts
            across multiple scrapers.

    Returns:
        When defer_alerts=True and once=True, returns the v2_signal_deals dict.
        Otherwise returns None.
    """
    while True:
        cycle_errors: list = []
        total_deals = 0
        total_matches = 0
        deals_deactivated = 0
        deals_expired = 0
        seen_dedupe_keys: set[str] = set()
        scrape_value_stats_cache: dict = {}
        started_at = datetime.now(timezone.utc)
        run_id = None
        _deferred_signal_deals: dict = {}

        try:
            # Browser profile + curl_cffi session for this cycle
            global _cycle_session, _last_page_url, _cycle_profile
            global _pages_on_current_ip, _ip_rotation_threshold
            _last_page_url = None  # Reset referer chain for new cycle
            _cycle_profile = pick_cycle_profile()
            _pages_on_current_ip = 0
            _ip_rotation_threshold = random.randint(30, 80)
            logger.info(
                "Cycle browser profile: %s (%s), IP rotation every ~%d pages",
                _cycle_profile["impersonate"], _cycle_profile["platform"],
                _ip_rotation_threshold,
            )

            # Check UA staleness at cycle start
            check_ua_staleness()

            proxy_ip = None
            proxy_url = _build_proxy_url()
            _cycle_session = _create_session(proxy_url)

            if proxy_url:
                logger.info("Using residential proxy (Canada) via DataImpulse")
                try:
                    resp = _cycle_session.get("https://api.ipify.org?format=json", timeout=10)
                    ip_data = resp.json()
                    raw_ip = ip_data.get("ip", "")
                    # Validate IP format to prevent injection into geo lookup URL
                    ipaddress.ip_address(raw_ip)
                    proxy_ip = raw_ip
                    logger.info("Proxy check passed: scraping from IP %s", proxy_ip)
                except Exception as e:
                    logger.warning("Proxy check FAILED — falling back to direct connection: %s", e)
                    _cycle_session.close()
                    _cycle_session = _create_session(proxy_url=None)
            else:
                logger.info("Proxy not configured — using direct connection")

            # Geo-locate the proxy IP
            proxy_geo = None
            if proxy_ip:
                try:
                    geo_resp = _cycle_session.get(
                        f"http://ip-api.com/json/{proxy_ip}?fields=city,regionName,countryCode",
                        timeout=5,
                    )
                    geo = geo_resp.json()
                    if geo.get("city"):
                        proxy_geo = f"{geo['city']}, {geo.get('regionName', '')}, {geo.get('countryCode', '')}".strip(", ")
                        logger.info("Proxy geo: %s", proxy_geo)
                except Exception as e:
                    logger.debug("Proxy geo lookup failed: %s", e)

            # Post cycle start to API and capture run_id for correlation
            try:
                import requests as _req
                resp = _req.post("http://api:8000/api/system/scrape-started", json={
                    "started_at": started_at.isoformat(),
                    "proxy_enabled": proxy_url is not None,
                    "proxy_ip": proxy_ip,
                    "proxy_geo": proxy_geo,
                }, headers=_SYSTEM_API_HEADERS, timeout=5)
                if resp.ok:
                    run_id = resp.json().get("run_id")
            except Exception as e:
                logger.warning("Failed to post scrape-started: %s", e)

            user_digest: dict = defaultdict(dict)
            # V2 match alert accumulator: {signal_id_str: [deal_dict, ...]}
            v2_signal_deals: dict = defaultdict(list)

            # Pre-load active signals once for the entire cycle
            with next(get_db()) as sig_db:
                _cycle_signals = load_active_signals(sig_db)
            logger.info("Loaded %d active signals for matching", len(_cycle_signals))

            # Block detection: consecutive non-404 pages with 0 deals
            _consecutive_empty = 0
            _BLOCK_THRESHOLD = 8  # stop after 8 consecutive empty (non-404) pages

            # Warm up the session with top-level page visits (collects cookies)
            _warmup_session()

            # Tiered destination and gateway selection — high-volume routes daily,
            # low-volume routes probabilistically to reduce footprint
            cycle_destinations = select_cycle_destinations(DESTINATION_SLUGS)
            cycle_gateways = select_cycle_gateways(GATEWAY_SLUGS)
            logger.info(
                "Cycle coverage: %d/%d destinations, %d/%d gateways",
                len(cycle_destinations), len(DESTINATION_SLUGS),
                len(cycle_gateways), len(GATEWAY_SLUGS),
            )

            elapsed = 0
            for slug in cycle_destinations:
                if _shutdown_requested:
                    logger.info("Shutdown requested — breaking out of destination loop")
                    break
                for gateway_code, city_slug in cycle_gateways:
                    if _shutdown_requested:
                        break
                    # Randomly skip ~5% of remaining pages for additional unpredictability
                    if random.random() < 0.05:
                        logger.debug("Random skip: %s from %s", slug, city_slug)
                        continue

                    url = f"https://www.selloffvacations.com/en/{slug}/from-{city_slug}"
                    logger.info("Scraping %s", url)

                    deals = fetch_deals_from_page(url)
                    logger.info("Found %d deals on %s", len(deals), url)

                    # Block detection circuit breaker
                    if not deals and not _last_fetch_was_404:
                        _consecutive_empty += 1
                        if _consecutive_empty >= _BLOCK_THRESHOLD:
                            logger.error(
                                "POSSIBLE BLOCK: %d consecutive non-404 pages returned 0 deals. "
                                "Stopping cycle to avoid wasting requests.",
                                _consecutive_empty,
                            )
                            cycle_errors.append({"error": "Block detected: consecutive empty pages", "type": "block"})
                            break
                    elif deals:
                        _consecutive_empty = 0

                    if not deals:
                        cycle_errors.append({"url": url, "error": "No deals found", "type": "empty"})

                    with next(get_db()) as db:
                        for deal_meta in deals:
                            try:
                                deal = upsert_deal(db, deal_meta)
                                if not deal:
                                    continue

                                seen_dedupe_keys.add(deal.dedupe_key)
                                total_deals += 1
                                matched_signals = match_deal_to_signals(db, deal, deal_meta, signals=_cycle_signals)

                                for signal in matched_signals:
                                    existing = db.execute(
                                        select(DealMatch).where(
                                            DealMatch.signal_id == signal.id,
                                            DealMatch.deal_id == deal.id,
                                        )
                                    ).scalar_one_or_none()

                                    if existing:
                                        continue

                                    duration_days = deal_meta.get("duration_days", 7)
                                    # _price_delta stores drop amount (positive = drop);
                                    # negate to standard delta convention (negative = drop)
                                    drop = getattr(deal, "_price_delta", None)
                                    match = _create_deal_match(
                                        db, signal, deal, duration_days,
                                        -drop if drop is not None else None,
                                        stats_cache=scrape_value_stats_cache,
                                    )
                                    total_matches += 1
                                    logger.info("Match: %s -> %s %s $%d", signal.name, deal.destination, deal.depart_date, deal.price_cents // 100)

                                    # Accumulate for V2 match alerts
                                    sig_key = str(signal.id)
                                    v2_signal_deals[sig_key].append({
                                        "deal_id": str(deal.id),
                                        "price_cents": deal.price_cents,
                                        "price_dropped": getattr(deal, "_price_dropped", False),
                                        "price_delta": getattr(deal, "_price_delta", 0),
                                        "hotel_name": deal.hotel_name or "",
                                        "hotel_id": deal.hotel_id or "",
                                        "star_rating": deal.star_rating,
                                        "depart_date": deal.depart_date,
                                        "return_date": deal.return_date,
                                        "duration_nights": duration_days,
                                        "destination": deal.destination or "",
                                        "destination_str": deal.destination_str or deal.destination or "",
                                        "origin": deal.origin or "",
                                        "deeplink_url": deal.deeplink_url or "",
                                        "provider": "selloff",
                                        "value_label": vlabel,
                                    })

                            except Exception as e:
                                logger.error("Error processing deal: %s", e)
                                cycle_errors.append({"url": url, "error": str(e), "type": "error"})
                                continue

                    # Human-like delay between pages (skip for 404s)
                    if not _last_fetch_was_404:
                        _interruptible_sleep(human_delay())

                    # Rotate proxy IP periodically to avoid single-IP detection
                    if proxy_url:
                        _maybe_rotate_ip(proxy_url)

                    # Internal timeout: bail if cycle has been running too long
                    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                    if elapsed > MAX_CYCLE_SECONDS:
                        logger.warning(
                            "CYCLE TIMEOUT: %d seconds elapsed (limit %d). "
                            "Stopping early with %d deals scraped so far.",
                            int(elapsed), MAX_CYCLE_SECONDS, total_deals,
                        )
                        cycle_errors.append({
                            "error": f"Cycle timeout after {int(elapsed)}s",
                            "type": "timeout",
                        })
                        break
                if elapsed > MAX_CYCLE_SECONDS:
                    break
                if _consecutive_empty >= _BLOCK_THRESHOLD:
                    break

                # Longer pause between destination categories
                if not _shutdown_requested:
                    pause = category_pause()
                    logger.debug("Category pause: %.0fs before next destination", pause)
                    _interruptible_sleep(pause)

                    # Occasionally visit a non-deal page between categories (~30% chance)
                    if random.random() < 0.30:
                        _visit_nav_page()
                        _interruptible_sleep(random.uniform(3, 8))

            # Graduated staleness: increment missed_cycles, only deactivate after 3+ misses
            # Skip if shutdown was requested — incomplete cycle would incorrectly penalize unseen deals
            DEACTIVATION_THRESHOLD = 3
            try:
                if seen_dedupe_keys and not _shutdown_requested:
                    with next(get_db()) as db:
                        unseen = db.query(Deal).filter(
                            Deal.is_active,
                            Deal.dedupe_key.notin_(seen_dedupe_keys)
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

            # Mark expired deals (past departure date) inactive
            try:
                with next(get_db()) as db:
                    expired = db.query(Deal).filter(
                        Deal.is_active,
                        Deal.depart_date < date.today()
                    ).all()
                    if expired:
                        deactivated_now = datetime.now(timezone.utc)
                        for deal in expired:
                            deal.is_active = False
                            deal.deactivated_at = deactivated_now
                        db.commit()
                        deals_expired = len(expired)
                        logger.info("Marked %d expired deals inactive", deals_expired)
            except Exception as e:
                logger.error("Expired deal cleanup failed: %s", e)
                cycle_errors.append({"error": str(e), "type": "expired_cleanup"})

            # Send match alert emails after full cycle (unless deferred to orchestrator)
            if defer_alerts:
                _deferred_signal_deals = dict(v2_signal_deals)
                logger.info(
                    "Alert sending deferred to orchestrator (%d signals with deals)",
                    len(_deferred_signal_deals),
                )
            else:
                try:
                    _send_cycle_alerts(v2_signal_deals, user_digest)
                except Exception as e:
                    logger.error("Match alert sending failed: %s", e)
                    cycle_errors.append({"error": str(e), "type": "alert_send"})

            # Refresh signal + route intelligence caches after each scrape cycle.
            # Skip when orchestrator will do it after all scrapers finish (defer_alerts mode).
            if not defer_alerts:
                try:
                    from app.services.signal_intel import refresh_all_active_signal_caches, refresh_route_intel_cache
                    with next(get_db()) as intel_db:
                        refresh_all_active_signal_caches(intel_db)
                        refresh_route_intel_cache(intel_db)
                except Exception as e:
                    logger.warning("Intel cache refresh failed: %s", e)

            completed_at = datetime.now(timezone.utc)
            logger.info("Scrape complete. Deals: %d, Matches: %d", total_deals, total_matches)
            logger.info("Unique dedupe keys this cycle: %d (total upserts: %d)", len(seen_dedupe_keys), total_deals)

            # Post completion summary to API
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/collection-complete", json={
                    "run_id": run_id,
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "total_deals": total_deals,
                    "total_matches": total_matches,
                    "error_count": sum(1 for e in cycle_errors if e.get("type") == "error"),
                    "errors": cycle_errors,
                    "deals_deactivated": deals_deactivated,
                    "deals_expired": deals_expired,
                    "status": "completed",
                    "proxy_enabled": proxy_url is not None,
                    "proxy_ip": proxy_ip,
                    "proxy_geo": proxy_geo,
                }, headers=_SYSTEM_API_HEADERS, timeout=5)
            except Exception as e:
                logger.warning("Failed to post collection summary: %s", e)

        except Exception as e:
            logger.error("SCRAPE CYCLE CRASHED: %s\n%s", e, traceback.format_exc())
            # Report crash so the ScrapeRun row doesn't stay orphaned as "running"
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/collection-complete", json={
                    "run_id": run_id,
                    "started_at": started_at.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "total_deals": total_deals,
                    "total_matches": total_matches,
                    "error_count": 1,
                    "errors": [{"error": str(e), "type": "crash"}],
                    "deals_deactivated": deals_deactivated,
                    "deals_expired": deals_expired,
                    "status": "crashed",
                }, headers=_SYSTEM_API_HEADERS, timeout=5)
            except Exception:
                logger.error("Failed to report crash to API")
        finally:
            # Always clean up the curl_cffi session to prevent fd/connection leaks
            if _cycle_session:
                try:
                    _cycle_session.close()
                except Exception:
                    pass

        if once or _shutdown_requested:
            if _shutdown_requested:
                logger.info("Shutting down gracefully after completed cycle")
            if defer_alerts:
                return _deferred_signal_deals
            return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Scrape once and exit")
    parser.add_argument("--match-only", action="store_true", help="Skip scraping, just run matching against existing deals")
    args = parser.parse_args()

    if args.match_only:
        with next(get_db()) as db:
            run_matching_only(db)
    else:
        run_scraper(once=args.once)
