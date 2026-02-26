"""SellOff Vacations scraper and signal matcher."""
import logging
import os
import re
import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import urllib.request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.user import User
from app.db.session import get_db

logger = logging.getLogger("selloff_scraper")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SCRAPE_DELAY_SECONDS = float(os.getenv("SCRAPE_DELAY_SECONDS", "10"))

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
    "YQQ": "comox",
    "YNB": "nanaimo",
}

DESTINATION_REGION_MAP = {
    "mexico": "mexico",
    "riviera maya": "mexico",
    "cancun": "mexico",
    "puerto vallarta": "mexico",
    "los cabos": "mexico",
    "mazatlan": "mexico",
    "mazatlán": "mexico",
    "huatulco": "mexico",
    "ixtapa": "mexico",
    "puerto escondido": "mexico",
    "dominican republic": "dominican_republic",
    "punta cana": "dominican_republic",
    "puerto plata": "dominican_republic",
    "la romana": "dominican_republic",
    "samana": "dominican_republic",
    "samaná": "dominican_republic",
    "cuba": "cuba",
    "varadero": "cuba",
    "holguin": "cuba",
    "holguín": "cuba",
    "havana": "cuba",
    "cayo coco": "cuba",
    "santa clara": "cuba",
    "jamaica": "jamaica",
    "montego bay": "jamaica",
    "negril": "jamaica",
    "ocho rios": "jamaica",
    "aruba": "caribbean",
    "barbados": "caribbean",
    "curacao": "caribbean",
    "curaçao": "caribbean",
    "cayman islands": "caribbean",
    "saint lucia": "caribbean",
    "st. lucia": "caribbean",
    "st maarten": "caribbean",
    "st. maarten": "caribbean",
    "turks and caicos": "caribbean",
    "bahamas": "caribbean",
    "nassau": "caribbean",
    "costa rica": "central_america",
    "liberia": "central_america",
    "belize": "central_america",
    "panama": "central_america",
    "roatan": "central_america",
    "roatán": "central_america",
    "honduras": "central_america",
}


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
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return []

    destinations = re.findall(r'adModuleHeading--\w+\">([^<]+)</h2>', html)
    hotels = re.findall(r'adModuleSubheading--\w+\">([^<]+)</p>', html)
    dates = re.findall(r'adModuleDetailsDays--\w+\"><span>([^<]+)</span>', html)
    prices = re.findall(r'adModuleDetailsAmount--\w+\">\$?(\d+)<', html)
    discounts = re.findall(r'Save up to (\d+)%', html)
    links = re.findall(r'href=\"(https://shopping\.selloffvacations\.com/cgi-bin/handler\.cgi\?[^\"]+)\"', html)

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
            region = map_destination_to_region(destination_str)

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
        if existing.price_cents != deal["price_cents"]:
            existing.price_cents = deal["price_cents"]
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
    )
    db.add(new_deal)
    db.commit()
    db.refresh(new_deal)
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
            if deal_meta["region"] not in signal.destination_regions:
                continue

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

            target_pp = budget.get("target_pp")
            adults = travellers.get("adults", 2)
            strict = budget.get("strict", False)
            if target_pp and strict:
                total_budget_cents = int(target_pp) * adults * 100
                if deal.price_cents > total_budget_cents:
                    continue

            matches.append(signal)
        except Exception as e:
            logger.warning("Error matching signal %s: %s", signal.id, e)
            continue

    return matches


