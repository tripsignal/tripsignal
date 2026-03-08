"""Deal match endpoints."""

from uuid import UUID
from typing import List
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_clerk_user_id
from app.db.session import get_db
from app.db.models.signal import Signal
from app.db.models.signal_run import SignalRun
from app.db.models.deal_match import DealMatch
from app.db.models.user import User
from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.hotel_link import HotelLink
from app.db.models.notification_outbox import NotificationOutbox
from app.schemas.deal_matches import DealMatchOut, DealOut
from app.schemas.deals import DealMatchCreate

router = APIRouter(prefix="/signals", tags=["matches"])


def _verify_signal_owner(signal_id: UUID, clerk_user_id: str, db: Session) -> Signal:
    """Verify the caller owns the signal. Returns the signal or raises 404."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    signal = db.query(Signal).filter(Signal.id == signal_id, Signal.user_id == user.id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


def get_price_trend(db: Session, deal_id: UUID):
    """Return (price_trend, previous_price_cents, delta_cents) comparing current vs previous price."""
    history = (
        db.query(DealPriceHistory)
        .filter(DealPriceHistory.deal_id == deal_id)
        .order_by(DealPriceHistory.recorded_at.asc())
        .all()
    )
    if len(history) < 2:
        return None, None, None

    previous_price = history[-2].price_cents
    current_price = history[-1].price_cents
    delta_cents = current_price - previous_price

    if delta_cents < 0:
        return "down", previous_price, abs(delta_cents)
    elif delta_cents > 0:
        return "up", previous_price, delta_cents
    else:
        return "stable", previous_price, 0


def _batch_price_trends(db: Session, deal_ids: list[UUID]) -> dict[UUID, tuple]:
    """Batch-fetch price trends for multiple deals in a single query.

    Returns {deal_id: (trend, previous_price_cents, abs_delta_cents)}.
    """
    if not deal_ids:
        return {}

    rows = (
        db.query(DealPriceHistory)
        .filter(DealPriceHistory.deal_id.in_(deal_ids))
        .order_by(DealPriceHistory.deal_id, DealPriceHistory.recorded_at.asc())
        .all()
    )

    # Group by deal_id
    by_deal: dict[UUID, list] = {}
    for row in rows:
        by_deal.setdefault(row.deal_id, []).append(row)

    result = {}
    for did, history in by_deal.items():
        if len(history) < 2:
            result[did] = (None, None, None)
            continue
        previous_price = history[-2].price_cents
        current_price = history[-1].price_cents
        delta = current_price - previous_price
        if delta < 0:
            result[did] = ("down", previous_price, abs(delta))
        elif delta > 0:
            result[did] = ("up", previous_price, delta)
        else:
            result[did] = ("stable", previous_price, 0)

    return result


@router.get("/{signal_id}/matches", response_model=List[DealMatchOut])
def list_signal_matches(
    signal_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Return active deals matched to a given signal, favourites first."""
    _verify_signal_owner(signal_id, clerk_user_id, db)
    matches = (
        db.query(DealMatch)
        .join(Deal)
        .filter(
            DealMatch.signal_id == signal_id,
            Deal.is_active == True,
        )
        .order_by(DealMatch.is_favourite.desc(), DealMatch.matched_at.desc())
        .all()
    )

    # Batch-fetch TripAdvisor URLs for all hotels in this result set
    hotel_ids = [m.deal.hotel_id for m in matches if m.deal.hotel_id]
    ta_urls: dict[str, str] = {}
    if hotel_ids:
        rows = (
            db.query(HotelLink.hotel_id, HotelLink.tripadvisor_url)
            .filter(HotelLink.hotel_id.in_(hotel_ids), HotelLink.tripadvisor_url.isnot(None))
            .all()
        )
        ta_urls = {r.hotel_id: r.tripadvisor_url for r in rows}

    # Batch-fetch price trends (fixes N+1)
    deal_ids = [m.deal.id for m in matches]
    price_trends = _batch_price_trends(db, deal_ids)

    result = []
    for match in matches:
        trend, previous_price, delta_cents = price_trends.get(match.deal.id, (None, None, None))
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
            price_delta_cents=delta_cents,
            is_active=match.deal.is_active,
            hotel_name=match.deal.hotel_name,
            hotel_id=match.deal.hotel_id,
            discount_pct=match.deal.discount_pct,
            destination_str=match.deal.destination_str,
            star_rating=match.deal.star_rating,
            tripadvisor_url=ta_urls.get(match.deal.hotel_id),
        )
        result.append(DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            is_favourite=match.is_favourite,
            value_label=match.value_label,
            deal=deal_out,
        ))

    return result


@router.patch("/{signal_id}/matches/{match_id}/favourite", response_model=DealMatchOut)
def toggle_favourite(
    signal_id: UUID,
    match_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Toggle the favourite status of a deal match."""
    _verify_signal_owner(signal_id, clerk_user_id, db)
    match = db.query(DealMatch).filter(
        DealMatch.id == match_id,
        DealMatch.signal_id == signal_id,
    ).first()

    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    match.is_favourite = not match.is_favourite
    db.commit()
    db.refresh(match)

    trend, previous_price, delta_cents = get_price_trend(db, match.deal.id)
    ta_url = None
    if match.deal.hotel_id:
        ta_url = db.query(HotelLink.tripadvisor_url).filter(
            HotelLink.hotel_id == match.deal.hotel_id
        ).scalar()
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
        price_delta_cents=delta_cents,
        is_active=match.deal.is_active,
        hotel_name=match.deal.hotel_name,
        hotel_id=match.deal.hotel_id,
        discount_pct=match.deal.discount_pct,
        destination_str=match.deal.destination_str,
        star_rating=match.deal.star_rating,
        tripadvisor_url=ta_url,
    )

    return DealMatchOut(
        id=match.id,
        matched_at=match.matched_at,
        is_favourite=match.is_favourite,
        value_label=match.value_label,
        deal=deal_out,
    )


@router.post("/{signal_id}/matches", response_model=DealMatchOut, status_code=201)
def create_signal_match(
    signal_id: UUID,
    payload: DealMatchCreate,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Create a match between a signal and a deal (idempotent)."""
    _verify_signal_owner(signal_id, clerk_user_id, db)

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
            is_favourite=match.is_favourite,
            deal=match.deal,
        )

    except Exception as e:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise
