"""Deal match endpoints."""

from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from app.db.session import get_db
from app.db.models.signal_run import SignalRun
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

    # Step 3: start a signal run
    # NOTE: run_type is REQUIRED (NOT NULL) in your DB schema
    run = SignalRun(
        signal_id=signal_id,
        run_type="manual",  # pick something simple for now
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
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
            db.commit()
            db.refresh(match)
        except IntegrityError:
            # Match already exists (idempotent); fetch it and stamp run_id
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
                raise Exception("IntegrityError occurred but existing DealMatch not found.")

            match.run_id = run.id
            db.add(match)
            db.commit()
            db.refresh(match)

        # Step 3: mark run success (use your actual column name: completed_at)
        run.status = "success"
        run.completed_at = datetime.now(timezone.utc)

        # Optional: if you want run stats to be meaningful
        run.matches_created_count = 1 if created_new else 0

        db.add(run)
        db.commit()

        return DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            deal=match.deal,
        )

    except Exception as e:
        # Step 3: mark run failed
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise

