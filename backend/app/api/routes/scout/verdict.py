"""Scout verdict endpoint — overall 'should I book now?' assessment."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.session import get_db

from .helpers import _get_user_and_signals

router = APIRouter()


@router.get("/verdict")
async def verdict(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Overall 'should I book now?' assessment."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {
            "headline": "Set up a signal to get started",
            "subtext": "Create your first signal and we'll start tracking deals for you.",
            "mood": "neutral",
            "signals_count": 0,
            "matches_count": 0,
            "drops_count": 0,
            "best_value_signal": None,
        }

    signal_ids = [s.id for s in signals]

    # Active match count
    matches_count = (
        db.query(func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)
        .scalar()
    ) or 0

    # Price drops (deals where latest price < previous)
    drop_result = db.execute(
        text("""
            SELECT COUNT(DISTINCT dm.deal_id)
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
        """),
        {"signal_ids": [str(sid) for sid in signal_ids]},
    ).scalar() or 0

    # Intel caches for signals
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    # Find best value signal
    best_signal = None
    best_score = -1
    for s in signals:
        intel = intel_map.get(s.id)
        if intel and intel.value_score and intel.value_score > best_score:
            best_score = intel.value_score
            best_signal = s

    # Determine mood & headline
    if drop_result > 0:
        mood = "positive"
        headline = f"Prices are dropping — {drop_result} deal{'s' if drop_result != 1 else ''} just got cheaper"
        subtext = "Now could be a good time to book."
    elif matches_count > 0:
        # Check overall trend from intel caches
        falling_count = sum(
            1 for i in intel_rows if i.trend_direction == "falling"
        )
        rising_count = sum(
            1 for i in intel_rows if i.trend_direction == "rising"
        )
        if falling_count > rising_count:
            mood = "positive"
            headline = f"Prices trending down across {falling_count} signal{'s' if falling_count != 1 else ''}"
            subtext = "The market is moving in your favour."
        elif rising_count > falling_count:
            mood = "caution"
            headline = f"Prices are climbing on {rising_count} signal{'s' if rising_count != 1 else ''}"
            subtext = "Consider booking soon if you see a good deal."
        else:
            mood = "neutral"
            headline = f"Tracking {matches_count} active deal{'s' if matches_count != 1 else ''}"
            subtext = "Prices are holding steady. We'll alert you if anything changes."
    else:
        mood = "neutral"
        headline = "No matches yet — we're watching"
        subtext = "We'll notify you as soon as deals match your signals."

    return {
        "headline": headline,
        "subtext": subtext,
        "mood": mood,
        "signals_count": len(signals),
        "matches_count": matches_count,
        "drops_count": drop_result,
        "best_value_signal": {
            "signal_id": str(best_signal.id),
            "signal_name": best_signal.name,
            "value_score": best_score,
        } if best_signal else None,
    }
