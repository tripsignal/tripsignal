"""SellOff Vacations scraper and signal matcher."""
import base64
import hashlib
import hmac
import logging
import os
import random
import json
import re
import time
import uuid as _uuid
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import urllib.request
from sqlalchemy import func, null, select, text
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.signal import Signal
from app.db.models.user import User
from app.core.config import settings
from app.db.session import get_db

logger = logging.getLogger("selloff_scraper")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
UNSUB_SECRET = os.getenv("UNSUB_SECRET", "tripsignal-unsub-default-key")
NEXT_SCAN_FILE = "/tmp/next_scan.json"

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
            travellers = config.get("travellers", {})

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


def send_digest_email(user_email: str, signal: Signal, new_deals: list) -> None:
    if not RESEND_API_KEY:
        logger.warning("No RESEND_API_KEY set, skipping email")
        return

    if not new_deals:
        return

    count = len(new_deals)
    best_price = min(d.price_cents for d in new_deals) // 100
    price_drops = [d for d in new_deals if getattr(d, "_price_dropped", False)]

    if count == 1:
        subject = f"New deal found for your {signal.name} signal — from ${best_price:,}"
    else:
        subject = f"{count} new deals found for your {signal.name} signal — from ${best_price:,}"

    drop_line = ""
    if price_drops:
        drop_line = f'<p style="margin: 0 0 16px; font-size: 14px; color: #15803d;">&#8595; {len(price_drops)} deal{"s" if len(price_drops) > 1 else ""} dropped in price since your last check.</p>'

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; background: #fff; max-width: 560px; margin: 0 auto; padding: 40px 24px;">

  <div style="margin-bottom: 24px;">
    <span style="font-size: 20px; font-weight: 600; letter-spacing: -0.3px;">Trip Signal</span>
  </div>

  <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;">
    <p style="margin: 0; font-size: 13px; color: #15803d; font-weight: 500;">
      {count} new deal{"s" if count > 1 else ""} found for your signal: {signal.name}
    </p>
  </div>

  <h1 style="font-size: 22px; font-weight: 600; margin: 0 0 8px;">Best price found</h1>
  <p style="font-size: 32px; font-weight: 700; margin: 0 0 8px; color: #111;">${best_price:,} <span style="font-size: 16px; font-weight: 400; color: #666;">CAD</span></p>
  <p style="font-size: 14px; color: #666; margin: 0 0 24px;">Across {count} new matching deal{"s" if count > 1 else ""}.</p>

  {drop_line}

  <a href="https://tripsignal.ca/signals" style="display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-size: 14px; font-weight: 500; margin-bottom: 32px;">
    Review your deals &rarr;
  </a>

  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">

  <p style="font-size: 12px; color: #999; margin: 0;">
    You're receiving this because your Trip Signal signal "{signal.name}" found new matches.<br>
    Manage your signals at <a href="https://tripsignal.ca/signals" style="color: #999;">tripsignal.ca/signals</a>
  </p>

