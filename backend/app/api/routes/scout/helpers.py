"""Shared helpers and constants for Scout endpoints."""
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models.signal import Signal
from app.db.models.user import User

logger = logging.getLogger("scout")

REGION_LABELS: dict[str, str] = {
    "mexico": "Mexico",
    "riviera_maya": "Riviera Maya",
    "cancun": "Cancún",
    "puerto_vallarta": "Puerto Vallarta",
    "los_cabos": "Los Cabos",
    "huatulco": "Huatulco",
    "puerto_escondido": "Puerto Escondido",
    "dominican_republic": "Dominican Republic",
    "punta_cana": "Punta Cana",
    "la_romana": "La Romana",
    "puerto_plata": "Puerto Plata",
    "samana": "Samaná",
    "santo_domingo": "Santo Domingo",
    "jamaica": "Jamaica",
    "montego_bay": "Montego Bay",
    "negril": "Negril",
    "ocho_rios": "Ocho Rios",
    "cuba": "Cuba",
    "varadero": "Varadero",
    "caribbean": "Caribbean",
    "costa_rica": "Costa Rica",
    "panama": "Panama",
    "barbados": "Barbados",
    "antigua": "Antigua",
    "saint_lucia": "Saint Lucia",
    "st_maarten": "St. Maarten",
    "grenada": "Grenada",
    "aruba": "Aruba",
    "curacao": "Curaçao",
    "bahamas": "Bahamas",
    "all_south": "All Destinations",
}


AIRPORT_CITY_MAP: dict[str, str] = {
    "YXX": "Abbotsford", "YVR": "Vancouver", "YYJ": "Victoria",
    "YLW": "Kelowna", "YKA": "Kamloops", "YXS": "Prince George",
    "YYC": "Calgary", "YEG": "Edmonton", "YMM": "Fort McMurray",
    "YQU": "Grande Prairie", "YQL": "Lethbridge", "YQR": "Regina",
    "YXE": "Saskatoon", "YWG": "Winnipeg", "YYZ": "Toronto",
    "YOW": "Ottawa", "YHM": "Hamilton", "YKF": "Kitchener",
    "YXU": "London", "YAM": "Sault Ste. Marie", "YSB": "Sudbury",
    "YQT": "Thunder Bay", "YQG": "Windsor", "YUL": "Montreal",
    "YQB": "Quebec City", "YBG": "Bagotville", "YFC": "Fredericton",
    "YQM": "Moncton", "YSJ": "Saint John", "YHZ": "Halifax",
    "YQY": "Sydney", "YYG": "Charlottetown", "YDF": "Deer Lake",
    "YQX": "Gander", "YYT": "St. John's", "YZF": "Yellowknife",
    "YXH": "Medicine Hat", "YTS": "Timmins",
    "YQQ": "Comox", "YXC": "Cranbrook", "YXJ": "Fort St. John",
    "YCD": "Nanaimo", "YYF": "Penticton", "YPR": "Prince Rupert",
    "YXT": "Terrace",
}

REGION_COUNTRY: dict[str, str] = {
    "mexico": "Mexico", "riviera_maya": "Mexico", "cancun": "Mexico",
    "puerto_vallarta": "Mexico", "los_cabos": "Mexico", "huatulco": "Mexico",
    "puerto_escondido": "Mexico", "mazatlan": "Mexico", "ixtapa": "Mexico",
    "dominican_republic": "Dominican Republic", "punta_cana": "Dominican Republic",
    "la_romana": "Dominican Republic", "puerto_plata": "Dominican Republic",
    "samana": "Dominican Republic", "santo_domingo": "Dominican Republic",
    "jamaica": "Jamaica", "montego_bay": "Jamaica", "negril": "Jamaica",
    "ocho_rios": "Jamaica",
    "cuba": "Cuba", "varadero": "Cuba", "holguin": "Cuba",
    "havana": "Cuba", "cayo_coco": "Cuba",
    "caribbean": "", "aruba": "Aruba", "barbados": "Barbados",
    "curacao": "Curaçao", "cayman_islands": "Cayman Islands",
    "saint_lucia": "Saint Lucia", "st_maarten": "Sint Maarten",
    "turks_caicos": "Turks and Caicos", "bahamas": "Bahamas",
    "antigua": "Antigua", "grenada": "Grenada",
    "costa_rica": "Costa Rica", "panama": "Panama", "belize": "Belize",
    "roatan": "Honduras",
    "all_south": "",
}


def _region_label(key: str) -> str:
    return REGION_LABELS.get(key, key.replace("_", " ").title())


def _get_user_and_signals(db: Session, clerk_user_id: str):
    """Shared helper: look up user + active signals."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    signals = (
        db.query(Signal)
        .filter(Signal.user_id == user.id, Signal.status == "active")
        .all()
    )
    return user, signals


def _build_route_label(signal: Signal) -> str:
    """Build a human-readable route label like 'Regina (YQR) → Los Cabos, Mexico'."""
    airports = signal.departure_airports or []
    regions = signal.destination_regions or []

    code = airports[0] if airports else "?"
    city = AIRPORT_CITY_MAP.get(code)
    origin = f"{city} ({code})" if city else code

    dest = _region_label(regions[0]) if regions else "?"
    country = REGION_COUNTRY.get(regions[0], "") if regions else ""
    if country and country != dest:
        dest = f"{dest}, {country}"

    return f"{origin} → {dest}"
