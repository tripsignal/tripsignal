"""RedTag.ca vacation package scraper — prototype.

Scrapes RedTag's JSON API for all-inclusive vacation deals from Canadian
airports to Caribbean/Mexico/Central America destinations.

RedTag API flow:
  1. GET /vacations/search?... → creates a session, returns HTML with embedded session ID
  2. POST /vacations/search/ajaxRefineSearchresults with {"sid": "<session_id>", ...}
     → returns JSON with packageResults containing deals
  3. Paginate by incrementing page number (10 results per page)

Reference data (no session required):
  - GET /vacations//engine/vacations → all hotels, destinations, airports
  - GET /vacations/engine/destinations/{airport_lowercase} → available destinations

Usage:
  python -m app.workers.redtag_scraper --dry-run --once   # test one search, print results
  python -m app.workers.redtag_scraper --dry-run           # all searches, print results
  python -m app.workers.redtag_scraper --once              # full cycle with DB, then exit
  python -m app.workers.redtag_scraper                     # continuous daemon
"""
import logging
import os
import random
import re
import json
import time
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger("redtag_scraper")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Top 15 Canadian departure airports (by vacation travel volume)
REDTAG_GATEWAYS = {
    "YYZ,YTZ": "Toronto",
    "YUL": "Montreal",
    "YYC": "Calgary",
    "YVR": "Vancouver",
    "YEG": "Edmonton",
    "YOW": "Ottawa",
    "YWG": "Winnipeg",
    "YHZ": "Halifax",
    "YQB": "Quebec City",
    "YQR": "Regina",
    "YXE": "Saskatoon",
    "YLW": "Kelowna",
    "YYJ": "Victoria",
    "YHM": "Hamilton",
    "YXU": "London",
}

# RedTag destination IDs → display names (Caribbean/Mexico/Central America)
REDTAG_DESTINATIONS = {
    2: "Cancun",
    24: "Riviera Maya",
    3049111: "Playa Mujeres",
    9: "Puerto Vallarta",
    156: "Riviera Nayarit",
    77: "Los Cabos",
    44: "Huatulco",
    69: "Mazatlan",
    7: "Ixtapa",
    10: "Punta Cana",
    73: "La Romana",
    710451: "Miches",
    8: "Puerto Plata",
    40: "Samana",
    14: "Santo Domingo",
    18: "Montego Bay",
    4244: "Negril",
    1843: "Ocho Rios",
    1341400: "Runaway Bay",
    15: "Varadero",
    47: "Havana",
    6: "Holguin",
    92: "Cayo Coco - Cayo Guillermo",
    87: "Cayo Santa Maria",
    3: "Cayo Largo",
    3049121: "Cayo Cruz",
    27: "Antigua",
    29: "Aruba",
    30: "Bridgetown",  # Barbados
    76: "Curacao",
    34: "Grenada",
    36: "St Lucia",
    39: "St Maarten",
    62: "Providenciales",  # Turks & Caicos
    33: "Grand Cayman",
    25: "Nassau",
    83: "Roatan",
    59: "Panama City",
    57: "Belize City",
}

# Subset of top 15 destination IDs for initial prototype scope
REDTAG_TOP_DESTINATIONS = {
    2: "Cancun",
    24: "Riviera Maya",
    9: "Puerto Vallarta",
    77: "Los Cabos",
    156: "Riviera Nayarit",
    44: "Huatulco",
    10: "Punta Cana",
    73: "La Romana",
    18: "Montego Bay",
    4244: "Negril",
    15: "Varadero",
    6: "Holguin",
    92: "Cayo Coco - Cayo Guillermo",
    87: "Cayo Santa Maria",
    29: "Aruba",
}

# Tour operator codes → display names
TOUR_OPERATORS = {
    "CAH": "Caribe Sol",
    "CLM": "Club Med",
    "HOL": "Hola Sun",
    "NOL": "Nolitours",
    "POR": "Porter Escapes",
    "SGN": "Signature Vacances",
    "SQV": "Sunquest Vacations",
    "SWG": "Sunwing Vacations",
    "SUW": "Sunwing Vacations",
    "SWE": "Sunwing Vacations",
    "TMR": "Tours Mont-Royal",
    "VAC": "Air Canada Vacations",
    "VAE": "Air Canada Vacations",
    "VAX": "Air Canada Vacations",
    "TBA": "TravelBrands",
    "VAT": "Transat Holidays",
    "WJV": "Westjet Vacations",
    "WJS": "Westjet Vacations",
    "VWQ": "WestJet Vacations Quebec",
}

