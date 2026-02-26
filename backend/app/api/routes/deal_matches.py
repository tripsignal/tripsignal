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
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.notification_outbox import NotificationOutbox
from app.schemas.deal_matches import DealMatchOut, DealOut
from app.schemas.deals import DealMatchCreate

router = APIRouter(prefix="/signals", tags=["matches"])


def get_price_trend(db: Session, deal_id: UUID):
    """Return (price_trend, previous_price_cents) for a deal based on price history."""
    history = (
        db.query(DealPriceHistory)
        .filter(DealPriceHistory.deal_id == deal_id)
        .order_by(DealPriceHistory.recorded_at.desc())
        .limit(2)
        .all()
    )
    if len(history) < 2:
        return None, None

    current = history[0].price_cents
    previous = history[1].price_cents
    change_pct = (current - previous) / previous * 100

    if change_pct <= -5:
        return "down", previous
    elif change_pct >= 5:
        return "up", previous
    else:
        return "stable", previous


@router.get("/{signal_id}/matches", response_model=List[DealMatchOut])
def list_signal_matches(
    signal_id: UUID,
    db: Session = Depends(get_db),
):
    """Return all deals matched to a given signal, with price trend."""
    matches = (
        db.query(DealMatch)
        .join(Deal)
        .filter(DealMatch.signal_id == signal_id)
        .order_by(DealMatch.matched_at.desc())
        .all()
    )

    result = []
    for match in matches:
        trend, previous_price = get_price_trend(db, match.deal.id)
        deal_out = DealOut(
            id=match.deal.id,
            provider=match.deal.provider,
            origin=match.deal.origin,
            destination=match.deal.destination,
            depart_date=match.deal.depart_date,
            return_date=match.deal.return_date,
            price_cents=match.deal.price_cents,
            currency=match.deal.currency,
            deeplink_url=match.deal.deeplink_url,
            airline=match.deal.airline,
            cabin=match.deal.cabin,
            stops=match.deal.stops,
            dedupe_key=match.deal.dedupe_key,
            price_trend=trend,
            previous_price_cents=previous_price,
        )
        result.append(DealMatchOut(id=match.id, matched_at=match.matched_at, deal=deal_out))

    return result


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

        if created_new:
            deal = match.deal

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
                    to_email="log",
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
