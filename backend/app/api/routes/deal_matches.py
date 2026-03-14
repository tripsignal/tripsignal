"""Deal match endpoints."""

from uuid import UUID
from typing import List, NamedTuple, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.db.models.signal import Signal
from app.db.models.signal_run import SignalRun
from app.db.models.deal_match import DealMatch
from app.db.models.user import User
from app.db.models.deal import Deal
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.hotel_link import HotelLink
from app.db.models.notification_outbox import NotificationOutbox
from app.schemas.deal_matches import DealMatchOut, DealOut, PriceHistoryDetail
from app.schemas.deals import DealMatchCreate
from app.services.formatting import normalize_destination_display

router = APIRouter(prefix="/signals", tags=["matches"])


def _verify_signal_owner(signal_id: UUID, clerk_user_id: str, db: Session) -> tuple[Signal, User]:
    """Verify the caller owns the signal. Returns (signal, user) or raises 404."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    signal = db.query(Signal).filter(Signal.id == signal_id, Signal.user_id == user.id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal, user


def _build_deal_out(
    deal: Deal,
    *,
    trend: Optional[str] = None,
    previous_price: Optional[int] = None,
    delta_cents: Optional[int] = None,
    first_price: Optional[int] = None,
    hist_rows: Optional[list] = None,
    ta_url: Optional[str] = None,
) -> DealOut:
    """Construct a DealOut from a Deal model — single source of truth for API response shape."""
    return DealOut(
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
        dedupe_key=deal.dedupe_key,
        price_trend=trend,
        previous_price_cents=previous_price,
        price_delta_cents=delta_cents,
        is_active=deal.is_active,
        deactivated_at=deal.deactivated_at,
        hotel_name=deal.hotel_name,
        hotel_id=deal.hotel_id,
        discount_pct=deal.discount_pct,
        destination_str=normalize_destination_display(deal.destination_str, deal.destination),
        star_rating=deal.star_rating,
        tripadvisor_url=ta_url,
        found_at=deal.found_at,
        first_price_cents=first_price,
        reactivated_at=deal.reactivated_at,
        price_history=hist_rows if hist_rows and len(hist_rows) > 1 else None,
    )


class PriceTrend(NamedTuple):
    trend: Optional[str]
    previous_price: Optional[int]
    delta_cents: Optional[int]
    first_price: Optional[int]
    hist_rows: Optional[list]


_EMPTY_TREND = PriceTrend(None, None, None, None, None)

# Cap price history rows sent to the frontend to keep payloads bounded
_MAX_HISTORY_ROWS = 30


def _batch_price_trends(db: Session, deal_ids: list[UUID]) -> dict[UUID, PriceTrend]:
    """Batch-fetch price trends for multiple deals in a single query."""
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

    result: dict[UUID, PriceTrend] = {}
    for did, history in by_deal.items():
        first_price = history[0].price_cents
        # Keep only the most recent entries for the response payload
        capped = history[-_MAX_HISTORY_ROWS:]
        hist_rows = [{"price_cents": h.price_cents, "recorded_at": h.recorded_at} for h in capped]
        if len(history) < 2:
            result[did] = PriceTrend(None, None, None, first_price, hist_rows)
            continue
        previous_price = history[-2].price_cents
        current_price = history[-1].price_cents
        delta = current_price - previous_price
        if delta < 0:
            result[did] = PriceTrend("down", previous_price, abs(delta), first_price, hist_rows)
        elif delta > 0:
            result[did] = PriceTrend("up", previous_price, delta, first_price, hist_rows)
        else:
            result[did] = PriceTrend("stable", previous_price, 0, first_price, hist_rows)

    return result


@router.get("/{signal_id}/matches", response_model=List[DealMatchOut])
@limiter.limit("30/minute")
def list_signal_matches(
    request: Request,
    signal_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Return all deals matched to a given signal — active first, then expired."""
    signal_obj, user = _verify_signal_owner(signal_id, clerk_user_id, db)
    is_pro = user.plan_type == "pro"
    matches = (
        db.query(DealMatch)
        .join(Deal)
        .options(joinedload(DealMatch.deal))
        .filter(DealMatch.signal_id == signal_id)
        .order_by(Deal.is_active.desc(), DealMatch.is_favourite.desc(), DealMatch.matched_at.desc())
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

    # Batch-fetch price trends
    deal_ids = [m.deal.id for m in matches]
    price_trends = _batch_price_trends(db, deal_ids)

    result = []
    for match in matches:
        pt = price_trends.get(match.deal.id, _EMPTY_TREND)
        deal_out = _build_deal_out(
            match.deal,
            trend=pt.trend,
            previous_price=pt.previous_price,
            delta_cents=pt.delta_cents,
            first_price=pt.first_price,
            hist_rows=pt.hist_rows,
            ta_url=ta_urls.get(match.deal.hotel_id),
        )
        result.append(DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            is_favourite=match.is_favourite,
            value_label=match.value_label if is_pro else None,
            deal=deal_out,
        ))

    return result


