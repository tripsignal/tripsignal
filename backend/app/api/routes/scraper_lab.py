"""Scraper Lab — test and diagnostic endpoints for the SellOff scraper."""
import os
import re
import random
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.signal import Signal
from app.db.models.deal import Deal

router = APIRouter(prefix="/admin/scraper-lab", tags=["scraper-lab"])

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

CATEGORIES = [
    "luxury-vacations",
    "adults-only",
    "family-vacations",
    "budget-friendly-vacations",
    "top-rated-all-inclusive-resorts",
]

GATEWAY_SLUGS = {
    "YXX": "abbotsford", "YVR": "vancouver", "YYJ": "victoria",
    "YLW": "kelowna", "YKA": "kamloops", "YXS": "prince-george",
    "YYC": "calgary", "YEG": "edmonton", "YMM": "fort-mcmurray",
    "YQU": "grande-prairie", "YQL": "lethbridge", "YQR": "regina",
    "YXE": "saskatoon", "YWG": "winnipeg", "YYZ": "toronto",
    "YHM": "hamilton", "YKF": "kitchener", "YXU": "london",
    "YQT": "thunder-bay", "YOW": "ottawa", "YQG": "windsor",
    "YUL": "montreal", "YQB": "quebec-city", "YBG": "bagotville",
    "YHZ": "halifax", "YDF": "deer-lake", "YQX": "gander",
    "YYT": "st-johns", "YQM": "moncton", "YFC": "fredericton",
    "YSJ": "saint-john", "YYG": "charlottetown", "YSB": "sudbury",
    "YAM": "sault-ste-marie",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

DESTINATION_REGION_MAP = {
    "riviera maya": "riviera_maya", "playa del carmen": "riviera_maya",
    "playa mujeres": "riviera_maya", "cozumel": "riviera_maya", "tulum": "riviera_maya",
    "cancun": "cancun", "cancún": "cancun",
    "puerto vallarta": "puerto_vallarta", "riviera nayarit": "puerto_vallarta",
    "nuevo vallarta": "puerto_vallarta",
    "los cabos": "los_cabos", "cabo san lucas": "los_cabos",
    "mazatlan": "mazatlan", "mazatlán": "mazatlan",
    "huatulco": "huatulco",
    "ixtapa": "ixtapa", "zihuatanejo": "ixtapa",
    "puerto escondido": "puerto_escondido",
    "mexico": "mexico", "acapulco": "mexico",
    "dominican republic": "dominican_republic",
    "punta cana": "punta_cana",
    "puerto plata": "puerto_plata",
    "la romana": "la_romana",
    "samana": "samana", "samaná": "samana",
    "santo domingo": "santo_domingo",
    "cuba": "cuba",
    "varadero": "varadero",
    "holguin": "holguin", "holguín": "holguin",
    "havana": "havana",
    "cayo coco": "cayo_coco", "cayo santa maria": "cuba",
    "jamaica": "jamaica",
    "montego bay": "montego_bay",
    "negril": "negril",
    "ocho rios": "ocho_rios", "runaway bay": "jamaica",
    "aruba": "aruba",
    "barbados": "barbados",
    "curacao": "curacao", "curaçao": "curacao",
    "cayman islands": "cayman_islands",
    "saint lucia": "saint_lucia", "st lucia": "saint_lucia", "st. lucia": "saint_lucia",
    "st maarten": "st_maarten",
    "turks and caicos": "turks_caicos",
    "bahamas": "bahamas", "nassau": "bahamas",
    "antigua": "antigua",
    "grenada": "grenada",
    "costa rica": "costa_rica", "liberia": "costa_rica",
    "belize": "belize",
    "panama": "panama",
    "roatan": "roatan", "roatán": "roatan",
    "playa blanca": "central_america",
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


def verify_admin(x_admin_token: str | None):
    token = os.getenv("ADMIN_TOKEN", "").strip()
    if not token or not x_admin_token or x_admin_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def map_region(destination: str) -> Optional[str]:
    dest_lower = destination.lower()
    for keyword, region in DESTINATION_REGION_MAP.items():
        if keyword in dest_lower:
            return region
    return None


def clean_url(url: str) -> str:
    return url.replace("&amp;", "&")


def fetch_html(url: str) -> tuple[str, str]:
    """Fetch HTML from URL. Returns (html, error_message)."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
            "Referer": "https://www.selloffvacations.com/",
        })
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        return html, ""
    except urllib.error.HTTPError as e:
        return "", f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return "", str(e)


def run_regexes(html: str) -> dict:
    """Run all scraper regexes against HTML and return counts + samples."""
    regexes = {
        "destinations": (r'adModuleHeading--\w+\">([^<]+)</h2>', "Destination headings"),
        "hotels": (r'adModuleSubheading--\w+\">([^<]+)</p>', "Hotel names"),
        "dates": (r'adModuleDetailsDays--\w+\"><span>([^<]+)</span>', "Departure dates"),
        "prices": (r'adModuleDetailsAmount--\w+\">[$](\d+)<', "Prices"),
        "discounts": (r'Save up to (\d+)%', "Discounts"),
        "links": (r'href=\"(https://shopping\.selloffvacations\.com/cgi-bin/handler\.cgi\?[^\"]+)\"', "Booking links"),
        "stars": (r'StarRating-module--rating--\w+\" rating=\"([\d.]+)\"', "Star ratings"),
    }
    results = {}
    for key, (pattern, label) in regexes.items():
        matches = re.findall(pattern, html)
        results[key] = {
            "label": label,
            "count": len(matches),
            "samples": matches[:3],
            "ok": len(matches) > 0,
        }
    return results


def parse_deals_from_html(html: str) -> list[dict]:
    """Parse deals from HTML. Same logic as scraper but returns dicts, no DB."""
    destinations = re.findall(r'adModuleHeading--\w+\">([^<]+)</h2>', html)
    hotels = re.findall(r'adModuleSubheading--\w+\">([^<]+)</p>', html)
    dates = re.findall(r'adModuleDetailsDays--\w+\"><span>([^<]+)</span>', html)
    prices = re.findall(r'adModuleDetailsAmount--\w+\">[$](\d+)<', html)
    discounts = re.findall(r'Save up to (\d+)%', html)
    links = re.findall(r'href=\"(https://shopping\.selloffvacations\.com/cgi-bin/handler\.cgi\?[^\"]+)\"', html)
    stars = re.findall(r'StarRating-module--rating--\w+\" rating=\"([\d.]+)\"', html)

    deals = []
    for i in range(len(prices)):
        try:
            link = links[i] if i < len(links) else ""
            clean_link = clean_url(link)
            gateway_match = re.search(r'gateway_dep=([A-Z]+)', clean_link)
            hotel_match = re.search(r'no_hotel=(\d+)', clean_link)
            date_match = re.search(r'date_dep=(\d+)', clean_link)
            duration_match = re.search(r'duration=([A-Z0-9]+)', clean_link)

            gateway = gateway_match.group(1) if gateway_match else ""
            hotel_id = hotel_match.group(1) if hotel_match else str(i)
            depart_date_str = date_match.group(1) if date_match else (dates[i] if i < len(dates) else "")
            duration_str = duration_match.group(1) if duration_match else "7DAYS"

            # Parse date
            depart_date = None
            for fmt in ("%Y%m%d", "%b %d, %Y", "%B %d, %Y"):
                try:
                    depart_date = datetime.strptime(depart_date_str.strip(), fmt).date()
                    break
                except ValueError:
                    continue
            if not depart_date:
                continue

            duration_days_match = re.search(r"(\d+)", duration_str)
            duration_days = int(duration_days_match.group(1)) if duration_days_match else 7

            destination_str = destinations[i].strip() if i < len(destinations) else ""
            region = map_region(destination_str)

            deals.append({
                "index": i + 1,
                "gateway": gateway,
                "destination_str": destination_str,
                "region": region or "unknown",
                "hotel_name": hotels[i].replace("&amp;", "&").strip() if i < len(hotels) else "",
                "hotel_id": hotel_id,
                "depart_date": depart_date.isoformat(),
                "duration_days": duration_days,
                "price_cad": int(prices[i]),
                "discount_pct": int(discounts[i]) if i < len(discounts) else 0,
                "star_rating": float(stars[i]) if i < len(stars) else None,
                "deeplink_url": clean_link,
                "dedupe_key": f"selloff:{gateway}:{hotel_id}:{depart_date}:{duration_days}",
            })
        except Exception as e:
            continue

    return deals


def simulate_db_actions(deals: list[dict], db: Session) -> list[dict]:
    """For each deal, check if it would be inserted, updated, or skipped."""
    results = []
    for deal in deals:
        existing = db.execute(
            select(Deal).where(Deal.dedupe_key == deal["dedupe_key"])
        ).scalar_one_or_none()

        if existing:
            if existing.price_cents != deal["price_cad"] * 100:
                action = "update"
                action_detail = f"price ${existing.price_cents // 100} → ${deal['price_cad']}"
            else:
                action = "skip"
                action_detail = "already exists, no price change"
        else:
            action = "insert"
            action_detail = "new deal"

        results.append({**deal, "db_action": action, "db_action_detail": action_detail})
    return results


def simulate_signal_matches(deals: list[dict], db: Session) -> list[dict]:
    """For each deal, find which active signals would match it."""
    signals = db.execute(select(Signal).where(Signal.status == "active")).scalars().all()
    results = []

    for deal in deals:
        matched = []
        depart_date = datetime.strptime(deal["depart_date"], "%Y-%m-%d").date()

        for signal in signals:
            try:
                config = signal.config
                if deal["gateway"] not in signal.departure_airports:
                    continue
                if not deal_matches_signal_region(deal["region"], signal.destination_regions):
                    continue

                travel_window = config.get("travel_window", {})
                start_date_str = travel_window.get("start_date")
                end_date_str = travel_window.get("end_date")
                if start_date_str and end_date_str:
                    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                    if not (start_dt <= depart_date <= end_dt):
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
                        if not (start_month <= depart_date <= end_month):
                            continue

                min_nights = travel_window.get("min_nights")
                max_nights = travel_window.get("max_nights")
                if min_nights and deal["duration_days"] < min_nights:
                    continue
                if max_nights and deal["duration_days"] > max_nights:
                    continue

                budget = config.get("budget", {})
                target_pp = budget.get("target_pp")
                travellers = config.get("travellers", {})
                adults = travellers.get("adults", 2)
                if target_pp:
                    total_budget = int(target_pp) * adults
                    if deal["price_cad"] > total_budget:
                        continue

                matched.append({"signal_id": str(signal.id), "signal_name": signal.name})
            except Exception:
                continue

        results.append({**deal, "signal_matches": matched, "match_count": len(matched)})
    return results


# ── Endpoints ──────────────────────────────────────────────────────────────

class HealthCheckRequest(BaseModel):
    url: str = "https://www.selloffvacations.com/en/vacation-packages/luxury-vacations/from-toronto"


class TestScrapeRequest(BaseModel):
    url: str


class DryRunRequest(BaseModel):
    category: str
    gateway: str


@router.post("/health-check")
def health_check(
    payload: HealthCheckRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    html, error = fetch_html(payload.url)
    if error:
        return {"ok": False, "error": error, "url": payload.url, "regexes": {}}

    regexes = run_regexes(html)
    all_ok = all(r["ok"] for r in regexes.values())
    consistent = True  # counts vary by field, not used for ok status
    prices_count = regexes.get("prices", {}).get("count", 0)

    return {
        "ok": all_ok,
        "url": payload.url,
        "html_size_kb": round(len(html) / 1024, 1),
        "deals_expected": prices_count,
        "regexes": regexes,
        "error": "",
    }


@router.post("/test-scrape")
def test_scrape(
    payload: TestScrapeRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)
    html, error = fetch_html(payload.url)
    if error:
        return {"ok": False, "error": error, "deals": [], "count": 0}

    deals = parse_deals_from_html(html)
    return {
        "ok": True,
        "url": payload.url,
        "count": len(deals),
        "html_size_kb": round(len(html) / 1024, 1),
        "deals": deals,
        "error": "",
    }


@router.post("/dry-run")
def dry_run(
    payload: DryRunRequest,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    verify_admin(x_admin_token)

    if payload.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category. Choose from: {CATEGORIES}")
    if payload.gateway not in GATEWAY_SLUGS:
        raise HTTPException(status_code=400, detail=f"Invalid gateway. Choose from: {list(GATEWAY_SLUGS.keys())}")

    city_slug = GATEWAY_SLUGS[payload.gateway]
    url = f"https://www.selloffvacations.com/en/vacation-packages/{payload.category}/from-{city_slug}"

    html, error = fetch_html(url)
    if error:
        return {"ok": False, "error": error, "url": url, "deals": [], "count": 0}

    deals = parse_deals_from_html(html)
    deals_with_actions = simulate_db_actions(deals, db)
    deals_with_matches = simulate_signal_matches(deals_with_actions, db)

    inserts = sum(1 for d in deals_with_matches if d["db_action"] == "insert")
    updates = sum(1 for d in deals_with_matches if d["db_action"] == "update")
    skips = sum(1 for d in deals_with_matches if d["db_action"] == "skip")
    total_signal_matches = sum(d["match_count"] for d in deals_with_matches)

    return {
        "ok": True,
        "url": url,
        "count": len(deals_with_matches),
        "summary": {
            "would_insert": inserts,
            "would_update": updates,
            "would_skip": skips,
            "signal_matches": total_signal_matches,
        },
        "deals": deals_with_matches,
        "error": "",
    }
