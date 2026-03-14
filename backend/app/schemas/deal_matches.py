from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PriceHistoryEntry(BaseModel):
    price_cents: int
    recorded_at: datetime


class DealOut(BaseModel):
    id: UUID
    provider: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    depart_date: Optional[date] = None
    return_date: Optional[date] = None
    price_cents: Optional[int] = None
    currency: Optional[str] = None
    deeplink_url: Optional[str] = None
    airline: Optional[str] = None
    cabin: Optional[str] = None
    stops: Optional[int] = None
    dedupe_key: Optional[str] = None
    price_trend: Optional[str] = None
    previous_price_cents: Optional[int] = None
    price_delta_cents: Optional[int] = None
    is_active: Optional[bool] = None
    deactivated_at: Optional[datetime] = None
    hotel_name: Optional[str] = None
    hotel_id: Optional[str] = None
    discount_pct: Optional[int] = None
    destination_str: Optional[str] = None
    star_rating: Optional[float] = None
    tripadvisor_url: Optional[str] = None
    found_at: Optional[datetime] = None
    first_price_cents: Optional[int] = None
    reactivated_at: Optional[datetime] = None
    price_history: Optional[list[PriceHistoryEntry]] = None

    class Config:
        from_attributes = True


class DealMatchOut(BaseModel):
    id: UUID
    matched_at: datetime
    is_favourite: bool = False
    value_label: Optional[str] = None
    deal: DealOut

    class Config:
        from_attributes = True


class DailyPricePoint(BaseModel):
    date: str
    price_cents: int


class PriceHistoryDetail(BaseModel):
    history: list[DailyPricePoint]
    first_price_cents: int
    current_price_cents: int
