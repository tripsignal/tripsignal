"""SellOff Vacations scraper and signal matcher."""
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
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger("selloff_scraper")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
NEXT_SCAN_FILE = "/tmp/next_scan.json"
_SYSTEM_API_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}

# Graceful shutdown — finish current cycle before exiting
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("SIGTERM received — will finish current cycle then exit")


_signal.signal(_signal.SIGTERM, _handle_sigterm)
_signal.signal(_signal.SIGINT, _handle_sigterm)

# Proxy configuration (DataImpulse residential proxy)
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1", "yes")
PROXY_HOST = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
PROXY_PORT = os.getenv("PROXY_PORT", "823")
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")
PROXY_COUNTRY = os.getenv("PROXY_COUNTRY", "cr.ca")

# Module-level proxy opener, set per cycle in run_scraper()
_cycle_proxy_opener: Optional[urllib.request.OpenerDirector] = None

# Scrape schedule: 3 daily windows in Eastern Time (America/Toronto)
# Each tuple: (start_hour, start_min, end_hour, end_min)
_ET = ZoneInfo("America/Toronto")
_SCRAPE_WINDOWS = [(7, 0, 9, 0), (12, 0, 14, 0), (18, 0, 20, 0)]


def _in_scrape_window() -> bool:
    """True if current Eastern time falls inside a scrape window."""
    now_et = datetime.now(_ET)
    for sh, sm, eh, em in _SCRAPE_WINDOWS:
        ws = now_et.replace(hour=sh, minute=sm, second=0, microsecond=0)
        we = now_et.replace(hour=eh, minute=em, second=0, microsecond=0)
        if ws <= now_et < we:
            return True
    return False


def _next_scrape_time() -> datetime:
    """Return a random UTC datetime in the next upcoming scrape window."""
    now_et = datetime.now(_ET)
    for day_offset in range(3):
        base = now_et + timedelta(days=day_offset)
        for sh, sm, eh, em in _SCRAPE_WINDOWS:
            window_start = base.replace(hour=sh, minute=sm, second=0, microsecond=0)
            window_end = base.replace(hour=eh, minute=em, second=0, microsecond=0)
            if window_start > now_et:
                offset = random.randint(0, int((window_end - window_start).total_seconds()))
                return (window_start + timedelta(seconds=offset)).astimezone(timezone.utc)
    # Fallback (shouldn't happen)
    return datetime.now(timezone.utc) + timedelta(hours=6)


def _build_proxy_url() -> Optional[str]:
    """Build proxy URL from env vars. Returns None if not configured."""
    if not PROXY_ENABLED or not PROXY_USER:
        return None
    return f"http://{PROXY_USER}__{PROXY_COUNTRY}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"


def _build_proxy_opener(proxy_url: str) -> urllib.request.OpenerDirector:
    """Build a urllib opener that routes through the proxy."""
    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    return urllib.request.build_opener(proxy_handler)

