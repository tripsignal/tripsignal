"""Scout insights schemas — unified response for the Scout page."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from app.schemas.book_window import BookWindowOut


class BriefingSummary(BaseModel):
    total_signals: int
    active_deals: int
    price_drops_today: int
    new_deals_today: int
    headline: str
    subtext: str
    mood: str  # 'positive' | 'caution' | 'neutral'
    book_now_nudge: Optional[str] = None  # e.g. "We think it's time to book your Punta Cana trip"


class ActionItem(BaseModel):
    type: str  # 'price_drop' | 'below_budget' | 'low_inventory' | 'new_deal' | 'near_floor' | 'review_deals'
    signal_id: str
    signal_name: str
    route_label: str
    headline: str
    detail: str
    deal_id: Optional[str] = None
    urgency: str  # 'high' | 'medium' | 'low'


class ScoutDeal(BaseModel):
    signal_id: str
    match_id: str
    destination: str
    hotel_name: Optional[str] = None
    star_rating: Optional[float] = None
    price_cents: int
    price_trend: str  # 'up' | 'down' | 'stable'
    price_delta_cents: Optional[int] = None
    vs_typical: Optional[str] = None
    nights: Optional[int] = None
    departure_date: Optional[str] = None
    departure_airport: Optional[str] = None
    deal_url: Optional[str] = None
    is_favourite: bool = False


class SignalPriceContext(BaseModel):
    signal_id: str
    signal_name: str
    route_label: str
    current_avg_cents: int
    range_low_cents: int
    range_high_cents: int
    percentile: float  # 0.0 to 1.0
    great_deal_cents: Optional[int] = None
    typical_cents: Optional[int] = None
    pricey_cents: Optional[int] = None
    cheapest_ever_cents: Optional[int] = None
    market_label: str  # 'low' | 'average' | 'high' | 'very_high'
    data_points: int
    time_range_days: int


class ScoutInsights(BaseModel):
    briefing: BriefingSummary
    action_items: List[ActionItem]
    best_deals: List[ScoutDeal]
    price_context: List[SignalPriceContext]
    book_windows: List[BookWindowOut]
    next_scan_at: Optional[str] = None