# Rotating browser user-agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

# Readable city names for emails (superset of gateways)
AIRPORT_CITY_MAP = {
    "YYZ,YTZ": "Toronto", "YYZ": "Toronto", "YTZ": "Toronto",
    "YUL": "Montreal", "YYC": "Calgary", "YVR": "Vancouver",
    "YEG": "Edmonton", "YOW": "Ottawa", "YWG": "Winnipeg",
    "YHZ": "Halifax", "YQB": "Quebec City", "YQR": "Regina",
    "YXE": "Saskatoon", "YLW": "Kelowna", "YYJ": "Victoria",
    "YHM": "Hamilton", "YXU": "London",
}

# ---------------------------------------------------------------------------
# Destination → TripSignal region mapping
# (copied from selloff_scraper.py to keep this file self-contained)
# ---------------------------------------------------------------------------

DESTINATION_REGION_MAP = {
    # Sub-regions MUST come before parent catch-alls (first match wins)
    "riviera maya": "riviera_maya",
    "playa mujeres": "cancun",  # Adjacent to Cancun
    "cancun": "cancun",
    "puerto vallarta": "puerto_vallarta",
    "riviera nayarit": "puerto_vallarta",  # Adjacent region
    "los cabos": "los_cabos",
    "mazatlan": "mazatlan",
    "huatulco": "huatulco",
    "ixtapa": "ixtapa",
    "puerto escondido": "puerto_escondido",
    "mexico": "mexico",
    "punta cana": "punta_cana",
    "puerto plata": "puerto_plata",
    "la romana": "la_romana",
    "miches": "punta_cana",  # Eastern DR, close to Punta Cana
    "samana": "samana",
    "santo domingo": "santo_domingo",
    "dominican republic": "dominican_republic",
    "varadero": "varadero",
    "holguin": "holguin",
    "havana": "havana",
    "cayo coco": "cayo_coco",
    "cayo guillermo": "cayo_coco",
    "cayo santa maria": "cuba",
    "cayo largo": "cuba",
    "cayo cruz": "cuba",
    "cayo paredon": "cuba",
    "santa clara": "cuba",
    "cuba": "cuba",
    "montego bay": "montego_bay",
    "negril": "negril",
    "ocho rios": "ocho_rios",
    "runaway bay": "jamaica",
    "jamaica": "jamaica",
    "aruba": "aruba",
    "bridgetown": "barbados",
    "barbados": "barbados",
    "curacao": "curacao",
    "grand cayman": "cayman_islands",
    "cayman islands": "cayman_islands",
    "saint lucia": "saint_lucia",
    "st lucia": "saint_lucia",
    "st. lucia": "saint_lucia",
    "st maarten": "st_maarten",
    "st. maarten": "st_maarten",
    "turks and caicos": "turks_caicos",
    "providenciales": "turks_caicos",
    "bahamas": "bahamas",
    "nassau": "bahamas",
    "antigua": "antigua",
    "grenada": "grenada",
    "costa rica": "costa_rica",
    "belize": "belize",
    "belize city": "belize",
    "panama": "panama",
    "panama city": "panama",
    "roatan": "roatan",
    "honduras": "central_america",
    "san andres": "caribbean",
    "bonaire": "caribbean",
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


def deal_matches_signal_region(deal_region: str, signal_regions: list[str]) -> bool:
    if not deal_region:
        return False
    if deal_region in signal_regions:
        return True
    parent = PARENT_REGION_MAP.get(deal_region)
    if parent and parent in signal_regions:
        return True
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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

BASE_URL = "https://secure-res.redtag.ca"


def _get_headers(referer: str = "") -> dict:
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = BASE_URL
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Accept"] = "*/*"
        del headers["Upgrade-Insecure-Requests"]
    return headers


def _build_search_url(gateway: str, dest_id: int, date_str: str) -> str:
    return (
        f"{BASE_URL}/vacations/search?"
        f"dest_dep={dest_id}&gateway_dep={gateway}&date={date_str}"
        f"&duration=7days,8days&numberOfRooms=1&numberOfAdults=2"
        f"&numberOfChildren=0&all_inclusive=y&date_format=Ymd"
        f"&alias=engine&sentalias=api&lang=en"
    )


# ---------------------------------------------------------------------------
# Session creation — the key unknown
# ---------------------------------------------------------------------------

# Patterns to search for session ID in the HTML response
_SID_PATTERNS = [
    # Common JS variable patterns
    re.compile(r'"sid"\s*:\s*"([a-f0-9]{20,64})"'),
    re.compile(r"'sid'\s*:\s*'([a-f0-9]{20,64})'"),
    re.compile(r'session_id\s*[=:]\s*"([a-f0-9]{20,64})"'),
    re.compile(r"session_id\s*[=:]\s*'([a-f0-9]{20,64})'"),
    re.compile(r'sessionId\s*[=:]\s*"([a-f0-9]{20,64})"'),
    re.compile(r"sessionId\s*[=:]\s*'([a-f0-9]{20,64})'"),
    re.compile(r'window\.__SESSION__\s*=\s*"([a-f0-9]{20,64})"'),
    re.compile(r'data-sid="([a-f0-9]{20,64})"'),
    # URL param patterns
    re.compile(r'[?&]sid=([a-f0-9]{20,64})'),
    # Generic hex token that looks like a session (32-char MD5 hash)
    re.compile(r'"([a-f0-9]{32})"'),
]

# Cookie names that might contain the session ID
_SESSION_COOKIE_NAMES = [
    "sid", "session_id", "PHPSESSID", "session", "sess_id",
    "search_session", "res_session",
]


def _extract_session_id_from_html(html: str) -> Optional[str]:
    """Try to extract session ID from the search page HTML body."""
    for pattern in _SID_PATTERNS[:-1]:  # Skip the generic hex pattern first
        m = pattern.search(html)
        if m:
            sid = m.group(1)
            logger.debug("Found session ID via pattern %s: %s", pattern.pattern[:40], sid)
            return sid

    # Last resort: look for a 32-char hex token near "sid" or "session"
    # Only use if we find it near a session-related keyword
    for keyword in ["sid", "session", "search"]:
        idx = html.lower().find(keyword)
        if idx >= 0:
            snippet = html[max(0, idx - 50):idx + 200]
            m = _SID_PATTERNS[-1].search(snippet)
            if m:
                sid = m.group(1)
                logger.debug("Found session ID near '%s': %s", keyword, sid)
                return sid

    return None


def _extract_session_id_from_cookies(session: requests.Session) -> Optional[str]:
    """Try to find a session ID in the cookie jar."""
    for name in _SESSION_COOKIE_NAMES:
        cookie = session.cookies.get(name)
        if cookie and len(cookie) >= 20:
            logger.debug("Found session ID in cookie '%s': %s", name, cookie)
            return cookie

    # Check all cookies for hex-looking values
    for cookie in session.cookies:
        if re.fullmatch(r"[a-f0-9]{20,64}", cookie.value):
            logger.debug("Found hex session ID in cookie '%s': %s", cookie.name, cookie.value)
            return cookie.value

    return None


def create_search_session(
    http_session: requests.Session, gateway: str, dest_id: int, date_str: str
) -> Optional[str]:
    """Load the search page to create a session, then extract the session ID.

    Returns the session ID string, or None if extraction fails.
    """
    url = _build_search_url(gateway, dest_id, date_str)
    headers = _get_headers()

    try:
        resp = http_session.get(url, headers=headers, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to load search page (%s → dest %d): %s", gateway, dest_id, e)
        return None

    # Strategy 1: Check cookies
    sid = _extract_session_id_from_cookies(http_session)
    if sid:
        return sid

    # Strategy 2: Parse HTML body
    sid = _extract_session_id_from_html(resp.text)
    if sid:
        return sid

    # Strategy 3: Try POST with sid=null to see if the API creates a session
    logger.debug("No session ID found in HTML/cookies, trying POST with sid=null")
    try:
        ajax_resp = http_session.post(
            f"{BASE_URL}/vacations/search/ajaxRefineSearchresults",
            json={
                "sid": None,
                "filter": {
                    "hotel": {"text": "", "value": ""},
                    "allInclusive": True,
                    "duration": None,
                    "deptDate": date_str,
                    "pkgOpt": [],
                    "tourOpt": [],
                    "priceSeq": -1,
                    "rating": "3",
                },
                "token": None,
            },
            headers=_get_headers(referer=url),
            timeout=30,
        )
        if ajax_resp.status_code == 200:
            data = ajax_resp.json()
            sid = data.get("session", {}).get("id")
            if sid:
                logger.debug("Got session ID from null-sid POST: %s", sid)
                return sid
    except Exception as e:
        logger.debug("Null-sid POST failed: %s", e)

    logger.warning("Could not extract session ID for %s → dest %d on %s", gateway, dest_id, date_str)
    return None


# ---------------------------------------------------------------------------
# Results fetching
# ---------------------------------------------------------------------------

def fetch_results_page(
    http_session: requests.Session,
    session_id: str,
    date_str: str,
    referer_url: str,
    page: int = 0,
) -> tuple[dict, list[dict]]:
    """Fetch one page of results from the RedTag AJAX endpoint.

    Returns (full_response_data, rows_list).
    """
    payload = {
        "sid": session_id,
        "filter": {
            "hotel": {"text": "", "value": ""},
            "allInclusive": True,
            "duration": None,
            "deptDate": date_str,
            "pkgOpt": [],
            "tourOpt": [],
            "priceSeq": -1,
            "rating": "3",
        },
        "token": None,
    }
    if page > 0:
        payload["page"] = page

    headers = _get_headers(referer=referer_url)
    headers["Content-Type"] = "application/json"

    try:
        resp = http_session.post(
            f"{BASE_URL}/vacations/search/ajaxRefineSearchresults",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("Failed to fetch results page %d: %s", page, e)
        return {}, []
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Invalid JSON in results page %d: %s", page, e)
        return {}, []

    rows = []
    pkg_results = data.get("packageResults")
    if pkg_results:
        rows = pkg_results.get("rows", [])

    return data, rows


def fetch_all_results(
    http_session: requests.Session,
    session_id: str,
    date_str: str,
    referer_url: str,
    max_pages: int = 10,
) -> tuple[list[dict], dict]:
    """Paginate through all result pages for a search.

    Returns (all_rows, extras_dict).
    """
    all_rows = []
    extras = {}

    for page in range(max_pages):
        data, rows = fetch_results_page(http_session, session_id, date_str, referer_url, page)

        if not rows:
            break

        all_rows.extend(rows)

        # Capture extras from first page (hotel images, tour operators, etc.)
        if page == 0:
            pkg_results = data.get("packageResults", {})
            extras = pkg_results.get("extras", {})

        # Check pagination
        pagination = data.get("packageResults", {}).get("pagination", {})
        total_pages = int(pagination.get("totalPage", 1))
        if page + 1 >= total_pages:
            break

        # Small delay between pages (same session, lighter load)
        time.sleep(random.uniform(1, 3))

    return all_rows, extras


# ---------------------------------------------------------------------------
# Deal parsing
# ---------------------------------------------------------------------------

def parse_redtag_result(row: dict, extras: dict, gateway: str) -> Optional[dict]:
    """Convert a RedTag packageResults row into a deal_meta dict.

    The row structure is:
      row.package.hotel.hotelName
      row.package.hotel.hotelRating
      row.package.hotel.location.address.city
      row.package.gatewayId
      row.package.depDate
      row.package.retArriveDate
      row.package.duration
      row.package.touroptCode
      row.rateInfo.pricingInfo.perPerson.total
      row.rateInfo.deepLink
      row.rateInfo.attr.roomDescription
      row.rateInfo.attr.mealtypeCode
    """
    try:
        pkg = row.get("package", {})
        rate_info = row.get("rateInfo", {})
        hotel = pkg.get("hotel", {})
        pricing = rate_info.get("pricingInfo", {}).get("perPerson", {})
        attr = rate_info.get("attr", {})
        location = hotel.get("location", {}).get("address", {})
        flight_info = pkg.get("flight", {}).get("slices", {}).get("outbound", {})

        hotel_name = hotel.get("hotelName", "").strip()
        hotel_id = str(hotel.get("hotelId", "")).strip()
        if not hotel_name or not hotel_id:
            return None

        # Price (per person, total including tax)
        price_str = pricing.get("total", "0")
        try:
            price_pp = float(price_str)
        except (ValueError, TypeError):
            return None
        if price_pp <= 0:
            return None
        price_cents = int(round(price_pp * 100))

        # Dates
        depart_str = pkg.get("depDate", "")
        return_str = pkg.get("retArriveDate", "")
        depart_date = _parse_date(depart_str)
        return_date = _parse_date(return_str)
        if not depart_date:
            return None

        # Duration
        duration_str = pkg.get("duration", "7")
        try:
            duration_days = int(duration_str)
        except (ValueError, TypeError):
            duration_days = 7

        # Destination
        city = location.get("city", "").strip()
        country = location.get("country", "").strip()
        destination_str = city or country or ""
        region = map_destination_to_region(destination_str)

        # Star rating
        star_str = hotel.get("hotelRating", "")
        try:
            star_rating = float(star_str) if star_str else None
        except (ValueError, TypeError):
            star_rating = None

        # Tour operator
        tour_code = pkg.get("touroptCode", "")
        tour_operator = TOUR_OPERATORS.get(tour_code, tour_code)

        # Flight info
        airline_code = ""
        stops = None
        segments = flight_info.get("segments", [])
        if segments:
            first_seg = segments[0]
            flight_detail = first_seg.get("flight", {})
            airline_code = flight_detail.get("carrier", "")
            try:
                stops = int(first_seg.get("nbstop", "0"))
            except (ValueError, TypeError):
                stops = None

        # Deep link
        deep_link = rate_info.get("deepLink", "")

        # Hotel image from extras
        hotel_images = extras.get("hotelImages", {})
        hotel_image_url = hotel_images.get(hotel_id, hotel_images.get(str(hotel_id), ""))

        # Reviews from extras
        reviews_data = extras.get("trustyouReviews", {})
        review_info = reviews_data.get(hotel_id, reviews_data.get(str(hotel_id), {}))
        review_count = review_info.get("reviews_count")

        # Room and meal type
        room_type = attr.get("roomDescription", "").strip()
        meal_type = attr.get("mealtypeCode", "").strip()

        # Price breakdown
        price_base = pricing.get("base", "")
        price_tax = pricing.get("tax", "")

        # Extras dict for provider-specific fields
        deal_extras = {}
        if tour_operator:
            deal_extras["tour_operator"] = tour_operator
        if tour_code:
            deal_extras["tour_operator_code"] = tour_code
        if room_type:
            deal_extras["room_type"] = room_type
        if meal_type:
            deal_extras["meal_type"] = meal_type
        if hotel_image_url:
            deal_extras["hotel_image_url"] = hotel_image_url
        if review_count is not None:
            deal_extras["review_count"] = review_count
        if price_base:
            deal_extras["price_base"] = price_base
        if price_tax:
            deal_extras["price_tax"] = price_tax

        return {
            "gateway": gateway,
            "destination_str": destination_str,
            "hotel_name": hotel_name,
            "hotel_id": hotel_id,
            "region": region,
            "depart_date": depart_date,
            "return_date": return_date,
            "duration_days": duration_days,
            "price_cents": price_cents,
            "discount_pct": 0,
            "deeplink_url": deep_link,
            "star_rating": star_rating,
            "airline": airline_code,
            "stops": stops,
            "extras": deal_extras or None,
        }
    except Exception as e:
        logger.warning("Failed to parse RedTag result: %s", e)
        return None


def _parse_date(date_str: str) -> Optional[date]:
    """Parse dates in formats returned by RedTag: 2026-04-01, 20260401."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Dry-run printing
# ---------------------------------------------------------------------------

def print_deal(deal: dict, index: int = 0) -> None:
    """Pretty-print a parsed deal for dry-run output."""
    d = deal
    price = d["price_cents"] / 100
    extras = d.get("extras") or {}
    tour_op = extras.get("tour_operator", "?")
    room = extras.get("room_type", "?")

    print(
        f"  [{index:3d}] {d['hotel_name']:<45} "
        f"{'★' * int(d['star_rating'] or 0):<5} "
        f"${price:>8.2f}/pp  "
        f"{d['gateway']:<8} → {d['destination_str']:<20} "
        f"{d['depart_date']}  {d['duration_days']}n  "
        f"{tour_op}  {room}"
    )


# ---------------------------------------------------------------------------
# Signal matching (from selloff_scraper.py — self-contained copy)
# ---------------------------------------------------------------------------

def match_deal_to_signals(db, deal_obj, deal_meta: dict):
    """Match a deal against all active user signals.

    This is a copy of the matching logic from selloff_scraper.py.
    Only used in non-dry-run mode.
    """
    from sqlalchemy import select as sa_select
    from app.db.models.signal import Signal

    signals = db.execute(
        sa_select(Signal).where(Signal.status == "active")
    ).scalars().all()

    matches = []
    for signal in signals:
        try:
            config = signal.config
            budget = config.get("budget", {})
            travel_window = config.get("travel_window", {})

            if deal_meta["gateway"] not in signal.departure_airports:
                continue
            if not deal_matches_signal_region(deal_meta["region"], signal.destination_regions):
                continue

            start_date_str = travel_window.get("start_date")
            end_date_str = travel_window.get("end_date")
            if start_date_str and end_date_str:
                start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if deal_obj.depart_date < start_dt:
                    continue
                deal_return = deal_obj.return_date or (deal_obj.depart_date + timedelta(days=deal_meta.get("duration_days", 7)))
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
                    if not (start_month <= deal_obj.depart_date <= end_month):
                        continue

            min_nights = travel_window.get("min_nights")
            max_nights = travel_window.get("max_nights")
            if min_nights and deal_meta["duration_days"] < min_nights:
                continue
            if max_nights and deal_meta["duration_days"] > max_nights:
                continue

            preferences = config.get("preferences", {})
            min_star_rating = preferences.get("min_star_rating")
            if min_star_rating and deal_obj.star_rating is not None:
                if deal_obj.star_rating < float(min_star_rating):
                    continue

            target_pp = budget.get("target_pp")
            if target_pp:
                budget_cents = int(target_pp) * 100
                if deal_obj.price_cents > budget_cents:
                    continue

            matches.append(signal)
        except Exception as e:
            logger.warning("Error matching signal %s: %s", signal.id, e)
            continue

    return matches


# ---------------------------------------------------------------------------
# Database operations (only used in non-dry-run mode)
# ---------------------------------------------------------------------------

def upsert_deal(db, deal_meta: dict):
    """Insert or update a deal in the database.

    Only used when not in dry-run mode. Follows the same pattern as
    selloff_scraper.py's upsert_deal.
    """
    from sqlalchemy import select as sa_select
    from app.db.models.deal import Deal
    from app.db.models.deal_price_history import DealPriceHistory

    dedupe_key = f"redtag:{deal_meta['gateway']}:{deal_meta['hotel_id']}:{deal_meta['depart_date']}:{deal_meta['duration_days']}"

    existing = db.execute(
        sa_select(Deal).where(Deal.dedupe_key == dedupe_key)
    ).scalar_one_or_none()

    if existing:
        old_price = existing.price_cents
        if not existing.is_active:
            existing.is_active = True
            existing.deactivated_at = None
        if existing.price_cents != deal_meta["price_cents"]:
            existing.price_cents = deal_meta["price_cents"]
        db.commit()
        delta = old_price - deal_meta["price_cents"]
        existing._price_dropped = delta > 0
        existing._price_delta = delta
        db.add(DealPriceHistory(deal_id=existing.id, price_cents=deal_meta["price_cents"]))
        db.commit()
        return existing

    new_deal = Deal(
        provider="redtag",
        origin=deal_meta["gateway"],
        destination=deal_meta["region"] or deal_meta["destination_str"],
        depart_date=deal_meta["depart_date"],
        return_date=deal_meta["return_date"],
        price_cents=deal_meta["price_cents"],
        currency="CAD",
        deeplink_url=deal_meta.get("deeplink_url"),
        dedupe_key=dedupe_key,
        hotel_name=deal_meta.get("hotel_name"),
        hotel_id=deal_meta.get("hotel_id"),
        discount_pct=deal_meta.get("discount_pct"),
        destination_str=deal_meta.get("destination_str"),
        star_rating=deal_meta.get("star_rating"),
        airline=deal_meta.get("airline"),
        stops=deal_meta.get("stops"),
    )
    db.add(new_deal)
    db.commit()
    db.refresh(new_deal)
    new_deal._price_dropped = False
    new_deal._price_delta = 0
    db.add(DealPriceHistory(deal_id=new_deal.id, price_cents=new_deal.price_cents))
    db.commit()
    return new_deal


# ---------------------------------------------------------------------------
# Main scraper loop
# ---------------------------------------------------------------------------

def _generate_search_dates(weeks_ahead: int = 14) -> list[str]:
    """Generate one date per week for the next N weeks (YYYYMMDD format).

    Starts from next Saturday (typical vacation departure day).
    """
    today = date.today()
    # Find next Saturday
    days_until_sat = (5 - today.weekday()) % 7
    if days_until_sat == 0:
        days_until_sat = 7
    next_sat = today + timedelta(days=days_until_sat)

    dates = []
    for w in range(weeks_ahead):
        d = next_sat + timedelta(weeks=w)
        dates.append(d.strftime("%Y%m%d"))
    return dates


def run_scraper(once: bool = True, dry_run: bool = False) -> None:
    mode_label = "DRY-RUN" if dry_run else "LIVE"
    logger.info("RedTag scraper starting [%s]", mode_label)

    while True:
        cycle_errors: list = []
        total_deals = 0
        total_results = 0
        started_at = datetime.now(timezone.utc)
        search_dates = _generate_search_dates(14)

        if not dry_run:
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/scrape-started", json={
                    "started_at": started_at.isoformat(),
                    "provider": "redtag",
                }, timeout=5)
            except Exception as e:
                logger.warning("Failed to post scrape-started: %s", e)

        destinations = REDTAG_TOP_DESTINATIONS
        gateway_count = 0

        for gateway_code, gateway_city in REDTAG_GATEWAYS.items():
            gateway_count += 1
            logger.info(
                "=== Gateway %d/%d: %s (%s) ===",
                gateway_count, len(REDTAG_GATEWAYS), gateway_city, gateway_code,
            )

            for dest_id, dest_name in destinations.items():
                for search_date in search_dates:
                    logger.info(
                        "Searching %s → %s on %s",
                        gateway_code, dest_name, search_date,
                    )

                    # Create a fresh HTTP session for each search
                    http_session = requests.Session()

                    # Step 1: Create session
                    session_id = create_search_session(
                        http_session, gateway_code, dest_id, search_date
                    )
                    if not session_id:
                        cycle_errors.append({
                            "search": f"{gateway_code}→{dest_name} {search_date}",
                            "error": "Failed to create session",
                        })
                        time.sleep(random.uniform(5, 10))
                        continue

                    logger.debug("Got session ID: %s", session_id)

                    # Step 2: Fetch all pages of results
                    referer_url = _build_search_url(gateway_code, dest_id, search_date)
                    rows, extras = fetch_all_results(
                        http_session, session_id, search_date, referer_url
                    )
                    total_results += len(rows)

                    if not rows:
                        logger.debug("No results for %s → %s on %s", gateway_code, dest_name, search_date)
                        time.sleep(random.uniform(8, 15))
                        continue

                    logger.info("Got %d results for %s → %s on %s", len(rows), gateway_code, dest_name, search_date)

                    # Step 3: Parse results
                    for i, row in enumerate(rows):
                        deal_meta = parse_redtag_result(row, extras, gateway_code)
                        if not deal_meta:
                            continue

                        total_deals += 1

                        if dry_run:
                            print_deal(deal_meta, total_deals)
                        else:
                            # DB operations — only in live mode
                            try:
                                from app.db.session import get_db
                                with next(get_db()) as db:
                                    deal_obj = upsert_deal(db, deal_meta)
                                    if deal_obj:
                                        # Signal matching would go here
                                        pass
                            except Exception as e:
                                logger.error("Error upserting deal: %s", e)
                                cycle_errors.append({
                                    "search": f"{gateway_code}→{dest_name} {search_date}",
                                    "error": str(e),
                                })

                    # Rate limiting between searches
                    delay = random.uniform(8, 20)
                    logger.debug("Sleeping %.1fs before next search", delay)
                    time.sleep(delay)

            # Longer pause between airports
            if gateway_count < len(REDTAG_GATEWAYS):
                pause = random.uniform(120, 240)
                logger.info("Pausing %.0fs before next airport", pause)
                time.sleep(pause)

        completed_at = datetime.now(timezone.utc)
        elapsed = (completed_at - started_at).total_seconds()
        logger.info(
            "Cycle complete. Results: %d, Parsed deals: %d, Errors: %d, Elapsed: %.0fs (%.1fh)",
            total_results, total_deals, len(cycle_errors), elapsed, elapsed / 3600,
        )

        if cycle_errors:
            logger.warning("Cycle errors: %s", json.dumps(cycle_errors[:20], indent=2))

        if not dry_run:
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/collection-complete", json={
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "total_deals": total_deals,
                    "total_matches": 0,  # TODO: implement matching
                    "error_count": len(cycle_errors),
                    "errors": cycle_errors[:50],
                    "deals_deactivated": 0,  # TODO: implement deactivation
                    "provider": "redtag",
                    "status": "completed",
                }, timeout=5)
            except Exception as e:
                logger.warning("Failed to post collection-complete: %s", e)

        if once:
            return

        # Sleep 24 hours with jitter
        jitter = random.randint(-3600, 3600)
        sleep_seconds = 24 * 60 * 60 + jitter
        logger.info("Sleeping %.1f hours before next cycle", sleep_seconds / 3600)

        if not dry_run:
            next_scan_at = completed_at.timestamp() + sleep_seconds
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/next-scan", json={
                    "next_scan_at": next_scan_at,
                    "last_scan_at": completed_at.timestamp(),
                    "provider": "redtag",
                }, timeout=5)
            except Exception as e:
                logger.warning("Failed to post next_scan: %s", e)

        time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RedTag.ca vacation package scraper")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print results without DB writes")
    parser.add_argument(
        "--test-session", action="store_true",
        help="Test session creation for a single search and print debug info",
    )
    args = parser.parse_args()

    if args.test_session:
        # Quick test: try to create one session and fetch one page
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

        gateway = "YQR"
        dest_id = 9  # Puerto Vallarta
        test_date = _generate_search_dates(2)[0]

        print(f"\nTesting session creation: {gateway} → Puerto Vallarta on {test_date}")
        print(f"Search URL: {_build_search_url(gateway, dest_id, test_date)}\n")

        http_session = requests.Session()
        sid = create_search_session(http_session, gateway, dest_id, test_date)

        if sid:
            print(f"Session ID: {sid}")
            print(f"\nCookies: {dict(http_session.cookies)}")
            print(f"\nFetching results page 0...")

            referer_url = _build_search_url(gateway, dest_id, test_date)
            data, rows = fetch_results_page(http_session, sid, test_date, referer_url)

            print(f"Got {len(rows)} results")
            if rows:
                extras = data.get("packageResults", {}).get("extras", {})
                print(f"\nPagination: {data.get('packageResults', {}).get('pagination', {})}")
                print(f"\nFirst result (raw):")
                print(json.dumps(rows[0], indent=2)[:2000])
                print(f"\nParsed:")
                deal = parse_redtag_result(rows[0], extras, gateway)
                if deal:
                    print_deal(deal, 1)
                    print(f"\nExtras: {json.dumps(deal.get('extras'), indent=2)}")
                else:
                    print("  (failed to parse)")
            elif data:
                print(f"\nResponse keys: {list(data.keys())}")
                print(f"Session data: {data.get('session', {})}")
                print(f"Raw response (first 2000 chars):")
                print(json.dumps(data, indent=2)[:2000])
            else:
                print("Empty response")
        else:
            print("FAILED to create session")
            print(f"\nCookies: {dict(http_session.cookies)}")

            # Dump the raw HTML for debugging
            url = _build_search_url(gateway, dest_id, test_date)
            print(f"\nRe-fetching search page for raw HTML inspection...")
            resp = requests.get(url, headers=_get_headers(), timeout=30)
            print(f"Status: {resp.status_code}")
            print(f"Response headers: {dict(resp.headers)}")
            print(f"\nHTML body (first 3000 chars):")
            print(resp.text[:3000])
    else:
        run_scraper(once=args.once or args.dry_run, dry_run=args.dry_run)
