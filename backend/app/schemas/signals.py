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
    archived = "archived"


class RegionKey(str, Enum):
    """Destination region keys."""

    mexico = "mexico"
    dominican_republic = "dominican_republic"
    cuba = "cuba"
    jamaica = "jamaica"


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
    min_nights: int = Field(default=7, ge=1, description="Minimum nights")
    max_nights: int = Field(default=10, ge=1, description="Maximum nights")

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

    adults: int = Field(default=2, ge=1, description="Number of adults")
    children_ages: list[int] = Field(default_factory=list, description="Ages of children")
    rooms: int = Field(default=1, ge=1, description="Number of rooms")

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
    target_pp: int = Field(description="Target price per person")
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

    name: str = Field(description="Signal name")
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

    model_config = {"from_attributes": True}
