"""Deal match endpoints."""

from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models.deal_match import DealMatch
from app.db.models.deal import Deal
from app.schemas.deal_matches import DealMatchOut


router = APIRouter(prefix="/signals", tags=["matches"])


@router.get("/{signal_id}/matches", response_model=List[DealMatchOut])
def list_signal_matches(
    signal_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Return all deals matched to a given signal.
    """
    matches = (
        db.query(DealMatch)
        .join(Deal)
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
 
