"""Browser profiles for scraper anti-detection.

Each profile bundles a curl_cffi impersonate target with matching headers.
One profile is selected per scrape cycle to simulate a single browser session.

The UA_LAST_UPDATED date triggers a WARNING log if >90 days stale.
Run `python scripts/update_user_agents.py` to refresh.
"""
import logging
import random
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# --- Staleness check -----------------------------------------------------------
# Update this date whenever you refresh the UA / impersonate targets below.
# The scraper logs a WARNING every startup if this is >90 days old.
UA_LAST_UPDATED = date(2026, 3, 12)
_UA_MAX_AGE_DAYS = 90


def check_ua_staleness() -> None:
    """Log a warning if browser profiles haven't been updated recently."""
    age = (date.today() - UA_LAST_UPDATED).days
    if age > _UA_MAX_AGE_DAYS:
        logger.warning(
            "BROWSER PROFILES ARE %d DAYS OLD (last updated %s). "
            "Run `python scripts/update_user_agents.py` to refresh. "
            "Stale fingerprints increase detection risk.",
            age, UA_LAST_UPDATED.isoformat(),
        )


# --- Browser profiles ----------------------------------------------------------
# Each profile pairs a curl_cffi impersonate target with headers that match
# what that real browser would send. curl_cffi handles TLS fingerprint, HTTP/2
# settings, and header ordering automatically when `impersonate` is set.
#
# The `extra_headers` here are *supplemental* — curl_cffi sets User-Agent,
# Accept, Accept-Language, etc. automatically. We add Sec-Fetch-* and other
# headers that curl_cffi doesn't set by default.

BROWSER_PROFILES = [
    # Chrome 131 on Windows
    {
        "impersonate": "chrome131",
        "platform": "windows",
        "extra_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-CH-UA": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        },
    },
    # Chrome 131 on Mac
    {
        "impersonate": "chrome131",
        "platform": "macos",
        "extra_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-CH-UA": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"macOS"',
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        },
    },
    # Chrome 130 on Windows
    {
        "impersonate": "chrome130",
        "platform": "windows",
        "extra_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-CH-UA": '"Chromium";v="130", "Google Chrome";v="130", "Not_A Brand";v="99"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        },
    },
    # Edge 131 on Windows
    {
        "impersonate": "edge131",
        "platform": "windows",
        "extra_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-CH-UA": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        },
    },
    # Safari 18 on Mac (no Sec-CH-UA headers — Safari doesn't send them)
    {
        "impersonate": "safari18_0",
        "platform": "macos",
        "extra_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
    },
]


_FALLBACK_PROFILE = BROWSER_PROFILES[0]  # Chrome on Windows — always supported


def pick_cycle_profile() -> dict:
    """Select a random browser profile for this scrape cycle.

    Validates that the impersonate target is supported by curl_cffi.
    Falls back to a known-good profile if the selected one isn't.
    """
    profile = random.choice(BROWSER_PROFILES)
    try:
        from curl_cffi.requests import BrowserType
        # Validate target exists (BrowserType is an enum)
        BrowserType[profile["impersonate"]]
    except (KeyError, ImportError):
        logger.warning(
            "Impersonate target '%s' not supported by installed curl_cffi — "
            "falling back to '%s'. Run update_user_agents.py to fix.",
            profile["impersonate"], _FALLBACK_PROFILE["impersonate"],
        )
        return _FALLBACK_PROFILE
    return profile


def build_request_headers(
    profile: dict,
    referer: Optional[str] = None,
) -> dict:
    """Build request headers from a profile, with optional referer.

    curl_cffi sets core headers (User-Agent, Accept, Accept-Language, etc.)
    automatically via the impersonate target. We only add supplemental headers
    here — Sec-Fetch-*, Sec-CH-UA-*, and navigation context.
    """
    headers = dict(profile.get("extra_headers", {}))

    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    else:
        headers["Sec-Fetch-Site"] = "none"

    return headers


# --- Human-like delay distribution ---------------------------------------------

def human_delay() -> float:
    """Return a delay in seconds that mimics human browsing patterns.

    Mostly 8-25s (normal page reading), occasionally 40-90s (distracted pause).
    Gaussian distribution centered on 18s with occasional long tails.
    """
    if random.random() < 0.12:
        # ~12% chance of a "distracted" longer pause
        return random.uniform(40, 90)
    # Normal browsing delay — Gaussian centered at 18s, clamped to [8, 35]
    delay = random.gauss(18, 5)
    return max(8.0, min(35.0, delay))