def send_alert_email(user_email: str, signal: Signal, deal: Deal, deal_meta: dict) -> None:
    if not RESEND_API_KEY:
        logger.warning("No RESEND_API_KEY set, skipping email")
        return

    config = signal.config
    adults = config.get("travellers", {}).get("adults", 2)
    price_pp = deal.price_cents // 100 // adults
    total_price = deal.price_cents // 100

    subject = f"Deal alert: {deal_meta['destination_str']} from ${total_price} — {deal_meta['hotel_name']}"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #111; background: #fff; max-width: 560px; margin: 0 auto; padding: 40px 24px;">

  <div style="margin-bottom: 24px;">
    <span style="font-size: 20px; font-weight: 600; letter-spacing: -0.3px;">TripSignal</span>
  </div>

  <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;">
    <p style="margin: 0; font-size: 13px; color: #15803d; font-weight: 500;">Deal match found for signal: {signal.name}</p>
  </div>

  <h1 style="font-size: 22px; font-weight: 600; margin: 0 0 4px;">{deal_meta['hotel_name']}</h1>
  <p style="font-size: 15px; color: #666; margin: 0 0 24px;">{deal_meta['destination_str']}</p>

  <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
    <tr>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; color: #666;">Departure</td>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; font-weight: 500; text-align: right;">{deal.depart_date.strftime('%B %d, %Y')}</td>
    </tr>
    <tr>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; color: #666;">Duration</td>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; font-weight: 500; text-align: right;">{deal_meta['duration_days']} nights</td>
    </tr>
    <tr>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; color: #666;">Travellers</td>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; font-weight: 500; text-align: right;">{adults} adults</td>
    </tr>
    <tr>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; color: #666;">Price per person</td>
      <td style="padding: 10px 0; border-bottom: 1px solid #eee; font-size: 14px; font-weight: 500; text-align: right;">${price_pp:,} CAD</td>
    </tr>
    <tr>
      <td style="padding: 10px 0; font-size: 16px; font-weight: 600;">Total price</td>
      <td style="padding: 10px 0; font-size: 20px; font-weight: 700; text-align: right; color: #111;">${total_price:,} CAD</td>
    </tr>
  </table>

  {f'<div style="margin-bottom: 24px;"><span style="background: #bef564; border-radius: 20px; padding: 4px 10px; font-size: 13px; font-weight: 600; color: #0f2541;">Save up to {deal_meta["discount_pct"]}%</span></div>' if deal_meta.get('discount_pct') else ''}

  <a href="{deal.deeplink_url}" style="display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-size: 14px; font-weight: 500; margin-bottom: 32px;">
    View this deal →
  </a>

  <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">

  <p style="font-size: 12px; color: #999; margin: 0;">
    You're receiving this because your TripSignal signal "{signal.name}" matched a deal.<br>
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
                "from": "TripSignal <hello@tripsignal.ca>",
                "to": user_email,
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Alert email sent to %s for deal %s", user_email, deal.id)
    except Exception as e:
        logger.error("Failed to send alert email to %s: %s", user_email, e)


def run_matching_only(db: Session) -> None:
    """Run signal matching against all existing deals without scraping."""
    logger.info("Running match-only mode against existing deals")

    deals = db.execute(select(Deal)).scalars().all()
    logger.info("Matching %d deals against active signals", len(deals))

    total_matches = 0
    for deal in deals:
        duration_days = (deal.return_date - deal.depart_date).days if deal.return_date else 7
        deal_meta = {
            "gateway": deal.origin,
            "region": deal.destination,
            "destination_str": deal.destination,
            "hotel_name": deal.deeplink_url or "",
            "duration_days": duration_days,
            "discount_pct": 0,
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
            logger.info("Match: %s → %s %s $%d", signal.name, deal.destination, deal.depart_date, deal.price_cents // 100)

            user_email = signal.config.get("notifications", {}).get("email", "")
            send_alert_email(user_email, signal, deal, deal_meta)

    logger.info("Match-only complete. New matches: %d", total_matches)


def run_scraper(once: bool = True) -> None:
    logger.info("SellOff scraper starting")

    while True:
        total_deals = 0
        total_matches = 0

        for category in CATEGORIES:
            for gateway_code, city_slug in GATEWAY_SLUGS.items():
                url = f"https://www.selloffvacations.com/en/vacation-packages/{category}/from-{city_slug}"
                logger.info("Scraping %s", url)

                deals = fetch_deals_from_page(url)
                logger.info("Found %d deals on %s", len(deals), url)

                with next(get_db()) as db:
                    for deal_meta in deals:
                        try:
                            deal = upsert_deal(db, deal_meta)
                            if not deal:
                                continue

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
                                logger.info("Match: %s → %s %s $%d", signal.name, deal.destination, deal.depart_date, deal.price_cents // 100)

                                user_email = signal.config.get("notifications", {}).get("email", "")
                                send_alert_email(user_email, signal, deal, deal_meta)

                        except Exception as e:
                            logger.error("Error processing deal: %s", e)
                            continue

                time.sleep(SCRAPE_DELAY_SECONDS)

        logger.info("Scrape complete. Deals: %d, Matches: %d", total_deals, total_matches)

        if once:
            return

        logger.info("Sleeping 4 hours before next scrape")
        time.sleep(4 * 60 * 60)


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