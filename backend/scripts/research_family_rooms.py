#!/usr/bin/env python3
"""Research hotel intelligence data using Google Gemini with web search.

Queries Gemini (with Google Search grounding) for each hotel in hotels_input.csv
and saves structured JSON results. Resumable — skips already-researched hotels.

Usage:
    cd backend/scripts
    .venv/bin/python research_family_rooms.py [--limit N] [--dry-run]

Requires:
    GEMINI_API_KEY environment variable (or set in script below)
    pip install google-genai
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

# ── Configuration ──────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
INPUT_CSV = Path(__file__).parent / "hotels_input.csv"
OUTPUT_FILE = Path(__file__).parent / "hotel_research_results.json"
REQUESTS_PER_MINUTE = 10  # stay under 15 RPM paid limit
DELAY_SECONDS = 60 / REQUESTS_PER_MINUTE  # 6 seconds between requests


def build_prompt(hotel_name: str, destination: str, star_rating: str) -> str:
    """Build the research prompt for a single hotel."""
    return f"""You are a travel research assistant. Research the following hotel thoroughly using web search and return ONLY a valid JSON object (no markdown, no explanation, no extra text).

Hotel: {hotel_name}
Destination: {destination}
Star Rating: {star_rating}

Return this exact JSON structure. Use null for any field you cannot determine with confidence. Do not guess — if you're unsure, use null.

{{
  "hotel_name": "{hotel_name}",
  "destination": "{destination}",
  "star_rating": {star_rating if star_rating else "null"},

  "official_website": "full URL of the hotel's official website (not booking sites)",
  "instagram_handle": "@handle or null",
  "resort_chain": "parent brand/chain name or null if independent",
  "loyalty_program": "loyalty program name or null",

  "total_rooms": null,
  "resort_size": "small / medium / large / mega",
  "resort_layout": "brief description (e.g., 'single tower', 'low-rise village', 'multiple buildings')",
  "last_renovation_year": null,
  "renovation_notes": "what was renovated or null",

  "vibe": "family / adults-only / party / romantic / mixed",
  "primary_demographics": "who typically stays here (e.g., 'Canadian and European families', 'young couples')",
  "adults_only": false,

  "accommodates_5": true,
  "max_occupancy_standard_room": null,
  "max_occupancy_any_room": null,
  "room_fit_for_5_type": "standard_room / family_room / suite_required / connecting_rooms_required / not_possible / unclear",
  "room_types_for_5": ["list of specific room types that fit 5 people"],
  "connecting_rooms_available": true,
  "extra_person_fee_usd": null,
  "extra_person_fee_notes": "any details about extra person charges",
  "rollaway_beds": true,
  "cribs_available": true,
  "room_categories": ["list of room category names offered at this resort"],

  "kids_club": true,
  "kids_club_ages": "age range (e.g., '4-12')",
  "kids_club_hours": "operating hours or null",
  "teen_club": false,
  "teen_club_ages": "age range or null",
  "babysitting_available": false,
  "babysitting_notes": "details or null",
  "waterpark": false,
  "waterpark_notes": "description or null",
  "kids_pool": true,
  "kids_activities": ["list of kids activities"],
  "family_friendly_notes": "any other family-relevant details",

  "num_restaurants": null,
  "restaurant_names": ["list of restaurant names"],
  "cuisine_types": ["list of cuisine types available"],
  "buffet_available": true,
  "room_service_24h": false,
  "dietary_accommodations": ["gluten-free", "vegetarian", "vegan", "halal", "kosher"],
  "food_quality_notes": "general consensus on food quality from reviews",
  "num_bars": null,
  "bar_names": ["list of bar names"],

  "all_inclusive": true,
  "all_inclusive_includes": "what's included (drinks, activities, etc.)",
  "all_inclusive_exclusions": "what's NOT included (premium drinks, spa, etc.)",
  "resort_fee_usd": null,
  "tipping_policy": "tips included / tips expected / tips not accepted",
  "tipping_notes": "any details about tipping",

  "beach_access": true,
  "beach_type": "private / shared / public / no beach",
  "beach_description": "sand color, size, conditions",
  "sargassum_risk": "none / low / moderate / high / severe",
  "sargassum_notes": "seasonal details or null",
  "pool_count": null,
  "pool_types": ["infinity", "swim-up bar", "kids pool", "adult-only pool"],

  "spa": true,
  "gym": true,
  "golf": false,
  "casino": false,
  "watersports": ["list of available watersports"],
  "land_activities": ["list of land activities (tennis, archery, etc.)"],
  "entertainment": "description of evening entertainment",
  "excursions_offered": ["list of popular excursions from this resort"],

  "nearest_airport_code": "IATA code (e.g., 'PUJ')",
  "transfer_time_minutes": null,
  "airport_transfer_included": false,
  "airport_transfer_cost_usd": null,
  "surrounding_area": "brief description of what's nearby (town, shops, attractions)",

  "wifi_included": true,
  "wifi_quality_notes": "general consensus from reviews",
  "power_outlets": "US / European / both / varies",
  "voltage": "110V / 220V / both",
  "entry_requirements_notes": "visa or health requirements for Canadians or null",

  "tripadvisor_rating": null,
  "tripadvisor_review_count": null,
  "google_rating": null,
  "google_review_count": null,

  "top_complaints": ["list of 3-5 most common complaints from reviews"],
  "top_praise": ["list of 3-5 most commonly praised aspects"],
  "red_flags": ["any serious concerns: safety, health, construction, scams"],
  "best_time_to_visit": "best months or season",
  "avoid_time": "worst months or season to visit",

  "field_confidence": {{
    "accommodates_5": "high / medium / low",
    "max_occupancy_standard_room": "high / medium / low",
    "room_fit_for_5_type": "high / medium / low",
    "official_website": "high / medium / low",
    "all_inclusive": "high / medium / low",
    "kids_club": "high / medium / low",
    "food_quality_notes": "high / medium / low",
    "tripadvisor_rating": "high / medium / low",
    "sargassum_risk": "high / medium / low",
    "top_complaints": "high / medium / low"
  }},

  "source_urls": ["list of URLs used as sources for this research"],
  "research_notes": "any important caveats about the data quality"
}}