CATEGORIES = [
    "luxury-vacations",
    "adults-only",
    "family-vacations",
    "budget-friendly-vacations",
    "top-rated-all-inclusive-resorts",
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

DESTINATION_REGION_MAP = {
    # Sub-regions MUST come before parent catch-alls (first match wins)
    "riviera maya": "riviera_maya",
    "cancun": "cancun",
    "puerto vallarta": "puerto_vallarta",
    "los cabos": "los_cabos",
    "mazatlan": "mazatlan",
    "huatulco": "huatulco",
    "ixtapa": "ixtapa",
    "puerto escondido": "puerto_escondido",
    "mexico": "mexico",
    "punta cana": "punta_cana",
    "puerto plata": "puerto_plata",
    "la romana": "la_romana",
    "samana": "samana",
    "santo domingo": "santo_domingo",
    "dominican republic": "dominican_republic",
    "varadero": "varadero",
    "holguin": "holguin",
    "havana": "havana",
    "cayo coco": "cayo_coco",
    "santa clara": "cuba",
    "cuba": "cuba",
    "montego bay": "montego_bay",
    "negril": "negril",
    "ocho rios": "ocho_rios",
    "jamaica": "jamaica",
    "aruba": "aruba",
    "barbados": "barbados",
    "curacao": "curacao",
    "cayman islands": "cayman_islands",
    "saint lucia": "saint_lucia",
    "st. lucia": "saint_lucia",
    "st maarten": "st_maarten",
    "st. maarten": "st_maarten",
    "turks and caicos": "turks_caicos",
    "bahamas": "bahamas",
    "nassau": "bahamas",
    "antigua": "antigua",
    "grenada": "grenada",
    "costa rica": "costa_rica",
    "liberia": "costa_rica",
    "belize": "belize",
    "panama": "panama",
    "roatan": "roatan",
    "honduras": "central_america",
}

PARENT_REGION_MAP = {
    "cancun": "mexico",
    "riviera_maya": "mexico",
    "puerto_vallarta": "mexico",
    "los_cabos": "mexico",
    "mazatlan": "mexico",
    "huatulco": "mexico",
    "ixtapa": "mexico",
    "puerto_escondido": "mexico",
    "punta_cana": "dominican_republic",
    "puerto_plata": "dominican_republic",
    "la_romana": "dominican_republic",
    "samana": "dominican_republic",
    "santo_domingo": "dominican_republic",
    "montego_bay": "jamaica",
    "negril": "jamaica",
    "ocho_rios": "jamaica",
    "varadero": "cuba",
    "holguin": "cuba",
    "havana": "cuba",
    "cayo_coco": "cuba",
    "aruba": "caribbean",
    "barbados": "caribbean",
    "curacao": "caribbean",
    "cayman_islands": "caribbean",
    "saint_lucia": "caribbean",
    "st_maarten": "caribbean",
    "turks_caicos": "caribbean",
    "bahamas": "caribbean",
    "antigua": "caribbean",
    "grenada": "caribbean",
    "costa_rica": "central_america",
    "panama": "central_america",
    "belize": "central_america",
    "roatan": "central_america",
}


def deal_matches_signal_region(deal_region, signal_regions):
    if not deal_region:
        return False
    # Exact match
    if deal_region in signal_regions:
        return True
    # Parent match — deal is sub-region, signal has parent catch-all
    parent = PARENT_REGION_MAP.get(deal_region)
    if parent and parent in signal_regions:
        return True
    # Reverse match — deal is parent catch-all, signal has a sub-region of that parent
    for sr in signal_regions:
        if PARENT_REGION_MAP.get(sr) == deal_region:
            return True
    return False


def map_destination_to_region(destination: str) -> Optional[str]:
    dest_lower = destination.lower()
    for keyword, region in DESTINATION_REGION_MAP.items():
        if keyword in dest_lower:
            return region
    return None


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


def fetch_deals_from_page(url: str) -> list[dict]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Language": "en-CA,en;q=0.9",
            },
        )
        if _cycle_proxy_opener:
            html = _cycle_proxy_opener.open(req, timeout=30).read().decode("utf-8", "ignore")
        else:
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    except Exception as e:
        if _cycle_proxy_opener:
            logger.warning("Proxy error fetching %s: %s — retrying direct", url, e)
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept-Language": "en-CA,en;q=0.9",
                    },
                )
                html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            except Exception as e2:
                logger.warning("Direct retry also failed for %s: %s", url, e2)
                return []
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


def upsert_deal(db: Session, deal: dict) -> Optional[Deal]:
    dedupe_key = f"selloff:{deal['gateway']}:{deal['hotel_id']}:{deal['depart_date']}:{deal['duration_days']}"

    existing = db.execute(
        select(Deal).where(Deal.dedupe_key == dedupe_key)
    ).scalar_one_or_none()

    if existing:
        old_price = existing.price_cents
        # Mark as seen this cycle
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.missed_cycles = 0
        if not existing.is_active:
            existing.is_active = True
            existing.deactivated_at = None
        if existing.price_cents != deal["price_cents"]:
            existing.price_cents = deal["price_cents"]
            db.commit()
        # Track price delta for email hero selection (positive = drop)
        delta = old_price - deal["price_cents"]
        existing._price_dropped = delta > 0
        existing._price_delta = delta
        db.add(DealPriceHistory(deal_id=existing.id, price_cents=deal["price_cents"]))
        db.commit()
        return existing

    new_deal = Deal(
        provider="selloff",
        origin=deal["gateway"],
        destination=deal["region"] or deal["destination_str"],
        depart_date=deal["depart_date"],
        return_date=deal["return_date"],
        price_cents=deal["price_cents"],
        currency="CAD",
        deeplink_url=deal["deeplink_url"],
        dedupe_key=dedupe_key,
        hotel_name=deal.get("hotel_name"),
        hotel_id=deal.get("hotel_id"),
        discount_pct=deal.get("discount_pct"),
        destination_str=deal.get("destination_str"),
        star_rating=deal.get("star_rating"),
    )
    db.add(new_deal)
    db.commit()
    db.refresh(new_deal)
    new_deal._price_dropped = False
    new_deal._price_delta = 0
    db.add(DealPriceHistory(deal_id=new_deal.id, price_cents=new_deal.price_cents))
    db.commit()
    return new_deal