@router.patch("/{signal_id}/matches/{match_id}/favourite", response_model=DealMatchOut)
@limiter.limit("30/minute")
def toggle_favourite(
    request: Request,
    signal_id: UUID,
    match_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Toggle the favourite status of a deal match."""
    _, user = _verify_signal_owner(signal_id, clerk_user_id, db)
    is_pro = user.plan_type == "pro"
    match = db.query(DealMatch).filter(
        DealMatch.id == match_id,
        DealMatch.signal_id == signal_id,
    ).first()

    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    match.is_favourite = not match.is_favourite
    db.commit()
    db.refresh(match)

    pt = _batch_price_trends(db, [match.deal.id]).get(match.deal.id, _EMPTY_TREND)

    ta_url = None
    if match.deal.hotel_id:
        ta_url = db.query(HotelLink.tripadvisor_url).filter(
            HotelLink.hotel_id == match.deal.hotel_id
        ).scalar()

    deal_out = _build_deal_out(
        match.deal,
        trend=pt.trend,
        previous_price=pt.previous_price,
        delta_cents=pt.delta_cents,
        first_price=pt.first_price,
        hist_rows=pt.hist_rows,
        ta_url=ta_url,
    )

    return DealMatchOut(
        id=match.id,
        matched_at=match.matched_at,
        is_favourite=match.is_favourite,
        value_label=match.value_label if is_pro else None,
        deal=deal_out,
    )


@router.post("/{signal_id}/matches", response_model=DealMatchOut, status_code=201)
@limiter.limit("10/minute")
def create_signal_match(
    request: Request,
    signal_id: UUID,
    payload: DealMatchCreate,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Create a match between a signal and a deal (idempotent)."""
    _, user = _verify_signal_owner(signal_id, clerk_user_id, db)

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

        # Use savepoint so IntegrityError rollback doesn't destroy the SignalRun
        savepoint = db.begin_nested()
        try:
            savepoint.commit()
            db.refresh(match)
        except IntegrityError:
            savepoint.rollback()
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

        deal_out = _build_deal_out(match.deal)
        return DealMatchOut(
            id=match.id,
            matched_at=match.matched_at,
            is_favourite=match.is_favourite,
            deal=deal_out,
        )

    except Exception as e:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise


@router.get(
    "/{signal_id}/matches/{match_id}/price-history",
    response_model=PriceHistoryDetail,
)
@limiter.limit("30/minute")
def get_match_price_history(
    request: Request,
    signal_id: UUID,
    match_id: UUID,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Return daily-best price history for a specific deal match."""
    _verify_signal_owner(signal_id, clerk_user_id, db)

    match = (
        db.query(DealMatch)
        .options(joinedload(DealMatch.deal))
        .filter(DealMatch.id == match_id, DealMatch.signal_id == signal_id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    deal = match.deal

    # Aggregate: one row per day, best (min) price each day
    rows = (
        db.query(
            cast(DealPriceHistory.recorded_at, Date).label("day"),
            func.min(DealPriceHistory.price_cents).label("best_price"),
        )
        .filter(DealPriceHistory.deal_id == deal.id)
        .group_by("day")
        .order_by("day")
        .all()
    )

    history = [
        {"date": row.day.strftime("%b %d").replace(" 0", " "), "price_cents": row.best_price}
        for row in rows
    ]

    first_price = rows[0].best_price if rows else (deal.price_cents or 0)
    current_price = deal.price_cents or 0

    return PriceHistoryDetail(
        history=history,
        first_price_cents=first_price,
        current_price_cents=current_price,
    )
