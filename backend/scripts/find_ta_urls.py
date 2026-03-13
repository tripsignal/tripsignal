"""Find TripAdvisor URLs for hotels missing them, via Startpage search.

Searches Startpage for 'tripadvisor [hotel name] [destination]' and
extracts the Hotel_Review URL from results. Updates the DB directly.

Usage (on server):
    docker exec -w /app/backend tripsignal-api python -m scripts.find_ta_urls [--dry-run] [--limit N]

Usage (locally via SSH):
    python3 backend/scripts/find_ta_urls.py --local [--dry-run] [--limit N]
"""

import argparse
import os
import re
import subprocess
import time
import random

import requests
from urllib.parse import quote

# Match TripAdvisor Hotel_Review URLs
TA_URL_RE = re.compile(
    r'(https?://(?:www\.)?tripadvisor\.(?:com|ca)/Hotel_Review-[^\s"\'<>&?#]+)',
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def search_startpage(query: str) -> str | None:
    """Search Startpage and return the first TripAdvisor Hotel_Review URL."""
    try:
        resp = requests.post(
            "https://www.startpage.com/sp/search",
            data={"query": query},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        matches = TA_URL_RE.findall(resp.text)
        if matches:
            url = matches[0]
            # Normalize to www.tripadvisor.com
            url = re.sub(r"tripadvisor\.ca", "tripadvisor.com", url)
            return url
    except Exception as e:
        print(f"  Search error: {e}")
    return None


def get_hotels_from_db():
    """Get hotels without TA URLs directly from SQLAlchemy."""
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.db.models.hotel_link import HotelLink

    db = SessionLocal()
    try:
        query = (
            select(HotelLink)
            .where(HotelLink.tripadvisor_url.is_(None))
            .order_by(HotelLink.hotel_name)
        )
        return db.execute(query).scalars().all(), db
    except Exception:
        db.close()
        raise


def get_hotels_via_ssh():
    """Get hotels without TA URLs via SSH to server DB."""
    sql = "SELECT hotel_id, hotel_name, destination FROM hotel_links WHERE tripadvisor_url IS NULL ORDER BY hotel_name"
    cmd = f'ssh -i ~/.ssh/id_ed25519 -p 41922 trent@77.42.26.197 "sudo docker exec tripsignal-postgres psql -U postgres -d tripsignal -t -A -F \'|\' -c \\"{sql}\\""'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)

    hotels = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            hotels.append({
                "hotel_id": parts[0].strip(),
                "hotel_name": parts[1].strip(),
                "destination": parts[2].strip() if len(parts) > 2 else "",
            })
    return hotels


_SAFE_URL_RE = re.compile(r'^https?://(?:www\.)?tripadvisor\.(?:com|ca)/Hotel_Review-[\w\-\.]+$')
_SAFE_ID_RE = re.compile(r'^[\w\-]+$')


def update_hotel_via_ssh(hotel_id: str, url: str) -> bool:
    """Update hotel URL in DB via SSH using parameterized psql query."""
    # Validate inputs to prevent injection through shell + psql layers
    if not _SAFE_URL_RE.match(url):
        print(f"  Rejected URL (failed validation): {url}")
        return False
    if not _SAFE_ID_RE.match(hotel_id):
        print(f"  Rejected hotel_id (failed validation): {hotel_id}")
        return False

    # Use psql -v to pass parameters safely, avoiding shell interpolation
    sql = "UPDATE hotel_links SET tripadvisor_url = :'ta_url', updated_at = NOW() WHERE hotel_id = :'h_id'"
    cmd = [
        "ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519"),
        "-p", "41922", "trent@77.42.26.197",
        "sudo", "docker", "exec", "tripsignal-postgres",
        "psql", "-U", "postgres", "-d", "tripsignal",
        "-v", f"ta_url={url}",
        "-v", f"h_id={hotel_id}",
        "-c", sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return "UPDATE 1" in result.stdout


def main():
    parser = argparse.ArgumentParser(description="Find TripAdvisor URLs for hotels")
    parser.add_argument("--dry-run", action="store_true", help="Don't update DB")
    parser.add_argument("--limit", type=int, default=0, help="Max hotels to process")
    parser.add_argument("--local", action="store_true", help="Run locally, update DB via SSH")
    args = parser.parse_args()

    db = None

    if args.local:
        print("Running locally, will update DB via SSH...")
        hotels_raw = get_hotels_via_ssh()
        hotels = hotels_raw
    else:
        rows, db = get_hotels_from_db()
        hotels = [{"hotel_id": h.hotel_id, "hotel_name": h.hotel_name, "destination": h.destination or ""} for h in rows]

    if args.limit:
        hotels = hotels[:args.limit]

    print(f"Found {len(hotels)} hotels without TripAdvisor URLs\n")

    found = 0
    not_found = 0
    errors = 0

    for i, hotel in enumerate(hotels, 1):
        if isinstance(hotel, dict):
            hotel_id = hotel["hotel_id"]
            name = hotel["hotel_name"]
            dest = hotel.get("destination", "")
        else:
            hotel_id = hotel.hotel_id
            name = hotel.hotel_name
            dest = hotel.destination or ""

        query = f"tripadvisor {name} {dest} hotel".strip()
        print(f"[{i}/{len(hotels)}] {name} ({dest})")

        url = search_startpage(query)

        if url:
            print(f"  -> {url}")
            found += 1
            if not args.dry_run:
                if args.local:
                    if update_hotel_via_ssh(hotel_id, url):
                        print("  Saved")
                    else:
                        print("  DB update failed!")
                        errors += 1
                else:
                    hotel_obj = db.execute(
                        __import__("sqlalchemy").select(
                            __import__("app.db.models.hotel_link", fromlist=["HotelLink"]).HotelLink
                        ).where(
                            __import__("app.db.models.hotel_link", fromlist=["HotelLink"]).HotelLink.hotel_id == hotel_id
                        )
                    ).scalar_one()
                    hotel_obj.tripadvisor_url = url
                    db.commit()
                    print("  Saved")
        else:
            not_found += 1
            print("  Not found")

        # Rate limit: 3-6 seconds
        if i < len(hotels):
            delay = 3 + random.random() * 3
            time.sleep(delay)

    print(f"\nDone: {found} found, {not_found} not found, {errors} errors")
    if args.dry_run:
        print("(dry run — no DB changes made)")

    if db:
        db.close()


if __name__ == "__main__":
    main()
