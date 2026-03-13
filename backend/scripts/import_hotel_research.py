"""Import hotel_research_results.json into hotel_intel table.

Usage:
    docker exec tripsignal-api python3 -m scripts.import_hotel_research
"""

import json
import os
import re
import sys

from sqlalchemy import create_engine, text

# Add parent to path so we can import enrichment module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.enrichment.normalize import normalize_destination, normalize_hotel_name


POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "tripsignal")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(SCRIPT_DIR, "hotel_research_results.json")


def make_record_id(hotel_name: str, destination: str) -> str:
    """Generate stable record_id from hotel name + destination."""
    raw = f"{hotel_name}__{destination}".lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", raw)
    slug = slug.strip("_")
    return slug


# Fields that map directly from JSON key -> DB column
FIELD_MAP = {
    "hotel_name": "hotel_name",
    "destination": "destination",
    "star_rating": "star_rating",
    "official_website": "official_website",
    "resort_chain": "resort_chain",
    "loyalty_program": "loyalty_program",
    "total_rooms": "total_rooms",
    "resort_size": "resort_size",
    "resort_layout": "resort_layout",
    "last_renovation_year": "last_renovation_year",
    "primary_demographics": "primary_demographics",
    "vibe": "vibe",
    "adults_only": "adults_only",
    "accommodates_5": "accommodates_5",
    "max_occupancy_standard_room": "max_occupancy_standard_room",
    "max_occupancy_any_room": "max_occupancy_any_room",
    "room_fit_for_5_type": "room_fit_for_5_type",
    "room_types_for_5": "room_types_for_5",
    "connecting_rooms_available": "connecting_rooms_available",
    "cribs_available": "cribs_available",
    "rollaway_beds": "rollaway_beds",
    "kids_club": "kids_club",
    "kids_club_ages": "kids_club_ages",
    "kids_club_hours": "kids_club_hours",
    "teen_club": "teen_club",
    "teen_club_ages": "teen_club_ages",
    "babysitting_available": "babysitting_available",
    "waterpark": "waterpark",
    "waterpark_notes": "waterpark_notes",
    "kids_pool": "kids_pool",
    "num_restaurants": "num_restaurants",
    "restaurant_names": "restaurant_names",
    "cuisine_types": "cuisine_types",
    "num_bars": "num_bars",
    "buffet_available": "buffet_available",
    "room_service_24h": "room_service_24h",
    "food_quality_notes": "food_quality_notes",
    "beach_access": "beach_access",
    "beach_type": "beach_type",
    "beach_description": "beach_description",
    "sargassum_risk": "sargassum_risk",
    "sargassum_notes": "sargassum_notes",
    "pool_count": "pool_count",
    "pool_types": "pool_types",
    "nearest_airport_code": "nearest_airport_code",
    "transfer_time_minutes": "transfer_time_minutes",
    "airport_transfer_included": "airport_transfer_included",
    "surrounding_area": "surrounding_area",
    "tripadvisor_rating": "tripadvisor_rating",
    "tripadvisor_review_count": "tripadvisor_review_count",
    "google_rating": "google_rating",
    "google_review_count": "google_review_count",
    "top_complaints": "top_complaints",
    "top_praise": "top_praise",
    "red_flags": "red_flags",
    "best_time_to_visit": "best_time_to_visit",
    "field_confidence": "field_confidence",
    "source_urls": "source_urls",
}

# Columns that are stored as JSONB
JSONB_COLUMNS = {
    "room_types_for_5", "restaurant_names", "cuisine_types", "pool_types",
    "top_complaints", "top_praise", "red_flags", "field_confidence", "source_urls",
}

# Columns that are INTEGER in the database
INTEGER_COLUMNS = {
    "total_rooms", "num_restaurants", "transfer_time_minutes",
    "max_occupancy_standard_room", "max_occupancy_any_room",
    "last_renovation_year", "num_bars", "pool_count",
    "tripadvisor_review_count", "google_review_count",
}

# Columns that are NUMERIC in the database
NUMERIC_COLUMNS = {
    "star_rating", "tripadvisor_rating", "google_rating",
}


def safe_int(value):
    """Extract an integer from a value that might be a range like '45-65'."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        # Try direct parse first
        try:
            return int(value)
        except ValueError:
            pass
        # Extract first number from ranges like "45-65" or "~50"
        match = re.search(r"(\d+)", value)
        if match:
            return int(match.group(1))
    return None


def safe_numeric(value):
    """Extract a numeric value."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            match = re.search(r"(\d+\.?\d*)", value)
            if match:
                return float(match.group(1))
    return None