</body>
</html>"""

    import requests as _requests
    try:
        resp = _requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "Trip Signal <hello@tripsignal.ca>",
                "to": user_email,
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Digest email sent to %s — %d deals for signal %s", user_email, count, signal.id)
    except Exception as e:
        logger.error("Failed to send digest email to %s: %s", user_email, e)


def generate_unsub_token(user_id: str) -> str:
    """Generate an HMAC-signed token encoding a user ID for unsubscribe links."""
    sig = hmac.new(UNSUB_SECRET.encode(), user_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(f"{user_id}:{sig.hex()}".encode()).decode()


def validate_unsub_token(token: str) -> str | None:
    """Validate an unsubscribe token. Returns user_id (str UUID) if valid, None otherwise."""
    try:
        decoded = base64.urlsafe_b64decode(token).decode()
        user_id, sig_hex = decoded.rsplit(":", 1)
        expected = hmac.new(UNSUB_SECRET.encode(), user_id.encode(), hashlib.sha256).digest().hex()
        if hmac.compare_digest(sig_hex, expected):
            return user_id
    except Exception:
        pass
    return None


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

    # Respect notification_delivery_speed
    speed = getattr(user, "notification_delivery_speed", "immediate") or "immediate"
    if speed == "daily":
        last_sent = db.execute(
            select(func.max(NotificationOutbox.sent_at))
            .where(
                NotificationOutbox.to_email == user_email,
                NotificationOutbox.status == "sent",
            )
        ).scalar()
        if last_sent:
            hours_since = (datetime.now(timezone.utc) - last_sent).total_seconds() / 3600
            if hours_since < 20:
                logger.info(
                    "Skipping daily user %s — last email %.1fh ago",
                    user_email, hours_since,
                )
                return False, is_pro

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


def send_user_digest_email(user_email: str, signal_deals: dict, is_pro: bool = False, unsub_token: str = "", notification_id: str = "") -> None:
    """Send a single consolidated digest email covering all signals for one user.

    signal_deals: {signal_id_str: {"signal_name": str, "signal_id": UUID, "deals": [dict]}}
    Each deal dict: {price_cents, hotel_name, star_rating, depart_date, return_date,
                     destination_str, origin, price_dropped, price_delta}
    """
    if not RESEND_API_KEY:
        logger.warning("No RESEND_API_KEY set, skipping email")
        return

    if not signal_deals:
        return

    total_deals = sum(len(sd["deals"]) for sd in signal_deals.values())
    if total_deals == 0:
        return

    num_signals = len(signal_deals)

    # ── Collect all deals with their signal context ──────────────────
    all_deals_with_signal = []
    for sd in signal_deals.values():
        for d in sd["deals"]:
            all_deals_with_signal.append((d, sd["signal_name"]))

    # ── Count total price drops across all signals ───────────────────
    total_drops = sum(1 for d, _ in all_deals_with_signal if d.get("price_delta", 0) > 0)
    has_drops = total_drops > 0

    # ── Find the hero deal ───────────────────────────────────────────
    # If any deals have price drops, hero = biggest dollar drop
    # Otherwise, hero = cheapest deal
    dropped_deals = [(d, s) for d, s in all_deals_with_signal if d.get("price_delta", 0) > 0]
    if dropped_deals:
        hero_deal, hero_signal_name = max(dropped_deals, key=lambda x: x[0]["price_delta"])
    else:
        hero_deal, hero_signal_name = min(all_deals_with_signal, key=lambda x: x[0]["price_cents"])

    hero_price = hero_deal["price_cents"] // 100
    hero_city = _city_from_destination(hero_deal.get("destination_str", ""))
    hero_origin = hero_deal.get("origin", "")
    hero_origin_city = AIRPORT_CITY_MAP.get(hero_origin, hero_origin)
    hero_hotel = hero_deal.get("hotel_name", "")
    hero_stars = _star_display(hero_deal.get("star_rating"))
    hero_dates = _format_date_range(hero_deal.get("depart_date"), hero_deal.get("return_date"))
    hero_delta = hero_deal.get("price_delta", 0)

    dep = hero_deal.get("depart_date")
    ret = hero_deal.get("return_date")
    hero_nights = (ret - dep).days if dep and ret else 7

    # ── Readable destination for subject line ────────────────────────
    # Use the hero deal's city, or fall back to parsing the signal name
    subj_dest = hero_city or "your destinations"

    # ── Subject line (no price — curiosity-driven) ───────────────────
    if has_drops and num_signals == 1:
        subject = f"Price drop on your {subj_dest} signal"
    elif has_drops and num_signals > 1:
        subject = f"Prices dropped across {num_signals} of your signals"
    elif num_signals == 1:
        subject = f"New deals for your {subj_dest} signal"
    else:
        subject = f"New deals across {num_signals} of your signals"

    # ── Preheader (hidden preview text for inbox list view) ──────────
    cheapest = min(d["price_cents"] for d, _ in all_deals_with_signal) // 100
    if num_signals == 1:
        preheader = f"From ${cheapest:,} &middot; {total_deals} deal{'s' if total_deals > 1 else ''} available now"
    else:
        preheader = f"From ${cheapest:,} &middot; {total_deals} deal{'s' if total_deals > 1 else ''} across {num_signals} signals"

    # ── Hero deal: price context line (only if real price drop) ──────
    price_context_html = ""
    if hero_delta > 0:
        drop_dollars = hero_delta // 100
        price_context_html = f"""
      <p style="margin: 0 0 12px; font-size: 13px; color: #15803d; font-weight: 500;">
        &#8595; ${drop_dollars:,} less than last check
      </p>"""

    # ── Hero deal: detail line (dates · nights · stars · hotel) ──────
    detail_parts = []
    if hero_dates:
        detail_parts.append(hero_dates)
    detail_parts.append(f"{hero_nights} night{'s' if hero_nights != 1 else ''}")
    if hero_stars:
        detail_parts.append(hero_stars)
    if hero_hotel:
        detail_parts.append(hero_hotel)
    hero_detail_line = " &middot; ".join(detail_parts)

    # ── Hero route label (readable city names) ───────────────────────
    hero_route = f"{hero_origin_city} &rarr; {hero_city}" if hero_origin_city and hero_city else hero_signal_name

    # ── Urgency line (deal count for the hero's signal) ──────────────
    hero_signal_deal_count = 0
    for sd in signal_deals.values():
        if sd["signal_name"] == hero_signal_name:
            hero_signal_deal_count = len(sd["deals"])
            break
    urgency_html = f"""
      <p style="margin: 12px 0 0; font-size: 12px; color: #999;">
        {hero_signal_deal_count} deal{'s' if hero_signal_deal_count != 1 else ''} available for this signal &middot; Prices change daily
      </p>"""

    # ── Other signals section (readable names from deal data) ────────
    other_signals_html = ""
    sorted_signals = sorted(signal_deals.values(), key=lambda x: min(d["price_cents"] for d in x["deals"]))
    if num_signals > 1:
        signal_rows = []
        for sd in sorted_signals:
            deals = sd["deals"]
            sig_count = len(deals)
            sig_best = min(d["price_cents"] for d in deals) // 100
            sig_drops = sum(1 for d in deals if d.get("price_delta", 0) > 0)

            # Build readable signal label from deal data
            cheapest_deal = min(deals, key=lambda d: d["price_cents"])
            sig_city = _city_from_destination(cheapest_deal.get("destination_str", ""))
            sig_origin = cheapest_deal.get("origin", "")
            sig_origin_city = AIRPORT_CITY_MAP.get(sig_origin, sig_origin)
            sig_label = f"{sig_origin_city} &rarr; {sig_city}" if sig_origin_city and sig_city else sd["signal_name"]

            drop_text = ""
            if sig_drops:
                drop_text = f' &middot; <span style="color: #15803d;">&#8595; {sig_drops} price drop{"s" if sig_drops > 1 else ""}</span>'

            signal_rows.append(f"""
        <div style="padding: 12px 0; border-top: 1px solid #f0f0f0;">
          <p style="margin: 0; font-size: 14px; font-weight: 500; color: #111;">{sig_label}</p>
          <p style="margin: 2px 0 0; font-size: 13px; color: #666;">
            {sig_count} deal{'s' if sig_count > 1 else ''} from <strong style="color: #111;">${sig_best:,}</strong>{drop_text}
          </p>
        </div>""")

        other_signals_html = f"""
    <div style="background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #e5e5e5;">
      <p style="margin: 0 0 4px; font-size: 14px; font-weight: 600; color: #333;">
        More deals from your signals
      </p>
      {''.join(signal_rows)}
    </div>"""

    # ── Primary CTA — always drives to website ───────────────────────
    if num_signals == 1:
        cta_text = f"See all {total_deals} deal{'s' if total_deals > 1 else ''}"
    else:
        cta_text = "See all your deals"
    cta_html = f"""
    <a href="https://tripsignal.ca/signals" style="display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-size: 14px; font-weight: 500; margin-bottom: 20px;">
      {cta_text} &rarr;
    </a>"""

    # ── Pro upsell (free users only, contextual) ─────────────────────
    upsell_html = ""
    if not is_pro:
        if has_drops:
            upsell_copy = "Pro users got this alert hours earlier. Upgrade to get instant price drop notifications."
        else:
            upsell_copy = "Free signals check once a day. Pro checks multiple times a day &mdash; so you catch deals before prices change."
        upsell_html = f"""
    <div style="background: #fefce8; border: 1px solid #fde68a; border-radius: 12px; padding: 16px 20px; margin-bottom: 20px;">
      <p style="margin: 0 0 4px; font-size: 13px; font-weight: 600; color: #92400e;">&#9889; Go Pro</p>
      <p style="margin: 0 0 8px; font-size: 13px; color: #78350f; line-height: 1.4;">
        {upsell_copy}
      </p>
      <a href="https://tripsignal.ca/pricing" style="font-size: 13px; color: #92400e; font-weight: 500; text-decoration: underline;">
        Upgrade to Pro &rarr;
      </a>
    </div>"""

    # ── Assemble final HTML ──────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; background: #f5f5f5; margin: 0; padding: 0;">
<!-- Preheader text (hidden, shows in inbox preview) -->
<div style="display: none; max-height: 0; overflow: hidden; mso-hide: all;">
  {preheader}
</div>
<div style="max-width: 560px; margin: 0 auto; padding: 32px 16px;">

  <!-- Header -->
  <div style="margin-bottom: 24px;">
    <span style="font-size: 20px; font-weight: 600; letter-spacing: -0.3px;">Trip Signal</span>
  </div>

  <!-- Hero Deal -->
  <div style="background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 20px; border: 1px solid #e5e5e5;">
    <p style="margin: 0 0 6px; font-size: 12px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 0.5px;">
      &#9992; {hero_route}
    </p>

    <p style="margin: 0 0 2px; font-size: 36px; font-weight: 700; color: #111; line-height: 1.1;">
      ${hero_price:,} <span style="font-size: 15px; font-weight: 400; color: #666;">CAD per person</span>
    </p>
    {price_context_html}
    <p style="margin: 0 0 4px; font-size: 14px; color: #555; line-height: 1.4;">
      {hero_detail_line}
    </p>
    {urgency_html}
  </div>

  {other_signals_html}

  {cta_html}

  {upsell_html}

  <!-- Footer -->
  <hr style="border: none; border-top: 1px solid #e5e5e5; margin: 24px 0;">
  <p style="font-size: 12px; color: #999; margin: 0; line-height: 1.6;">
    You're receiving this because you have active signals on Trip Signal.<br>
    <a href="https://tripsignal.ca/signals" style="color: #999;">Manage signals</a> &middot;
    <a href="https://tripsignal.ca/unsubscribe?token={unsub_token}" style="color: #999;">Unsubscribe</a>
  </p>
  {f'<img src="https://tripsignal.ca/api/notifications/{notification_id}/pixel.png" width="1" height="1" style="display:block;" alt="" />' if notification_id else ''}

</div>
</body>
</html>"""

    import requests as _requests
    try:
        resp = _requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "Trip Signal <hello@tripsignal.ca>",
                "to": user_email,
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Consolidated digest sent to %s — %d deals across %d signals", user_email, total_deals, num_signals)
    except Exception as e:
        logger.error("Failed to send consolidated digest to %s: %s", user_email, e)


