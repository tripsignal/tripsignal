"""Pydantic schemas for Signal CRUD operations."""
import re
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Mode(str, Enum):
    """Mode for departure/destination selection."""

    single = "single"
    multiple = "multiple"
    any = "any"


class SignalStatus(str, Enum):
    """Signal status."""

    active = "active"
    paused = "paused"
    payment_paused = "payment_paused"
    archived = "archived"


class RegionKey(str, Enum):
    """Destination region keys."""

    mexico = "mexico"
    riviera_maya = "riviera_maya"
    cancun = "cancun"
    puerto_vallarta = "puerto_vallarta"
    los_cabos = "los_cabos"
    mazatlan = "mazatlan"
    huatulco = "huatulco"
    ixtapa = "ixtapa"
    puerto_escondido = "puerto_escondido"
    dominican_republic = "dominican_republic"
    punta_cana = "punta_cana"
    puerto_plata = "puerto_plata"
    la_romana = "la_romana"
    samana = "samana"
    santo_domingo = "santo_domingo"
    jamaica = "jamaica"
    montego_bay = "montego_bay"
    negril = "negril"
    ocho_rios = "ocho_rios"
    cuba = "cuba"
    varadero = "varadero"
    holguin = "holguin"
    havana = "havana"
    cayo_coco = "cayo_coco"
    caribbean = "caribbean"
    aruba = "aruba"
    barbados = "barbados"
    curacao = "curacao"
    cayman_islands = "cayman_islands"
    saint_lucia = "saint_lucia"
    st_maarten = "st_maarten"
    turks_caicos = "turks_caicos"
    bahamas = "bahamas"
    antigua = "antigua"
    grenada = "grenada"
    central_america = "central_america"
    costa_rica = "costa_rica"
    panama = "panama"
    belize = "belize"
    roatan = "roatan"


class DepartureSpec(BaseModel):
    """Departure airport specification."""

    mode: Mode
    airports: list[str] = Field(default_factory=list, description="List of IATA airport codes")

    @field_validator("airports")
    @classmethod
    def validate_iata_codes(cls, v: list[str]) -> list[str]:
        """Validate IATA codes are exactly 3 characters."""
        for code in v:
            if len(code) != 3 or not code.isalpha():
                raise ValueError(f"IATA code must be exactly 3 alphabetic characters: {code}")
        return [code.upper() for code in v]


class DestinationSpec(BaseModel):
    """Destination specification."""

    mode: Mode
    regions: list[RegionKey] = Field(default_factory=list)
    airports: list[str] = Field(default_factory=list, description="List of IATA airport codes")
    label: Optional[str] = None

    @field_validator("airports")
    @classmethod
    def validate_iata_codes(cls, v: list[str]) -> list[str]:
        """Validate IATA codes are exactly 3 characters."""
        for code in v:
            if len(code) != 3 or not code.isalpha():
                raise ValueError(f"IATA code must be exactly 3 alphabetic characters: {code}")
        return [code.upper() for code in v]


class TravelWindow(BaseModel):
    """Travel date window specification."""

    start_month: str = Field(description="Start month in YYYY-MM format")
    end_month: str = Field(description="End month in YYYY-MM format")
    min_nights: int = Field(default=7, ge=1, le=30, description="Minimum nights")
    max_nights: int = Field(default=10, ge=1, le=30, description="Maximum nights")
    start_date: Optional[str] = Field(default=None, description="Specific start date in YYYY-MM-DD format")
    end_date: Optional[str] = Field(default=None, description="Specific end date in YYYY-MM-DD format")

    @field_validator("start_month", "end_month")
    @classmethod
    def validate_month_format(cls, v: str) -> str:
        """Validate month string matches YYYY-MM format."""
        if not re.match(r"^\d{4}-\d{2}$", v):
            raise ValueError(f"Month must be in YYYY-MM format: {v}")
        return v

    @model_validator(mode="after")
    def validate_nights_range(self) -> "TravelWindow":
        """Ensure max_nights >= min_nights."""
        if self.max_nights < self.min_nights:
            raise ValueError("max_nights must be >= min_nights")
        return self


class Travellers(BaseModel):
    """Traveller specification."""

    adults: int = Field(default=2, ge=1, le=10, description="Number of adults")
    children_ages: list[int] = Field(default_factory=list, max_length=10, description="Ages of children")
    rooms: int = Field(default=1, ge=1, le=10, description="Number of rooms")

    @field_validator("children_ages")
    @classmethod
    def validate_children_ages(cls, v: list[int]) -> list[int]:
        """Validate children ages are reasonable."""
        for age in v:
            if age < 0 or age > 17:
                raise ValueError(f"Child age must be between 0 and 17: {age}")
        return v