IMPORTANT:
- Search the web for current, accurate information about this specific hotel.
- For family occupancy questions, check the hotel's official website and booking sites.
- For room_fit_for_5_type: use "standard_room" only if a standard room officially sleeps 5+.
  Use "family_room" if they have a family room type. Use "suite_required" if only suites fit 5.
  Use "connecting_rooms_required" if 5 people need two connected rooms.
  Use "not_possible" if the resort cannot accommodate 5 in any configuration.
  Use "unclear" if you cannot determine this.
- For adults_only resorts, set accommodates_5 to false and room_fit_for_5_type to "not_possible".
- Return ONLY the JSON object. No markdown code fences. No explanation before or after."""


def load_existing_results(path: Path) -> dict:
    """Load already-researched hotels keyed by 'hotel_name|destination'."""
    results = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                key = f"{item.get('hotel_name', '')}|{item.get('destination', '')}"
                results[key] = item
        except (json.JSONDecodeError, KeyError):
            pass
    return results


def save_results(results: dict, path: Path) -> None:
    """Save all results to JSON file."""
    data = list(results.values())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_json_response(text: str) -> dict | None:
    """Extract JSON from Gemini response, handling markdown fences."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try extracting from markdown code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def research_hotel(client, hotel_name: str, destination: str, star_rating: str) -> dict | None:
    """Query Gemini with Google Search for a single hotel."""
    prompt = build_prompt(hotel_name, destination, star_rating)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )

        if not response.text:
            print(f"    Empty response from Gemini")
            return None

        result = parse_json_response(response.text)
        if result is None:
            print(f"    Failed to parse JSON response")
            # Save raw response for debugging
            debug_path = Path(__file__).parent / "debug_responses"
            debug_path.mkdir(exist_ok=True)
            safe_name = re.sub(r"[^\w]", "_", hotel_name)[:50]
            with open(debug_path / f"{safe_name}.txt", "w") as f:
                f.write(response.text)
            return None

        # Add metadata
        result["_researched_at"] = datetime.now(timezone.utc).isoformat()
        result["_model"] = MODEL
        return result

    except Exception as e:
        print(f"    API error: {e}")
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Research hotel intelligence via Gemini")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N unresearched hotels")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be researched without calling API")
    parser.add_argument("--api-key", default="", help="Gemini API key (or set GEMINI_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.api_key or GEMINI_API_KEY
    if not api_key and not args.dry_run:
        print("Error: Set GEMINI_API_KEY environment variable or use --api-key")
        sys.exit(1)

    # Load input hotels
    if not INPUT_CSV.exists():
        print(f"Error: {INPUT_CSV} not found")
        sys.exit(1)

    hotels = []
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            hotels.append(row)

    print(f"Loaded {len(hotels)} hotels from {INPUT_CSV.name}")

    # Load existing results
    existing = load_existing_results(OUTPUT_FILE)
    print(f"Already researched: {len(existing)} hotels")

    # Filter to unresearched
    to_research = []
    for h in hotels:
        key = f"{h['hotel_name']}|{h['destination']}"
        if key not in existing:
            to_research.append(h)

    print(f"Remaining to research: {len(to_research)} hotels")

    if args.limit > 0:
        to_research = to_research[:args.limit]
        print(f"Limited to {len(to_research)} hotels")

    if not to_research:
        print("Nothing to do — all hotels already researched!")
        return

    if args.dry_run:
        print("\nDry run — would research these hotels:")
        for i, h in enumerate(to_research, 1):
            print(f"  {i}. {h['hotel_name']} ({h['destination']})")
        return

    # Initialize Gemini client
    client = genai.Client(api_key=api_key)
    print(f"\nStarting research with {MODEL} ({REQUESTS_PER_MINUTE} RPM limit)...")
    print(f"Results will be saved to {OUTPUT_FILE.name} after each hotel.\n")

    succeeded = 0
    failed = 0
    results = existing.copy()

    for i, hotel in enumerate(to_research, 1):
        name = hotel["hotel_name"]
        dest = hotel["destination"]
        stars = hotel.get("star_rating", "")

        print(f"[{i}/{len(to_research)}] {name} ({dest})")

        result = research_hotel(client, name, dest, stars)

        if result:
            key = f"{name}|{dest}"
            results[key] = result
            save_results(results, OUTPUT_FILE)
            succeeded += 1
            print(f"    OK — accommodates_5={result.get('accommodates_5')}, "
                  f"type={result.get('room_fit_for_5_type')}")
        else:
            failed += 1
            print(f"    FAILED")

        # Rate limit (skip delay after last hotel)
        if i < len(to_research):
            time.sleep(DELAY_SECONDS)

    print(f"\nDone! Succeeded: {succeeded}, Failed: {failed}")
    print(f"Total researched: {len(results)} hotels")
    print(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
