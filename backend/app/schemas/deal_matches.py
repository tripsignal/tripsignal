"""Pydantic schemas for DealMatch operations."""
from datetime import date
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict


class DealMatchCreate(BaseModel):
    """Schema for creating a deal match."""

    deal_id: UUID

    model_config = ConfigDict(from_attributes=True)


class DealMatchCreateRequest(BaseModel):
    """Schema for batch creating deal matches."""

    matches: list[DealMatchCreate]

    model_config = ConfigDict(from_attributes=True)


class DealOut(BaseModel):
    """Schema for Deal output."""

    id: UUID
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
    dedupe_key: str

    model_config = ConfigDict(from_attributes=True)


class DealMatchOut(BaseModel):
    """Schema for DealMatch output."""

    id: UUID
    signal_id: UUID
    deal_id: UUID
    matched_at: datetime
    deal: DealOut

    model_config = ConfigDict(from_attributes=True)


class DealMatchBatchResponse(BaseModel):
    """Schema for batch deal match creation response."""

    created: int
    matches: list[DealMatchOut]

    model_config = ConfigDict(from_attributes=True)
