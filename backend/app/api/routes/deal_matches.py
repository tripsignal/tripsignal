"""Deal match endpoints."""

from uuid import UUID
from typing import List
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.db.models.signal_run import SignalRun
from app.db.models.deal_match import DealMatch
from app.db.models.deal import Deal
from app.db.models.notification_outbox import NotificationOutbox
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


@router.post("/{signal_id}/matches", response_model=DealMatchOut, status_code=201)
def create_signal_match(
    signal_id: UUID,
    payload: DealMatchCreate,
    db: Session = Depends(get_db),
):
    """Create a match between a signal and a deal (idempotent)."""

    run = SignalRun(
        signal_id=signal_id,
        run_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.refresh(run)

    try:
        match = DealMatch(
            signal_id=signal_id,
            deal_id=payload.deal_id,
            run_id=run.id,
        )
        db.add(match)

        created_new = True

        try:
            db.flush()
            db.refresh(match)
        except IntegrityError:
            db.rollback()
            created_new = False

            match = (
                db.query(DealMatch)
                .filter(
                    DealMatch.signal_id == signal_id,
                    DealMatch.deal_id == payload.deal_id,
                )
                .first()
            )

            if match is None:
                raise Exception("IntegrityError but DealMatch not found")

            match.run_id = run.id
            db.add(match)
            db.flush()
            db.refresh(match)

        # Enqueue outbox row ONLY if this is a new match (same transaction)
        if created_new:
            deal = match.deal  # should exist

            subject = f"TripSignal match: {deal.origin}->{deal.destination} ${deal.price_cents/100:.2f} {deal.currency}"
            body = (
                f"New deal match\n"
                f"signal_id: {signal_id}\n"
                f"match_id: {match.id}\n"
                f"deal_id: {deal.id}\n"
                f"route: {deal.origin} -> {deal.destination}\n"
                f"dates: {deal.depart_date} to {deal.return_date}\n"
                f"price: {deal.price_cents} {deal.currency}\n"
                f"provider: {deal.provider}\n"
                f"link: {deal.deeplink_url}\n"
                f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
            )

            db.add(
                NotificationOutbox(
                    status="pending",
                    channel="log",
                    signal_id=signal_id,
                    match_id=match.id,
                    to_email="log",  # required NOT NULL in schema
                    subject=subject,
                    body_text=body,
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )


        run.status = "success"
        run.completed_at = datetime.now(timezone.utc)
        run.matches_created_count = 1 if created_new else 0

        db.add(run)
        db.commit()

        return DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            deal=match.deal,
        )

    except Exception as e:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise
