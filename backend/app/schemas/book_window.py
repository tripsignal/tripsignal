"""Book Window schemas — booking timing recommendation."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class BookWindowFactor(BaseModel):
    name: str  # e.g. "trend_direction", "seasonal_pattern", "inventory_pressure"
    signal: str  # e.g. "declining", "favorable", "tightening"
    description: str


class BookWindowResult(BaseModel):
    signal_id: str
    recommendation: str  # 'book_now' | 'wait' | 'watch'
    confidence: str  # 'high' | 'medium' | 'low'
    reasoning: str
    factors: List[BookWindowFactor]
    data_points: int


class BookWindowOut(BaseModel):
    signal_id: str
    signal_name: str
    route_label: str  # e.g. "YQR -> Riviera Maya"
    result: Optional[BookWindowResult] = None