def match_deal_to_signals(db: Session, deal: Deal, deal_meta: dict) -> list[Signal]:
    signals = db.execute(
        select(Signal).where(Signal.status == "active")
    ).scalars().all()

    matches = []
    for signal in signals:
        try:
            config = signal.config
            budget = config.get("budget", {})
            travel_window = config.get("travel_window", {})
            config.get("travellers", {})

            if deal_meta["gateway"] not in signal.departure_airports:
                continue
            if not deal_matches_signal_region(deal_meta["region"], signal.destination_regions):
                continue

            start_date_str = travel_window.get("start_date")
            end_date_str = travel_window.get("end_date")
            if start_date_str and end_date_str:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                # start_date = earliest departure, end_date = latest return
                if deal.depart_date < start_dt:
                    continue
                deal_return = deal.return_date or (deal.depart_date + timedelta(days=deal_meta.get("duration_days", 7)))
                if deal_return > end_dt:
                    continue
            else:
                start_month_str = travel_window.get("start_month")
                end_month_str = travel_window.get("end_month")
                if start_month_str and end_month_str:
                    start_month = datetime.strptime(start_month_str, "%Y-%m").date().replace(day=1)
                    end_month_dt = datetime.strptime(end_month_str, "%Y-%m")
                    if end_month_dt.month == 12:
                        end_month = end_month_dt.replace(day=31).date()
                    else:
                        end_month = (end_month_dt.replace(month=end_month_dt.month + 1, day=1) - timedelta(days=1)).date()
                    if not (start_month <= deal.depart_date <= end_month):
                        continue

            min_nights = travel_window.get("min_nights")
            max_nights = travel_window.get("max_nights")
            if min_nights and deal_meta["duration_days"] < min_nights:
                continue
            if max_nights and deal_meta["duration_days"] > max_nights:
                continue

            preferences = config.get("preferences", {})
            min_star_rating = preferences.get("min_star_rating")
            if min_star_rating and deal.star_rating is not None:
                if deal.star_rating < float(min_star_rating):
                    continue

            # Budget check (deal prices are per-person, target_pp is per-person)
            target_pp = budget.get("target_pp")
            if target_pp:
                budget_cents = int(target_pp) * 100
                if deal.price_cents > budget_cents:
                    continue

            matches.append(signal)
        except Exception as e:
            logger.warning("Error matching signal %s: %s", signal.id, e)
            continue

    return matches



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


def _build_price_delta_map(db: Session) -> dict:
    """Query price history to find the most recent price drop per deal.

    Returns {deal_id_uuid: delta_cents} where delta > 0 means a drop.
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
        WHERE rn = 1 AND prev_price IS NOT NULL AND prev_price > price_cents
    """)).fetchall()
    return {row[0]: row[1] for row in rows}


