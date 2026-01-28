"""Deal match endpoints."""

from uuid import UUID, uuid4
from typing import List
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

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

    # --- Create run (must exist before writing deal_matches.run_id) ---
    run = SignalRun(
        id=uuid4(),
        signal_id=signal_id,
        run_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()  # guarantees run.id exists for FK usage

    try:
        # --- Detect whether this match already existed (BEFORE upsert) ---
        created_new = (
            db.query(DealMatch.id)
            .filter(
                DealMatch.signal_id == signal_id,
                DealMatch.deal_id == payload.deal_id,
            )
            .limit(1)
            .scalar()
        ) is None

        # --- UPSERT deal match (no uniqueness errors, ever) ---
        stmt = (
            insert(DealMatch)
            .values(
                signal_id=signal_id,
                deal_id=payload.deal_id,
                run_id=run.id,
            )
            .on_conflict_do_update(
                constraint="uq_deal_matches_signal_deal",
                set_={"run_id": run.id},
            )
            .returning(DealMatch.id)
        )

        match_id = db.execute(stmt).scalar_one()

        match = (
            db.query(DealMatch)
            .filter(DealMatch.id == match_id)
            .first()
        )
        if match is None:
            raise Exception("DealMatch not found after upsert")

        # --- Notification cooldown enforcement (MVP) ---
        COOLDOWN_HOURS = 24
        cutoff = datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)

        recent_sent_count = (
            db.query(func.count(NotificationOutbox.id))
            .filter(
                NotificationOutbox.signal_id == signal_id,
                NotificationOutbox.sent_at.isnot(None),
                NotificationOutbox.sent_at >= cutoff,
            )
            .scalar()
        )

        can_notify = (recent_sent_count or 0) == 0

        # --- Enqueue notification ONLY if newly created AND cooldown allows ---
        if created_new and can_notify:
            deal = match.deal  # relationship should be present

            subject = (
                f"TripSignal match: "
                f"{deal.origin}->{deal.destination} "
                f"${deal.price_cents / 100:.2f} {deal.currency}"
            )

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
                    signal_id=signal_id,
                    match_id=match.id,
                    to_email="log",  # NOT NULL placeholder
                    subject=subject,
                    body_text=body,
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )

        # --- Finalize run ---
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
        # IMPORTANT: session is poisoned after flush/execute failure
        db.rollback()

        try:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = str(e)
            db.add(run)
            db.commit()
        except Exception:
            db.rollback()

        raise
