"""Shared destination-region mapping used by all scrapers and signal matching."""

from typing import Optional

# Sub-regions MUST come before parent catch-alls (first match wins)
DESTINATION_REGION_MAP = {
    "riviera maya": "riviera_maya",
    "playa mujeres": "cancun",
    "cancun": "cancun",
    "puerto vallarta": "puerto_vallarta",
    "riviera nayarit": "puerto_vallarta",
    "los cabos": "los_cabos",
    "mazatlan": "mazatlan",
    "huatulco": "huatulco",
    "ixtapa": "ixtapa",
    "puerto escondido": "puerto_escondido",
    "mexico": "mexico",
    "punta cana": "punta_cana",
    "puerto plata": "puerto_plata",
    "la romana": "la_romana",
    "miches": "punta_cana",
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
    "liberia": "costa_rica",
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


def map_destination_to_region(destination: str) -> Optional[str]:
    dest_lower = destination.lower()
    for keyword, region in DESTINATION_REGION_MAP.items():
        if keyword in dest_lower:
            return region
    return None
