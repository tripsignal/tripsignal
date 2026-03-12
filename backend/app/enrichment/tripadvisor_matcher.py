"""Match source hotel records against a TripAdvisor seed dataset.

Produces a scored match result for each source hotel, with confidence levels
and review status flags.
"""

import csv
import json
import logging
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path

from app.enrichment.normalize import (
    normalize_hotel_name,
    normalize_hotel_name_aggressive,
    normalize_destination,
    get_country_for_destination,
)

logger = logging.getLogger(__name__)


def _safe_float(val: str | None) -> float | None:
    """Parse a float from a string, returning None on bad data."""
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# Confidence thresholds
EXACT_CONFIDENCE = 1.0
HIGH_CONFIDENCE = 0.92
MEDIUM_CONFIDENCE = 0.80
LOW_CONFIDENCE = 0.65
MINIMUM_CONFIDENCE = 0.55


@dataclass
class SeedHotel:
    """A TripAdvisor hotel from the seed file."""
    tripadvisor_url: str
    tripadvisor_name: str  # raw name from seed
    destination: str  # raw destination from seed
    tripadvisor_id: int | None = None

    # Normalized (populated during load)
    normalized_name: str = ""
    normalized_name_aggressive: str = ""
    normalized_destination: str = ""

    def __post_init__(self):
        self.normalized_name = normalize_hotel_name(self.tripadvisor_name)
        self.normalized_name_aggressive = normalize_hotel_name_aggressive(self.tripadvisor_name)
        self.normalized_destination = normalize_destination(self.destination) if self.destination else ""
        if not self.tripadvisor_id:
            self.tripadvisor_id = extract_tripadvisor_id(self.tripadvisor_url)


@dataclass
class SourceHotel:
    """A hotel from our deals database."""
    hotel_name: str
    hotel_id: str = ""
    destination: str = ""
    destination_str: str = ""
    star_rating: float | None = None

    # Normalized (populated during load)
    normalized_name: str = ""
    normalized_name_aggressive: str = ""
    normalized_destination: str = ""
    country: str = ""

    def __post_init__(self):
        self.normalized_name = normalize_hotel_name(self.hotel_name)
        self.normalized_name_aggressive = normalize_hotel_name_aggressive(self.hotel_name)
        dest_raw = self.destination_str or self.destination
        self.normalized_destination = normalize_destination(dest_raw) if dest_raw else ""
        self.country = get_country_for_destination(self.normalized_destination) or ""


@dataclass
class MatchResult:
    """Result of matching a source hotel to TripAdvisor."""
    source_hotel_name: str
    normalized_hotel_name: str
    source_hotel_id: str
    source_destination: str
    source_country: str
    tripadvisor_url: str | None = None
    tripadvisor_id: int | None = None
    tripadvisor_matched_name: str | None = None
    match_confidence: float = 0.0
    match_method: str = "none"
    review_status: str = "not_found"
    notes: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def extract_tripadvisor_id(url: str) -> int | None:
    """Extract the numeric hotel ID from a TripAdvisor URL.

    Handles patterns like:
    - /Hotel_Review-g147293-d152337-Reviews-...
    - /Hotel_Review-g...-d12345-...
    """
    if not url:
        return None
    m = re.search(r"-d(\d+)-", url)
    return int(m.group(1)) if m else None


def extract_destination_from_url(url: str) -> str:
    """Extract a destination hint from a TripAdvisor URL slug.

    TripAdvisor URLs end with location info like:
      ...Reviews-Hotel_Name-Cancun_Yucatan_Peninsula.html
      ...Reviews-Hotel_Name-Bavaro_Punta_Cana_La_Altagracia_Province_Dominican_Republic.html

    We extract the location part after the last hyphen in the Reviews section,
    then clean it into a searchable destination string.
    """
    if not url:
        return ""
    # Strip query params and fragment
    url = url.split("?")[0].split("#")[0]
    # Remove .html suffix
    url = re.sub(r"\.html$", "", url)
    # Remove pagination segments like "-or20-" before parsing
    url = re.sub(r"-or\d+-", "-", url)
    # Get everything after "Reviews-" — the slug contains Hotel_Name-Location
    m = re.search(r"Reviews-(.+)$", url)
    if not m:
        return ""
    slug = m.group(1)
    # Split on hyphens — last segment is the location
    parts = slug.split("-")
    if len(parts) < 2:
        return ""
    location_slug = parts[-1]
    # Convert underscores to spaces, strip province/state suffixes
    location = location_slug.replace("_", " ")
    # Remove common suffixes that aren't useful for matching
    for suffix in ["Province", "Peninsula", "Parish", "Quarter", "Island", "Islands"]:
        location = re.sub(rf"\b{suffix}\b", "", location, flags=re.IGNORECASE)
    location = re.sub(r"\s+", " ", location).strip()
    return location


