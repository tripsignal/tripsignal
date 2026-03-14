"""Shared display formatting utilities."""

from typing import Optional

from app.workers.shared.regions import PARENT_REGION_MAP

# Destination key → display label mapping (server-side, canonical source)
DESTINATION_LABELS: dict[str, str] = {
    "mexico": "Mexico", "riviera_maya": "Riviera Maya", "cancun": "Cancún",
    "puerto_vallarta": "Puerto Vallarta", "los_cabos": "Los Cabos",
    "mazatlan": "Mazatlán", "huatulco": "Huatulco", "ixtapa": "Ixtapa",
    "dominican_republic": "Dominican Republic", "punta_cana": "Punta Cana",
    "puerto_plata": "Puerto Plata", "la_romana": "La Romana", "samana": "Samaná",
    "jamaica": "Jamaica", "montego_bay": "Montego Bay", "negril": "Negril",
    "cuba": "Cuba", "varadero": "Varadero", "holguin": "Holguín", "havana": "Havana",
    "cayo_coco": "Cayo Coco", "caribbean": "Caribbean", "aruba": "Aruba",
    "barbados": "Barbados", "curacao": "Curaçao", "saint_lucia": "Saint Lucia",
    "turks_caicos": "Turks & Caicos", "bahamas": "Bahamas", "antigua": "Antigua",
    "costa_rica": "Costa Rica", "panama": "Panama", "belize": "Belize",
    "roatan": "Roatán",
}


def dest_label(key: str) -> str:
    """Return a human-readable label for a destination region key."""
    return DESTINATION_LABELS.get(key, key.replace("_", " ").title())


# Parent regions that are real countries (append to sub-region labels)
_PARENT_COUNTRY_NAMES = {
    "mexico": "Mexico", "dominican_republic": "Dominican Republic",
    "jamaica": "Jamaica", "cuba": "Cuba",
}


def normalize_destination_display(destination_str: Optional[str], region_key: Optional[str]) -> Optional[str]:
    """Ensure destination display always includes the country (e.g. 'Cancún, Mexico').

    Only appends country for sub-regions within real countries (Mexico, DR, Jamaica, Cuba).
    Strings that already contain a comma are returned as-is.
    """
    if not region_key:
        return destination_str
    if destination_str and "," in destination_str:
        return destination_str
    label = DESTINATION_LABELS.get(region_key)
    parent = PARENT_REGION_MAP.get(region_key)
    country = _PARENT_COUNTRY_NAMES.get(parent, "") if parent else ""
    if label and country:
        return f"{label}, {country}"
    return destination_str
