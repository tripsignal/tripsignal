"""Deal match endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.session import get_db
from app.schemas.deal_matches import (
    DealMatchBatchResponse,
    DealMatchCreateRequest,
    DealMatchOut,
    DealOut,
)

router = APIRouter(prefix="/api/signals", tags=["deal_matches"])


@router.post("/{signal_id}/matches", response_model=DealMatchBatchResponse)
async def create_deal_matches(
    signal_id: UUID,
    request: DealMatchCreateRequest,
    db: Session = Depends(get_db),
) -> DealMatchBatchResponse:
    """Create deal matches for a signal."""
    # Validate that the Signal exists
    signal = db.query(Signal).filter(Signal.id == signal_id).first()
    if not signal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )

    # Validate that all deal_id values exist
    deal_ids = [match.deal_id for match in request.matches]
    if not deal_ids:
        return DealMatchBatchResponse(created=0, matches=[])

    # De-duplicate incoming deal_ids in memory
    unique_deal_ids = list(set(deal_ids))

    existing_deals = db.query(Deal.id).filter(Deal.id.in_(unique_deal_ids)).all()
    existing_deal_ids = {deal.id for deal in existing_deals}
    missing_deal_ids = [deal_id for deal_id in unique_deal_ids if deal_id not in existing_deal_ids]

    if missing_deal_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "One or more deal_id values do not exist",
                "missing_deal_ids": missing_deal_ids,
            },
        )

    # Insert DealMatch rows using Postgres ON CONFLICT DO NOTHING
    rows = [{"signal_id": signal_id, "deal_id": deal_id} for deal_id in unique_deal_ids]
    stmt = (
        insert(DealMatch)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["signal_id", "deal_id"])
        .returning(DealMatch.id)
    )
    result = db.execute(stmt)
    created_ids = result.scalars().all()
    created = len(created_ids)
    db.commit()

    # Query all DealMatch rows for this signal with Deal relationship loaded
    stmt = (
        select(DealMatch)
        .filter(DealMatch.signal_id == signal_id)
        .options(joinedload(DealMatch.deal))
        .order_by(DealMatch.matched_at.desc())
    )
    deal_matches = db.execute(stmt).unique().scalars().all()

    # Convert to response schema
    matches = []
    for dm in deal_matches:
        deal = dm.deal
        matches.append(
            DealMatchOut(
                id=dm.id,
                signal_id=dm.signal_id,
                deal_id=dm.deal_id,
                matched_at=dm.matched_at,
                deal=DealOut(
                    id=deal.id,
                    provider=deal.provider,
                    origin=deal.origin,
                    destination=deal.destination,
                    depart_date=deal.depart_date,
                    return_date=deal.return_date,
                    price_cents=deal.price_cents,
                    currency=deal.currency,
                    deeplink_url=deal.deeplink_url,
                    airline=deal.airline,
                    cabin=deal.cabin,
                    stops=deal.stops,
                    found_at=deal.found_at,
                    dedupe_key=deal.dedupe_key,
                ),
            )
        )

    return DealMatchBatchResponse(created=created, matches=matches)
