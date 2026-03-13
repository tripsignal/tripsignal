"""Shared display formatting utilities."""

from typing import Optional

from app.services.market_intel import DESTINATION_LABELS
from app.workers.shared.regions import PARENT_REGION_MAP

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
