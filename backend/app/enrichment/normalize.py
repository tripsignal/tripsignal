"""Hotel name and destination normalization for TripAdvisor matching.

Centralizes all text cleaning so both source hotel records and TripAdvisor
seed data go through the same pipeline before comparison.
"""

import re
import unicodedata


# Brand suffixes that add noise to matching — stripped during normalization
_BRAND_SUFFIXES = [
    r"\bby\s+wyndham\b",
    r"\bby\s+mercure\b",
    r"\bby\s+marriott\b",
    r"\bby\s+hilton\b",
    r"\bby\s+hyatt\b",
    r"\bby\s+ihg\b",
    r"\bby\s+karisma\b",
    r"\ba?\s*trademark\s+(collection|all\s+inclusive)\b",
    r"\btapestry\s+collection\b",
    r"\bcurio\s+collection\b",
    r"\bautograph\s+collection\b",
]

# Common suffixes that may differ between sources
_OPTIONAL_SUFFIXES = [
    r"\ball\s+inclusive\b",
    r"\badults[\s-]*only\b",
    r"\bfamily\s+selection\b",
]

# Destination aliases — maps variant names to canonical form (lowercase)
DESTINATION_ALIASES: dict[str, str] = {
    "riviera nayarit": "nuevo vallarta",
    "nuevo vallarta": "nuevo vallarta",
    "playa del carmen": "playa del carmen",
    "puerto vallarta": "puerto vallarta",
    "riviera maya": "riviera maya",
    "cancun": "cancun",
    "los cabos": "los cabos",
    "cabo san lucas": "los cabos",
    "san jose del cabo": "los cabos",
    "punta cana": "punta cana",
    "bavaro": "punta cana",
    "cap cana": "punta cana",
    "puerto plata": "puerto plata",
    "la romana": "la romana",
    "bayahibe": "la romana",
    "montego bay": "montego bay",
    "negril": "negril",
    "ocho rios": "ocho rios",
    "varadero": "varadero",
    "holguin": "holguin",
    "cayo coco": "cayo coco",
    "cayo santa maria": "cayo santa maria",
    "samana": "samana",
    "miches": "samana",
    "huatulco": "huatulco",
    "ixtapa": "ixtapa",
    "zihuatanejo": "ixtapa",
    "mazatlan": "mazatlan",
    "nassau": "nassau",
    "paradise island": "nassau",
    "new providence": "nassau",
    "providenciales": "turks and caicos",
    "turks and caicos": "turks and caicos",
    "bridgetown": "barbados",
    "barbados": "barbados",
    "aruba": "aruba",
    "curacao": "curacao",
    "st lucia": "saint lucia",
    "saint lucia": "saint lucia",
    "st. lucia": "saint lucia",
    "costa rica": "costa rica",
    "liberia": "costa rica",
    "guanacaste": "costa rica",
    "roatan": "roatan",
}

# Precomputed: aliases sorted by length descending (longest match first)
_SORTED_ALIAS_KEYS: list[str] = sorted(DESTINATION_ALIASES.keys(), key=len, reverse=True)

# Country lookup from canonical destination
DESTINATION_COUNTRY: dict[str, str] = {
    "cancun": "Mexico",
    "riviera maya": "Mexico",
    "playa del carmen": "Mexico",
    "puerto vallarta": "Mexico",
    "nuevo vallarta": "Mexico",
    "los cabos": "Mexico",
    "mazatlan": "Mexico",
    "huatulco": "Mexico",
    "ixtapa": "Mexico",
    "punta cana": "Dominican Republic",
    "la romana": "Dominican Republic",
    "puerto plata": "Dominican Republic",
    "samana": "Dominican Republic",
    "montego bay": "Jamaica",
    "negril": "Jamaica",
    "ocho rios": "Jamaica",
    "varadero": "Cuba",
    "holguin": "Cuba",
    "cayo coco": "Cuba",
    "cayo santa maria": "Cuba",
    "nassau": "Bahamas",
    "aruba": "Aruba",
    "barbados": "Barbados",
    "curacao": "Curacao",
    "saint lucia": "Saint Lucia",
    "turks and caicos": "Turks and Caicos",
    "costa rica": "Costa Rica",
    "roatan": "Honduras",
}


def strip_accents(text: str) -> str:
    """Remove diacritics (é→e, ñ→n, ü→u) for matching. Preserves base chars."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_hotel_name(name: str, *, strip_brands: bool = True, strip_suffixes: bool = False) -> str:
    """Normalize a hotel name for comparison.

    Steps:
    1. Lowercase
    2. Strip accents
    3. Decode HTML entities (&amp; → and)
    4. Normalize & → and
    5. Remove brand suffixes (optional)
    6. Remove adults-only / all-inclusive suffixes (optional)
    7. Strip trailing destination names (e.g. "... Los Cabos, México")
    8. Collapse whitespace and strip punctuation noise
    """
    s = name.lower().strip()
    s = strip_accents(s)

    # HTML entity cleanup
    s = s.replace("&amp;", "and").replace("&#39;", "'")
    # Normalize ampersand
    s = s.replace(" & ", " and ")

    if strip_brands:
        for pat in _BRAND_SUFFIXES:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)

    if strip_suffixes:
        for pat in _OPTIONAL_SUFFIXES:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)

    # Remove trailing comma + destination ("..., Mexico", "..., Los Cabos")
    s = re.sub(r",\s*[a-z\s]+$", "", s)

    # Remove special chars except alphanumeric and spaces
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s


def normalize_destination(dest: str) -> str:
    """Normalize a destination string to its canonical form.

    Returns the canonical alias if found, otherwise a cleaned lowercase version.
    """
    s = dest.lower().strip()
    s = strip_accents(s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Check for alias matches (longest match first to avoid partial hits)
    for alias in _SORTED_ALIAS_KEYS:
        if alias in s:
            return DESTINATION_ALIASES[alias]

    return s


def get_country_for_destination(dest_normalized: str) -> str | None:
    """Look up the country for a normalized destination string."""
    return DESTINATION_COUNTRY.get(dest_normalized)


def normalize_hotel_name_aggressive(name: str) -> str:
    """More aggressive normalization — strips brands, suffixes, and common filler words.

    Use this for fuzzy fallback matching when standard normalization doesn't produce a match.
    """
    s = normalize_hotel_name(name, strip_brands=True, strip_suffixes=True)

    # Remove common filler words that differ between sources
    fillers = [r"\bresort\b", r"\bhotel\b", r"\bsuites?\b", r"\bspa\b", r"\bbeach\b",
               r"\ball\b", r"\bthe\b", r"\bclub\b", r"\bvillas?\b"]
    for f in fillers:
        s = re.sub(f, "", s)

    s = re.sub(r"\s+", " ", s).strip()
    return s