def _similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _destinations_compatible(src_dest: str, seed_dest: str) -> bool:
    """Check if two normalized destinations are compatible (same or closely related)."""
    if not src_dest or not seed_dest:
        return True  # can't disqualify if destination unknown
    return src_dest == seed_dest


class TripAdvisorMatcher:
    """Matches source hotels against a loaded TripAdvisor seed dataset."""

    def __init__(self, seed_hotels: list[SeedHotel]):
        self.seed_hotels = seed_hotels
        logger.info("Loaded %d TripAdvisor seed hotels", len(seed_hotels))

    @classmethod
    def from_csv(cls, path: str | Path) -> "TripAdvisorMatcher":
        """Load seed data from CSV.

        Accepted column names:
        - Name: tripadvisor_name OR hotel_name
        - URL:  tripadvisor_url
        - Dest: destination (optional — extracted from URL if missing)
        - ID:   tripadvisor_id (optional — extracted from URL if missing)
        """
        seeds = []
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("tripadvisor_url", "").strip()
                # Accept either column name for the hotel name
                name = (row.get("tripadvisor_name") or row.get("hotel_name") or "").strip()
                dest = row.get("destination", "").strip()
                ta_id = row.get("tripadvisor_id", "").strip()

                if not url or not name:
                    continue

                # Extract destination from URL if not provided
                if not dest and url:
                    dest = extract_destination_from_url(url)

                seeds.append(SeedHotel(
                    tripadvisor_url=url,
                    tripadvisor_name=name,
                    destination=dest,
                    tripadvisor_id=int(ta_id) if ta_id.isdigit() else None,
                ))
        return cls(seeds)

    def match(self, hotel: SourceHotel) -> MatchResult:
        """Match a single source hotel against all seed hotels. Returns best match."""
        result = MatchResult(
            source_hotel_name=hotel.hotel_name,
            normalized_hotel_name=hotel.normalized_name,
            source_hotel_id=hotel.hotel_id,
            source_destination=hotel.destination_str or hotel.destination,
            source_country=hotel.country,
        )

        if not hotel.normalized_name:
            result.notes = "Empty hotel name after normalization"
            return result

        best_score = 0.0
        best_method = "none"
        best_seed: SeedHotel | None = None
        runner_up_score = 0.0

        for seed in self.seed_hotels:
            score, method = self._score_pair(hotel, seed)
            if score > best_score:
                runner_up_score = best_score
                best_score = score
                best_method = method
                best_seed = seed
            elif score > runner_up_score:
                runner_up_score = score

        if best_seed and best_score >= MINIMUM_CONFIDENCE:
            result.tripadvisor_url = best_seed.tripadvisor_url
            result.tripadvisor_id = best_seed.tripadvisor_id
            result.tripadvisor_matched_name = best_seed.tripadvisor_name
            result.match_confidence = round(best_score, 4)
            result.match_method = best_method

            # Determine review status
            ambiguity_gap = best_score - runner_up_score
            if best_score >= HIGH_CONFIDENCE and ambiguity_gap >= 0.05:
                result.review_status = "matched"
            elif best_score >= MEDIUM_CONFIDENCE:
                if ambiguity_gap < 0.05:
                    result.review_status = "ambiguous"
                    result.notes = f"Runner-up within {ambiguity_gap:.2f} — verify correct hotel"
                else:
                    result.review_status = "matched"
            elif best_score >= LOW_CONFIDENCE:
                result.review_status = "needs_manual_review"
                result.notes = f"Moderate match ({best_score:.2f}) — verify manually"
            else:
                result.review_status = "needs_manual_review"
                result.notes = f"Weak match ({best_score:.2f}) — likely needs correction"
        else:
            result.review_status = "not_found"
            if best_seed:
                result.notes = f"Best candidate: {best_seed.tripadvisor_name} ({best_score:.2f})"

        return result

    def match_all(self, hotels: list[SourceHotel]) -> list[MatchResult]:
        """Match a list of source hotels. Returns results in same order."""
        results = []
        for i, hotel in enumerate(hotels):
            result = self.match(hotel)
            results.append(result)
            if (i + 1) % 50 == 0:
                logger.info("Matched %d / %d hotels", i + 1, len(hotels))
        logger.info("Matching complete: %d hotels processed", len(results))
        return results

    def _score_pair(self, hotel: SourceHotel, seed: SeedHotel) -> tuple[float, str]:
        """Score a (source, seed) pair. Returns (confidence, method)."""

        dest_match = _destinations_compatible(hotel.normalized_destination, seed.normalized_destination)

        # Layer 1: exact normalized name + destination
        if hotel.normalized_name == seed.normalized_name and dest_match:
            return EXACT_CONFIDENCE, "exact_name_plus_destination"

        # Layer 2: exact normalized name (any destination)
        if hotel.normalized_name == seed.normalized_name:
            return 0.93, "exact_name_only"

        # Layer 3: fuzzy standard name + destination
        sim = _similarity(hotel.normalized_name, seed.normalized_name)
        if sim >= 0.88 and dest_match:
            return min(sim + 0.05, 0.99), "fuzzy_name_plus_destination"

        # Layer 4: fuzzy standard name (any destination)
        if sim >= 0.88:
            return sim * 0.95, "fuzzy_name_only"

        # Layer 5: aggressive name match + destination
        sim_agg = _similarity(hotel.normalized_name_aggressive, seed.normalized_name_aggressive)
        if sim_agg >= 0.85 and dest_match:
            return min(sim_agg * 0.90, 0.90), "aggressive_fuzzy_plus_destination"

        # Layer 6: aggressive name match (any destination)
        if sim_agg >= 0.85:
            return sim_agg * 0.80, "aggressive_fuzzy_only"

        # Layer 7: partial match — one name contains the other + destination
        if dest_match and len(hotel.normalized_name) > 10 and len(seed.normalized_name) > 10:
            if hotel.normalized_name in seed.normalized_name or seed.normalized_name in hotel.normalized_name:
                containment = min(len(hotel.normalized_name), len(seed.normalized_name)) / max(len(hotel.normalized_name), len(seed.normalized_name))
                return min(containment * 0.85, 0.85), "substring_plus_destination"

        # Below threshold
        return max(sim, sim_agg) * 0.5, "weak_fuzzy"


