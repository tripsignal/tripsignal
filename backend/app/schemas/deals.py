"""Pydantic schemas for Deal and DealMatch."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class DealBase(BaseModel):
    """Base Deal schema with common fields."""

    provider: str
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price_cents: int
    currency: str
    deeplink_url: str | None
    airline: str | None
    cabin: str | None
    stops: int | None
    found_at: datetime

    model_config = {"from_attributes": True}


class DealCreate(DealBase):
    """Schema for creating a new deal."""

    dedupe_key: str

    model_config = {"from_attributes": True}


class DealResponse(DealBase):
    """Schema for Deal response."""

    id: UUID
    dedupe_key: str

    model_config = {"from_attributes": True}


class DealMatchResponse(BaseModel):
    """Schema for DealMatch response."""

    id: UUID
    signal_run_id: UUID
    deal_id: UUID
    matched_at: datetime
    deal: DealResponse

    model_config = {"from_attributes": True}
