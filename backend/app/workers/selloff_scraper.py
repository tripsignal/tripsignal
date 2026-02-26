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
from app.db.models.deal_price_history import DealPriceHistory
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
        if existing.price_cents != deal["price_cents"]:
            existing.price_cents = deal["price_cents"]
            db.commit()
        # Always record price history on every scrape
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
    # Record initial price history
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

            preferences = config.get("preferences", {})
            min_star_rating = preferences.get("min_star_rating")
            if min_star_rating and deal.star_rating is not None:
                if deal.star_rating < float(min_star_rating):
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


def send_digest_email(user_email: str, signal: Signal, new_deals: list) -> None:
    """Send a single digest email for a signal with all new matches."""
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
        seen_dedupe_keys: set[str] = set()

        for category in CATEGORIES:
            for gateway_code, city_slug in GATEWAY_SLUGS.items():
                url = f"https://www.selloffvacations.com/en/vacation-packages/{category}/from-{city_slug}"
                logger.info("Scraping %s", url)

                deals = fetch_deals_from_page(url)
                logger.info("Found %d deals on %s", len(deals), url)

                # Collect new matches per signal for digest emails
                signal_new_deals: dict = {}

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
                                logger.info("Match: %s → %s %s $%d", signal.name, deal.destination, deal.depart_date, deal.price_cents // 100)

                                # Collect for digest
                                key = signal.id
                                if key not in signal_new_deals:
                                    signal_new_deals[key] = {"signal": signal, "deals": [], "email": signal.config.get("notifications", {}).get("email", ""), "user": None}
                                signal_new_deals[key]["deals"].append(deal)

                        except Exception as e:
                            logger.error("Error processing deal: %s", e)
                            continue

                    # Send one digest email per signal
                    for key, data in signal_new_deals.items():
                        try:
                            user_email = data["email"]
                            signal = data["signal"]
                            new_deals = data["deals"]

                            if not user_email:
                                continue

                            user = db.execute(
                                select(User).where(User.email == user_email)
                            ).scalar_one_or_none()

                            is_pro = user and user.plan_type == "pro"
                            is_trial_active = user and user.plan_status == "active" and user.plan_type == "free"

                            if not is_pro and not is_trial_active:
                                logger.info("Skipping digest for expired/inactive user %s", user_email)
                                continue

                            if not is_pro:
                                # Free trial: max 1 digest per signal per 24 hours
                                recent = db.execute(
                                    select(DealMatch).where(
                                        DealMatch.signal_id == signal.id,
                                        DealMatch.matched_at >= datetime.now(timezone.utc) - timedelta(hours=24)
                                    ).order_by(DealMatch.matched_at.desc())
                                ).first()
                                if recent and recent.matched_at < datetime.now(timezone.utc) - timedelta(minutes=5):
                                    logger.info("Skipping digest for free user signal %s — already sent in last 24h", signal.id)
                                    continue

                            send_digest_email(user_email, signal, new_deals)

                        except Exception as e:
                            logger.error("Error sending digest for signal %s: %s", key, e)
                            continue

                time.sleep(SCRAPE_DELAY_SECONDS)

        # Mark deals inactive if not seen in this scrape run
        if seen_dedupe_keys:
            with next(get_db()) as db:
                stale = db.query(Deal).filter(
                    Deal.is_active == True,
                    Deal.dedupe_key.notin_(seen_dedupe_keys)
                ).all()
                for deal in stale:
                    deal.is_active = False
                db.commit()
                if stale:
                    logger.info("Marked %d deals inactive", len(stale))

        logger.info("Scrape complete. Deals: %d, Matches: %d", total_deals, total_matches)

        if once:
            return

        logger.info("Sleeping 6 hours before next scrape")
        time.sleep(6 * 60 * 60)


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
