"""Deal match endpoints."""

from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.db.models.deal_match import DealMatch
from app.db.models.deal import Deal
from app.schemas.deal_matches import DealMatchOut
from app.schemas.deals import DealMatchCreate  # expects {"deal_id": "..."} payload

router = APIRouter(prefix="/signals", tags=["matches"])


@router.get("/{signal_id}/matches", response_model=List[DealMatchOut])
def list_signal_matches(
    signal_id: UUID,
    db: Session = Depends(get_db),
):
    """Return all deals matched to a given signal."""
    matches = (
        db.query(DealMatch)
        .join(Deal)  # optional if relationship exists; safe to keep
        .filter(DealMatch.signal_id == signal_id)
        .order_by(DealMatch.matched_at.desc())
        .all()
    )

    return [
        DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            deal=match.deal,
        )
        for match in matches
    ]


@router.post("/{signal_id}/matches", response_model=DealMatchOut, status_code=201)
def create_signal_match(
    signal_id: UUID,
    payload: DealMatchCreate,
    db: Session = Depends(get_db),
):
    """Create a match between a signal and a deal (idempotent)."""
    match = DealMatch(signal_id=signal_id, deal_id=payload.deal_id)
    db.add(match)

    try:
        db.commit()
        db.refresh(match)
    except IntegrityError:
        db.rollback()
        match = (
            db.query(DealMatch)
            .filter(
                DealMatch.signal_id == signal_id,
                DealMatch.deal_id == payload.deal_id,
            )
            .first()
        )

    return DealMatchOut(
        id=match.id,
        matched_at=match.matched_at,
        deal=match.deal,
    )