def load_source_hotels_from_json(path: str | Path) -> list[SourceHotel]:
    """Load source hotels from a JSON export file.

    Expected shape: list of dicts with keys: hotel_name, hotel_id, destination, destination_str, star_rating
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    hotels = []
    seen = set()
    for row in data:
        name = row.get("hotel_name", "").strip()
        hid = str(row.get("hotel_id", "")).strip()
        if not name:
            continue
        # Deduplicate by hotel_id if available, else by name
        key = hid if hid else name.lower()
        if key in seen:
            continue
        seen.add(key)
        hotels.append(SourceHotel(
            hotel_name=name,
            hotel_id=hid,
            destination=row.get("destination", ""),
            destination_str=row.get("destination_str", ""),
            star_rating=row.get("star_rating"),
        ))
    return hotels


def load_source_hotels_from_csv(path: str | Path) -> list[SourceHotel]:
    """Load source hotels from a CSV export."""
    path = Path(path)
    hotels = []
    seen = set()
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("hotel_name", "").strip()
            hid = row.get("hotel_id", "").strip()
            if not name:
                continue
            key = hid if hid else name.lower()
            if key in seen:
                continue
            seen.add(key)
            hotels.append(SourceHotel(
                hotel_name=name,
                hotel_id=hid,
                destination=row.get("destination", ""),
                destination_str=row.get("destination_str", ""),
                star_rating=_safe_float(row.get("star_rating")),
            ))
    return hotels