def send_all_user_digests(db: Session, user_digest: dict, stagger_seconds: float = 1.5) -> None:
    """Send one consolidated email per user, with staggering between sends."""
    user_emails = sorted(user_digest.keys())
    logger.info("Sending consolidated digests to %d users", len(user_emails))

    for i, user_email in enumerate(user_emails):
        try:
            can_send, is_pro = validate_user_for_email(db, user_email)
            if not can_send:
                continue

            # Generate unsubscribe token for this user
            user = db.execute(
                select(User).where(User.email == user_email)
            ).scalar_one_or_none()
            unsub_token = generate_unsub_token(str(user.id)) if user else ""

            signal_deals = user_digest[user_email]

            # Create outbox record BEFORE sending so we have the ID for the tracking pixel
            total_deals = sum(len(sd["deals"]) for sd in signal_deals.values())
            signal_names = ", ".join(sd["signal_name"] for sd in signal_deals.values())
            first_signal = next(iter(signal_deals.values()))
            outbox = NotificationOutbox(
                id=_uuid.uuid4(),
                signal_id=first_signal["signal_id"],
                match_id=_uuid.uuid4(),
                channel="email",
                to_email=user_email,
                subject=f"{total_deals} new deals across {len(signal_deals)} signals",
                body_text=f"Consolidated digest for signals: {signal_names}",
                status="pending",
            )
            db.add(outbox)
            db.flush()  # get the ID without committing

            send_user_digest_email(
                user_email, signal_deals,
                is_pro=is_pro, unsub_token=unsub_token,
                notification_id=str(outbox.id),
            )

            # Mark as sent after successful send
            outbox.status = "sent"
            outbox.sent_at = datetime.now(timezone.utc)
            outbox.next_attempt_at = null()
            db.commit()

            # Stagger between users
            if i < len(user_emails) - 1:
                time.sleep(stagger_seconds)

        except Exception as e:
            logger.error("Error sending digest for user %s: %s", user_email, e)
            try:
                db.rollback()
            except Exception:
                pass
            continue

    logger.info("Digest sending complete for %d users", len(user_emails))


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

    V2 gate: when EMAIL_V2_ENABLED=true, creates SignalRun records and calls
    the orchestrator via process_signal_matches (one email per signal per run).
    When false, falls back to legacy send_all_user_digests.
    """
    from app.core.config import settings

    if settings.EMAIL_V2_ENABLED and v2_signal_deals:
        from app.db.models.signal_run import SignalRun, SignalRunType, SignalRunStatus
        from app.services.match_alert import process_signal_matches

        def _process(db: Session) -> None:
            now = datetime.now(timezone.utc)
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

                # Re-key so process_signal_matches can use the run_id
                v2_signal_deals[signal_id_str] = (str(run.id), deals)

            # Now call process_signal_matches with {signal_id: [deals]} + run_id
            for signal_id_str, (run_id, deals) in v2_signal_deals.items():
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

        logger.info("V2 match alerts sent for %d signals", len(v2_signal_deals))

    elif not settings.EMAIL_V2_ENABLED and user_digest:
        # Legacy path — only when V2 is explicitly disabled
        if db_override:
            send_all_user_digests(db_override, user_digest)
        else:
            with next(get_db()) as db:
                send_all_user_digests(db, user_digest)


def run_matching_only(db: Session) -> None:
    logger.info("Running match-only mode against existing deals")
    deals = db.execute(select(Deal).where(Deal.is_active == True)).scalars().all()
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

            match = DealMatch(signal_id=signal.id, deal_id=deal.id)
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
                "star_rating": deal.star_rating,
                "depart_date": deal.depart_date,
                "return_date": deal.return_date,
                "duration_nights": duration_days,
                "destination_str": deal.destination_str or deal.destination or "",
                "origin": deal.origin or "",
                "deeplink_url": deal.deeplink_url or "",
            })

            # Accumulate for legacy consolidated digest (only when V2 disabled)
            if not settings.EMAIL_V2_ENABLED:
                user_email = signal.config.get("notifications", {}).get("email", "")
                if user_email:
                    if sig_key not in user_digest[user_email]:
                        user_digest[user_email][sig_key] = {
                            "signal_name": signal.name,
                            "signal_id": signal.id,
                            "deals": [],
                        }
                    user_digest[user_email][sig_key]["deals"].append({
                        "price_cents": deal.price_cents,
                        "price_dropped": delta > 0,
                        "price_delta": delta,
                        "hotel_name": deal.hotel_name or "",
                        "star_rating": deal.star_rating,
                        "depart_date": deal.depart_date,
                        "return_date": deal.return_date,
                        "destination_str": deal.destination_str or deal.destination or "",
                        "origin": deal.origin or "",
                    })

    # Send match alert emails
    _send_cycle_alerts(v2_signal_deals, user_digest, db_override=db)

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
                }, timeout=5)
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
        seen_dedupe_keys: set[str] = set()
        started_at = datetime.now(timezone.utc)

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

        # Post cycle start to API
        try:
            import requests as _req
            _req.post("http://api:8000/api/system/scrape-started", json={
                "started_at": started_at.isoformat(),
                "proxy_enabled": _cycle_proxy_opener is not None,
                "proxy_ip": proxy_ip,
                "proxy_geo": proxy_geo,
            }, timeout=5)
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

                                match = DealMatch(signal_id=signal.id, deal_id=deal.id)
                                db.add(match)
                                db.commit()
                                total_matches += 1
                                logger.info("Match: %s -> %s %s $%d", signal.name, deal.destination, deal.depart_date, deal.price_cents // 100)

                                duration_days = deal_meta.get("duration_days", 7)

                                # Accumulate for V2 match alerts
                                sig_key = str(signal.id)
                                v2_signal_deals[sig_key].append({
                                    "deal_id": str(deal.id),
                                    "price_cents": deal.price_cents,
                                    "price_dropped": getattr(deal, "_price_dropped", False),
                                    "price_delta": getattr(deal, "_price_delta", 0),
                                    "hotel_name": deal.hotel_name or "",
                                    "star_rating": deal.star_rating,
                                    "depart_date": deal.depart_date,
                                    "return_date": deal.return_date,
                                    "duration_nights": duration_days,
                                    "destination_str": deal.destination_str or deal.destination or "",
                                    "origin": deal.origin or "",
                                    "deeplink_url": deal.deeplink_url or "",
                                })

                                # Accumulate for legacy consolidated digest (only when V2 disabled)
                                if not settings.EMAIL_V2_ENABLED:
                                    user_email = signal.config.get("notifications", {}).get("email", "")
                                    if user_email:
                                        if sig_key not in user_digest[user_email]:
                                            user_digest[user_email][sig_key] = {
                                                "signal_name": signal.name,
                                                "signal_id": signal.id,
                                                "deals": [],
                                            }
                                        user_digest[user_email][sig_key]["deals"].append({
                                            "price_cents": deal.price_cents,
                                            "price_dropped": getattr(deal, "_price_dropped", False),
                                            "price_delta": getattr(deal, "_price_delta", 0),
                                            "hotel_name": deal.hotel_name or "",
                                            "star_rating": deal.star_rating,
                                            "depart_date": deal.depart_date,
                                            "return_date": deal.return_date,
                                            "destination_str": deal.destination_str or deal.destination or "",
                                            "origin": deal.origin or "",
                                        })

                        except Exception as e:
                            logger.error("Error processing deal: %s", e)
                            cycle_errors.append({"url": url, "error": str(e), "type": "error"})
                            continue

                time.sleep(random.uniform(8, 20))

        # Mark stale deals inactive
        if seen_dedupe_keys:
            with next(get_db()) as db:
                stale = db.query(Deal).filter(
                    Deal.is_active == True,
                    Deal.dedupe_key.notin_(seen_dedupe_keys)
                ).all()
                deactivated_now = datetime.now(timezone.utc)
                for deal in stale:
                    deal.is_active = False
                    deal.deactivated_at = deactivated_now
                db.commit()
                deals_deactivated = len(stale)
                if stale:
                    logger.info("Marked %d deals inactive", len(stale))

        # Send match alert emails after full cycle
        _send_cycle_alerts(v2_signal_deals, user_digest)

        completed_at = datetime.now(timezone.utc)
        logger.info("Scrape complete. Deals: %d, Matches: %d", total_deals, total_matches)

        # Post completion summary to API
        try:
            import requests as _req
            _req.post("http://api:8000/api/system/collection-complete", json={
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "total_deals": total_deals,
                "total_matches": total_matches,
                "error_count": sum(1 for e in cycle_errors if e.get("type") == "error"),
                "errors": cycle_errors,
                "deals_deactivated": deals_deactivated,
                "status": "completed",
                "proxy_enabled": _cycle_proxy_opener is not None,
                "proxy_ip": proxy_ip,
                "proxy_geo": proxy_geo,
            }, timeout=5)
        except Exception as e:
            logger.warning("Failed to post collection summary: %s", e)

        if once:
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
                "last_scan_at": completed_at.timestamp(),
            }, timeout=5)
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
