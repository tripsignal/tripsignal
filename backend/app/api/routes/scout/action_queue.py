"""Scout action-queue endpoint — prioritized list of actionable items."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.session import get_db

from .helpers import _get_user_and_signals, _region_label

router = APIRouter()


@router.get("/action-queue")
async def action_queue(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Prioritized list of things the user should act on."""
    user, signals = _get_user_and_signals(db, clerk_user_id)
    actions: list[dict] = []

    if not signals:
        actions.append({
            "priority": 1,
            "type": "create_signal",
            "title": "Create your first signal",
            "description": "Tell us where you want to go and we'll find deals for you.",
            "cta_label": "Create signal",
            "cta_href": "/signals/new",
        })
        return {"actions": actions}

    signal_ids = [s.id for s in signals]

    # Batch fetch data
    match_counts = dict(
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)
        .group_by(DealMatch.signal_id)
        .all()
    )

    fav_counts = dict(
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,
            DealMatch.is_favourite == True,
        )
        .group_by(DealMatch.signal_id)
        .all()
    )

    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    # Check for price drops
    drop_rows = db.execute(
        text("""
            SELECT dm.signal_id::text, dm.deal_id::text,
                   d.destination, d.hotel_name,
                   ph.prev_price, ph.price_cents,
                   (ph.prev_price - ph.price_cents) as drop_cents
            FROM deal_matches dm
            JOIN deals d ON d.id = dm.deal_id
            JOIN (
                SELECT deal_id, price_cents,
                       LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                       ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
                FROM deal_price_history
            ) ph ON ph.deal_id = dm.deal_id
            WHERE dm.signal_id = ANY(:signal_ids)
              AND d.is_active = true
              AND ph.rn = 1
              AND ph.prev_price IS NOT NULL
              AND ph.price_cents < ph.prev_price
            ORDER BY drop_cents DESC
            LIMIT 5
        """),
        {"signal_ids": [str(sid) for sid in signal_ids]},
    ).all()

    # Price drop actions (highest priority)
    for row in drop_rows:
        drop_cents = row[6]
        hotel = row[3] or _region_label(row[2])
        actions.append({
            "priority": 1,
            "type": "price_drop",
            "title": f"${drop_cents // 100} price drop on {hotel}",
            "description": f"Was ${row[4] // 100}, now ${row[5] // 100}",
            "signal_id": row[0],
            "deal_id": row[1],
            "cta_label": "View deal",
            "cta_href": f"/signals?expand={row[0]}",
        })

    # Signals with high value scores but no favourites
    for s in signals:
        intel = intel_map.get(s.id)
        matches = match_counts.get(s.id, 0)
        favs = fav_counts.get(s.id, 0)

        if intel and intel.value_score and intel.value_score >= 70 and favs == 0 and matches > 0:
            actions.append({
                "priority": 2,
                "type": "review_deals",
                "title": f"Strong deals on {s.name} — none favourited",
                "description": f"Value score {intel.value_score}/100 with {matches} active deal{'s' if matches != 1 else ''}.",
                "signal_id": str(s.id),
                "cta_label": "Review deals",
                "cta_href": f"/signals?expand={s.id}",
            })

    # Signals near all-time low
    for s in signals:
        intel = intel_map.get(s.id)
        if intel and intel.floor_proximity_pct is not None and intel.floor_proximity_pct <= 5:
            actions.append({
                "priority": 2,
                "type": "near_floor",
                "title": f"{s.name} is near all-time low",
                "description": f"Current prices are within {intel.floor_proximity_pct:.0f}% of the lowest we've ever seen.",
                "signal_id": str(s.id),
                "cta_label": "Check it out",
                "cta_href": f"/signals?expand={s.id}",
            })

    # Signals with zero matches
    for s in signals:
        if match_counts.get(s.id, 0) == 0:
            actions.append({
                "priority": 3,
                "type": "no_matches",
                "title": f"No deals yet for {s.name}",
                "description": "Try widening your dates or budget for more matches.",
                "signal_id": str(s.id),
                "cta_label": "Edit signal",
                "cta_href": f"/signals?expand={s.id}",
            })

    # Sort by priority
    actions.sort(key=lambda a: a["priority"])

    return {"actions": actions[:10]}