class BudgetSpec(BaseModel):
    """Budget specification."""

    currency: str = Field(default="CAD", description="Currency code")
    target_pp: int = Field(ge=0, le=100000, description="Target price per person in cents")
    strict: bool = Field(default=False, description="Whether to strictly enforce budget")


class QuietHours(BaseModel):
    """Quiet hours configuration."""

    enabled: bool = Field(default=False)
    start: str = Field(default="21:00", description="Start time in HH:MM format")
    end: str = Field(default="08:00", description="End time in HH:MM format")

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Validate time string matches HH:MM format."""
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"Time must be in HH:MM format: {v}")
        # Validate hours and minutes are in valid range
        try:
            hours, minutes = map(int, v.split(":"))
            if not (0 <= hours <= 23) or not (0 <= minutes <= 59):
                raise ValueError(f"Time values out of range: {v}")
        except ValueError as e:
            raise ValueError(f"Invalid time format: {v}") from e
        return v


class Notifications(BaseModel):
    """Notification preferences."""

    email_enabled: bool = Field(default=False)
    email: Optional[EmailStr] = Field(default=None)
    quiet_hours: QuietHours = Field(default_factory=QuietHours)

    @model_validator(mode="after")
    def validate_email_when_enabled(self) -> "Notifications":
        """Require email when email_enabled is True."""
        if self.email_enabled is True and self.email is None:
            raise ValueError("email is required when email_enabled is true")
        return self


class Preferences(BaseModel):
    """Travel preferences."""

    min_star_rating: Optional[int] = Field(default=None, ge=1, le=5, description="Minimum star rating (1-5)")
    nonstop_only: Optional[bool] = Field(default=None, description="Only nonstop flights")


# CRUD DTOs


class SignalCreate(BaseModel):
    """Schema for creating a new signal."""

    name: str = Field(min_length=1, max_length=200, description="Signal name")
    departure: DepartureSpec
    destination: DestinationSpec
    travel_window: TravelWindow
    travellers: Travellers
    budget: BudgetSpec
    notifications: Notifications = Field(default_factory=Notifications)
    preferences: Preferences = Field(default_factory=Preferences)


class SignalUpdate(BaseModel):
    """Schema for updating an existing signal."""

    name: Optional[str] = None
    departure: Optional[DepartureSpec] = None
    destination: Optional[DestinationSpec] = None
    travel_window: Optional[TravelWindow] = None
    travellers: Optional[Travellers] = None
    budget: Optional[BudgetSpec] = None
    notifications: Optional[Notifications] = None
    preferences: Optional[Preferences] = None
    status: Optional[SignalStatus] = None


class SignalIntel(BaseModel):
    """Per-signal intelligence summary from cache."""

    value_score: Optional[int] = None
    trend_direction: Optional[str] = None
    trend_consecutive_weeks: Optional[int] = None
    min_price_ever_cents: Optional[int] = None
    current_deal_percentile: Optional[float] = None
    floor_proximity_pct: Optional[float] = None
    best_value_nights: Optional[int] = None
    total_matches: Optional[int] = None
    cache_refreshed_at: Optional[datetime] = None

    # Live market intelligence (computed per request from active deals)
    best_price_cents: Optional[int] = None
    median_price_cents: Optional[int] = None
    value_label: Optional[str] = None
    price_delta_amount: Optional[int] = None

    # Market price spectrum (from comparable market bucket)
    spectrum_min: Optional[int] = None
    spectrum_p25: Optional[int] = None
    spectrum_median: Optional[int] = None
    spectrum_p75: Optional[int] = None
    spectrum_max: Optional[int] = None
    spectrum_sample_size: Optional[int] = None

    # Empty-state diagnostics (for signals with no matches)
    empty_reason: Optional[str] = None  # 'above_budget', 'outside_date_window', 'no_inventory', 'healthy'
    empty_budget_gap_cents: Optional[int] = None
    empty_date_gap_days: Optional[int] = None
    empty_market_packages: Optional[int] = None
    empty_adjustment_type: Optional[str] = None  # 'budget_flex', 'date_flex'
    empty_adjustment_value: Optional[str] = None  # e.g. '+$200', '±3 days'
    empty_adjustment_matches: Optional[int] = None


class SignalOut(BaseModel):
    """Schema for signal output (read operations)."""

    id: UUID
    name: str
    status: SignalStatus
    departure: DepartureSpec
    destination: DestinationSpec
    travel_window: TravelWindow
    travellers: Travellers
    budget: BudgetSpec
    notifications: Notifications
    preferences: Preferences
    created_at: datetime
    updated_at: datetime
    match_count: int = 0
    intel: Optional[SignalIntel] = None
    model_config = {"from_attributes": True}