def _send_cycle_alerts(
    v2_signal_deals: dict,
    user_digest: dict,
    db_override: Optional[Session] = None,
) -> None:
    """Send match alert emails for a scrape cycle.

    Creates SignalRun records and calls the orchestrator via
    process_signal_matches (one email per signal per run).
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

        # Now call process_signal_matches with {signal_id: [deals]} + run_id
        for signal_id_str, (run_id, deals) in run_map.items():
            process_signal_matches(
                db=db,
                signal_deals={signal_id_str: deals},
                run_id=run_id,
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

            ppn = deal.price_cents // duration_days if duration_days > 0 else None
            match = DealMatch(
                signal_id=signal.id,
                deal_id=deal.id,
                price_per_night_cents=ppn,
            )
            db.add(match)
            db.commit()
            total_matches += 1

            delta = price_delta_map.get(deal.id, 0)
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


def run_scraper(once: bool = True) -> None:
    logger.info("SellOff scraper starting")

    if not once:
        logger.info("Scraper configured for 3 daily cycles: ~8AM, ~1PM, ~7PM ET")
        if not _in_scrape_window():
            next_time = _next_scrape_time()
            next_et = next_time.astimezone(_ET)
            sleep_sec = max(0, (next_time - datetime.now(timezone.utc)).total_seconds())
            hours, remainder = divmod(int(sleep_sec), 3600)
            minutes = remainder // 60
            logger.info("Not in a scrape window — next scrape scheduled for %s ET (%dh %dm from now)",
                        next_et.strftime("%Y-%m-%d %I:%M %p"), hours, minutes)
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/next-scan", json={
                    "next_scan_at": next_time.timestamp(),
                    "last_scan_at": datetime.now(timezone.utc).timestamp(),
                }, headers=_SYSTEM_API_HEADERS, timeout=5)
            except Exception as e:
                logger.warning("Failed to post next_scan time: %s", e)
            time.sleep(sleep_sec)
        else:
            logger.info("Currently inside a scrape window — starting immediately")

    while True:
        cycle_errors: list = []
        total_deals = 0
        total_matches = 0
        deals_deactivated = 0
        deals_expired = 0
        seen_dedupe_keys: set[str] = set()
        started_at = datetime.now(timezone.utc)
        run_id = None

        try:
            # Proxy setup for this cycle
            global _cycle_proxy_opener
            _cycle_proxy_opener = None
            proxy_ip = None
            proxy_url = _build_proxy_url()
            if proxy_url:
                logger.info("Using residential proxy (Canada) via DataImpulse")
                try:
                    test_opener = _build_proxy_opener(proxy_url)
                    test_req = urllib.request.Request("https://api.ipify.org?format=json")
                    resp = test_opener.open(test_req, timeout=10)
                    ip_data = json.loads(resp.read().decode())
                    proxy_ip = ip_data.get("ip")
                    logger.info("Proxy check passed: scraping from IP %s", proxy_ip)
                    _cycle_proxy_opener = test_opener
                except Exception as e:
                    logger.warning("Proxy check FAILED — falling back to direct connection: %s", e)
            else:
                logger.info("Proxy not configured — using direct connection")

            # Geo-locate the proxy IP
            proxy_geo = None
            if proxy_ip:
                try:
                    geo_req = urllib.request.Request(f"http://ip-api.com/json/{proxy_ip}?fields=city,regionName,countryCode")
                    opener = _cycle_proxy_opener or urllib.request.build_opener()
                    geo_resp = opener.open(geo_req, timeout=5)
                    geo = json.loads(geo_resp.read().decode())
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
                    "proxy_enabled": _cycle_proxy_opener is not None,
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

            for category in CATEGORIES:
                for gateway_code, city_slug in GATEWAY_SLUGS.items():
                    url = f"https://www.selloffvacations.com/en/vacation-packages/{category}/from-{city_slug}"
                    logger.info("Scraping %s", url)

                    deals = fetch_deals_from_page(url)
                    logger.info("Found %d deals on %s", len(deals), url)
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

                                    duration_days = deal_meta.get("duration_days", 7)
                                    ppn = deal.price_cents // duration_days if duration_days > 0 else None
                                    match = DealMatch(
                                        signal_id=signal.id,
                                        deal_id=deal.id,
                                        price_per_night_cents=ppn,
                                    )
                                    db.add(match)
                                    db.commit()
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
                                    })

                            except Exception as e:
                                logger.error("Error processing deal: %s", e)
                                cycle_errors.append({"url": url, "error": str(e), "type": "error"})
                                continue

                    time.sleep(random.uniform(8, 20))

            # Graduated staleness: increment missed_cycles, only deactivate after 3+ misses
            DEACTIVATION_THRESHOLD = 3
            try:
                if seen_dedupe_keys:
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

            # Send match alert emails after full cycle
            try:
                _send_cycle_alerts(v2_signal_deals, user_digest)
            except Exception as e:
                logger.error("Match alert sending failed: %s", e)
                cycle_errors.append({"error": str(e), "type": "alert_send"})

            # Refresh signal + route intelligence caches after each scrape cycle
            try:
                from app.services.signal_intel import refresh_all_active_signal_caches, refresh_route_intel_cache
                with next(get_db()) as intel_db:
                    refresh_all_active_signal_caches(intel_db)
                    refresh_route_intel_cache(intel_db)
            except Exception as e:
                logger.warning("Intel cache refresh failed: %s", e)

            completed_at = datetime.now(timezone.utc)
            logger.info("Scrape complete. Deals: %d, Matches: %d", total_deals, total_matches)

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
                    "proxy_enabled": _cycle_proxy_opener is not None,
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

        if once or _shutdown_requested:
            if _shutdown_requested:
                logger.info("Shutting down gracefully after completed cycle")
            return

        next_time = _next_scrape_time()
        next_et = next_time.astimezone(_ET)
        sleep_seconds = max(0, (next_time - datetime.now(timezone.utc)).total_seconds())
        hours, remainder = divmod(int(sleep_seconds), 3600)
        minutes = remainder // 60
        logger.info("Next scrape scheduled for %s ET (%dh %dm from now)",
                    next_et.strftime("%Y-%m-%d %I:%M %p"), hours, minutes)
        try:
            import requests as _req
            _req.post("http://api:8000/api/system/next-scan", json={
                "next_scan_at": next_time.timestamp(),
                "last_scan_at": datetime.now(timezone.utc).timestamp(),
            }, headers=_SYSTEM_API_HEADERS, timeout=5)
        except Exception as e:
            logger.warning("Failed to post next_scan time: %s", e)
        time.sleep(sleep_seconds)


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