def category_pause() -> float:
    """Return a longer pause between destination categories.

    Simulates a user taking a break between browsing different regions.
    """
    return random.uniform(60, 180)


# --- Destination tiering -------------------------------------------------------
# Tier 1: scraped every cycle (high-volume, high-value routes)
# Tier 2: scraped most cycles (~80% chance)
# Tier 3: scraped less frequently (~50% chance)

SELLOFF_DESTINATION_TIERS = {
    1: [
        "mexico/cancun", "mexico/riviera-maya", "mexico/puerto-vallarta",
        "mexico/los-cabos", "dominican-republic/punta-cana",
        "jamaica/montego-bay", "jamaica/negril", "cuba/varadero",
        "costa-rica", "aruba",
    ],
    2: [
        "mexico/mazatlan", "mexico/huatulco", "mexico/ixtapa-zihuatanejo",
        "mexico/cozumel", "mexico/playa-mujeres", "mexico/riviera-nayarit",
        "dominican-republic/puerto-plata", "dominican-republic/la-romana",
        "jamaica/ocho-rios", "barbados", "saint-lucia", "antigua",
        "honduras/roatan",
    ],
    3: [
        "mexico/tulum", "mexico/isla-holbox",
        "dominican-republic/samana", "dominican-republic/santo-domingo",
        "dominican-republic/cabarete", "dominican-republic/sosua",
        "panama", "grenada", "cayman-islands", "st-maarten", "bermuda",
    ],
}

# Tier probabilities: chance of including a destination from each tier
_TIER_PROBABILITIES = {1: 1.0, 2: 0.80, 3: 0.50}

SELLOFF_GATEWAY_TIERS = {
    1: ["YYZ", "YVR", "YUL", "YYC", "YEG", "YOW", "YWG", "YHZ"],
    2: ["YHM", "YKF", "YXU", "YQB", "YXE", "YQR", "YYJ", "YLW",
        "YYT", "YQM", "YFC", "YXX"],
    3: ["YKA", "YXS", "YMM", "YQU", "YQL", "YQT", "YBG", "YDF",
        "YQX", "YSJ", "YYG", "YSB", "YAM", "YQG"],
}


def select_cycle_destinations(destination_slugs: list[str]) -> list[str]:
    """Select which destinations to scrape this cycle based on tiering.

    Tier 1 destinations are always included. Tier 2 and 3 are probabilistic.
    Returns a shuffled list.
    """
    selected = []
    tiered = set()
    for tier, slugs in SELLOFF_DESTINATION_TIERS.items():
        prob = _TIER_PROBABILITIES[tier]
        for slug in slugs:
            tiered.add(slug)
            if random.random() < prob:
                selected.append(slug)

    # Include any destinations not in the tier map (future-proofing)
    for slug in destination_slugs:
        if slug not in tiered:
            selected.append(slug)

    random.shuffle(selected)
    return selected


def select_cycle_gateways(gateway_map: dict[str, str]) -> list[tuple[str, str]]:
    """Select which gateways to scrape this cycle based on tiering.

    Returns a shuffled list of (gateway_code, city_slug) tuples.
    """
    selected = []
    tiered = set()
    for tier, codes in SELLOFF_GATEWAY_TIERS.items():
        prob = _TIER_PROBABILITIES[tier]
        for code in codes:
            tiered.add(code)
            if code in gateway_map and random.random() < prob:
                selected.append((code, gateway_map[code]))

    # Include any gateways not in the tier map
    for code, slug in gateway_map.items():
        if code not in tiered:
            selected.append((code, slug))

    random.shuffle(selected)
    return selected


# --- Navigation pages for realistic browsing -----------------------------------

SELLOFF_NAV_PAGES = [
    "https://www.selloffvacations.com/en",
    "https://www.selloffvacations.com/en/mexico",
    "https://www.selloffvacations.com/en/caribbean",
    "https://www.selloffvacations.com/en/jamaica",
    "https://www.selloffvacations.com/en/dominican-republic",
    "https://www.selloffvacations.com/en/cuba",
    "https://www.selloffvacations.com/en/deals",
]

SELLOFF_WARMUP_PAGES = [
    "https://www.selloffvacations.com/en",
    "https://www.selloffvacations.com/en/mexico",
    "https://www.selloffvacations.com/en/caribbean",
]
