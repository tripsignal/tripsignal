from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


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

    class Config:
        from_attributes = True


class DealMatchOut(BaseModel):
    id: UUID
    matched_at: datetime
    deal: DealOut

    class Config:
        from_attributes = True