def coerce_value(col_name, value):
    """Coerce a JSON value to the appropriate DB type."""
    if value is None or value == "" or value == "N/A" or value == "Unknown":
        return None

    if col_name in JSONB_COLUMNS:
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        return None

    if col_name in INTEGER_COLUMNS:
        return safe_int(value)

    if col_name in NUMERIC_COLUMNS:
        return safe_numeric(value)

    # Boolean coercion
    if isinstance(value, str) and value.lower() in ("true", "yes"):
        return True
    if isinstance(value, str) and value.lower() in ("false", "no"):
        return False

    return value


def main():
    print(f"Loading {JSON_PATH}...")
    with open(JSON_PATH) as f:
        records = json.load(f)
    print(f"Loaded {len(records)} records")

    engine = create_engine(DATABASE_URL)
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0
    link_matches = 0

    with engine.connect() as conn:
        for i, rec in enumerate(records):
            hotel_name = rec.get("hotel_name", "").strip()
            destination = rec.get("destination", "").strip()

            if not hotel_name:
                print(f"  [{i}] SKIP: no hotel_name")
                skipped += 1
                continue

            # Normalize destination
            norm_dest = normalize_destination(destination) if destination else destination
            record_id = make_record_id(hotel_name, norm_dest or destination)

            # Build column values
            col_values = {}
            for json_key, db_col in FIELD_MAP.items():
                raw = rec.get(json_key)
                col_values[db_col] = coerce_value(db_col, raw)

            # Normalize destination for storage
            if norm_dest and norm_dest != destination.lower().strip():
                col_values["destination"] = norm_dest.title()

            col_values["record_id"] = record_id
            col_values["full_data"] = json.dumps(rec)
            col_values["source"] = "gemini"

            # Set researched_at from the JSON if available
            researched_at = rec.get("_researched_at")
            if researched_at:
                col_values["researched_at"] = researched_at

            try:
                # Use a savepoint so one failure doesn't abort the whole batch
                nested = conn.begin_nested()

                # Check for existing row by hotel_name (case-insensitive)
                existing = conn.execute(
                    text("SELECT hotel_id FROM hotel_intel WHERE LOWER(hotel_name) = LOWER(:name) LIMIT 1"),
                    {"name": hotel_name},
                ).fetchone()

                if existing:
                    # UPDATE existing row
                    hotel_id = existing[0]
                    set_clauses = []
                    params = {"hid": hotel_id}
                    for col, val in col_values.items():
                        if col == "hotel_name":
                            continue
                        param_name = f"p_{col}"
                        set_clauses.append(f"{col} = :{param_name}")
                        params[param_name] = val
                    set_clauses.append("updated_at = NOW()")

                    sql = f"UPDATE hotel_intel SET {', '.join(set_clauses)} WHERE hotel_id = :hid"
                    conn.execute(text(sql), params)
                    updated += 1
                    action = "UPD"
                else:
                    # INSERT new row - use record_id as hotel_id
                    hotel_id = record_id
                    col_values["hotel_id"] = hotel_id

                    cols = list(col_values.keys())
                    param_names = [f":p_{c}" for c in cols]
                    params = {f"p_{c}": v for c, v in col_values.items()}

                    sql = f"INSERT INTO hotel_intel ({', '.join(cols)}) VALUES ({', '.join(param_names)})"
                    conn.execute(text(sql), params)
                    inserted += 1
                    action = "INS"

                # Try to find matching hotel_links row
                link_match = conn.execute(
                    text("SELECT hotel_id FROM hotel_links WHERE LOWER(hotel_name) = LOWER(:name) LIMIT 1"),
                    {"name": hotel_name},
                ).fetchone()
                link_str = ""
                if link_match:
                    link_matches += 1
                    link_str = f" [LINK:{link_match[0]}]"

                nested.commit()

                if (i + 1) % 25 == 0 or i < 3:
                    print(f"  [{i+1}/{len(records)}] {action} {hotel_name[:40]}{link_str}")

            except Exception as e:
                nested.rollback()
                print(f"  [{i+1}] ERROR {hotel_name[:40]}: {e}")
                errors += 1

        # Commit the overall transaction
        conn.commit()

    print(f"\n=== Import Complete ===")
    print(f"  Inserted: {inserted}")
    print(f"  Updated:  {updated}")
    print(f"  Skipped:  {skipped}")
    print(f"  Errors:   {errors}")
    print(f"  Hotel Links matches: {link_matches}/{inserted + updated}")


if __name__ == "__main__":
    main()
