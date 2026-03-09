"""Scout V2 briefing endpoint — card-ready intelligence briefing.

Returns a simplified, pre-ranked response that the frontend renders directly
with zero aggregation. Parallel to V1 /api/scout/insights.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal import Signal
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.signal_run import SignalRun
from app.db.session import get_db
from app.services.book_window import get_book_window
from app.services.market_intel import (
    build_market_bucket_from_signal,
    compute_empty_state_insights,
    compute_market_stats,
    score_deal,
)

from .helpers import (
    AIRPORT_CITY_MAP,
    _build_route_label,
    _get_user_and_signals,
    logger,
)

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STILL_LEARNING_THRESHOLD = 20  # data_points below this → still_learning
DURABILITY_MIN_MATCHES = 3  # need at least this many matches to assess durability

VALUE_LABEL_WEIGHTS = {"strongest": 4, "promising": 3, "watching": 2, "quiet": 1}
CONFIDENCE_WEIGHTS = {"high": 3, "medium": 2, "low": 1}

# Suggestion base priorities
SUGGESTION_BASE = {
    "book_soon": 100,
    "nearby_airport": 90,
    "budget_unlock": 80,
    "date_flex": 75,
    "no_rush": 60,
}


# ──────────────────────────────────────────────────────────────────────────────
# Value label mapping
# ──────────────────────────────────────────────────────────────────────────────

def _compute_value_label(
    deal_score_label: str | None,
    intel: SignalIntelCache | None,
    has_deals: bool,
) -> dict:
    """Map deal scoring + intel into a V2 value label."""
    if not has_deals:
        return {"level": "quiet", "text": "Quiet"}

    if deal_score_label in ("Rare value", "Great value"):
        return {"level": "strongest", "text": "Strongest"}
    if deal_score_label == "Good price":
        return {"level": "promising", "text": "Promising"}
    if deal_score_label == "Typical price":
        return {"level": "watching", "text": "Watching"}
    if deal_score_label == "High for market":
        return {"level": "quiet", "text": "Quiet"}

    # Fallback: use value_score from intel cache
    if intel and intel.value_score is not None:
        if intel.value_score >= 70:
            return {"level": "promising", "text": "Promising"}
        if intel.value_score >= 40:
            return {"level": "watching", "text": "Watching"}
    return {"level": "watching", "text": "Watching"}


# ──────────────────────────────────────────────────────────────────────────────
# Deal durability — how risky is it to wait on a good deal?
# ──────────────────────────────────────────────────────────────────────────────

def _compute_durability(
    match_count: int,
    deal_score_label: str | None,
    book_window_result: dict | None,
    stats: object | None,
    best_deal_price_cents: int | None,
) -> str:
    """Assess how replaceable the current best deal appears.

    Returns one of: 'scarce', 'available', 'neutral', 'unknown'.

    This is NOT a prediction. It describes current market structure:
    - scarce: few comparable deals exist at this quality level right now
    - available: several similar options exist — deal is replaceable
    - neutral: no strong signal either way
    - unknown: insufficient data to assess
    """
    if match_count < DURABILITY_MIN_MATCHES or not deal_score_label:
        return "unknown"

    is_good_deal = deal_score_label in ("Rare value", "Great value", "Good price")

    # Extract inventory pressure from book window factors
    inventory_signal = None
    if book_window_result:
        for factor in book_window_result.get("factors", []):
            if factor.get("name") == "inventory_pressure":
                inventory_signal = factor.get("signal")
                break

    # Check how many comparable deals exist relative to what's typical
    # Low match count + good deal = scarce
    # High match count + good deal = available
    if is_good_deal:
        if inventory_signal == "tightening":
            return "scarce"
        if inventory_signal == "growing":
            return "available"
        # Use match count as a secondary heuristic
        if match_count <= 5:
            return "scarce"
        if match_count >= 15:
            return "available"
        return "neutral"

    # For typical/elevated deals, durability is less decision-useful
    if inventory_signal == "tightening":
        return "scarce"
    return "neutral"


# ──────────────────────────────────────────────────────────────────────────────
# Suggestion ranking
# ──────────────────────────────────────────────────────────────────────────────

def _build_suggestion(
    signal: Signal,
    book_window_result: dict | None,
    intel: SignalIntelCache | None,
    match_count: int,
    best_deal: dict | None,
    value_label_level: str,
    nearby_suggestion: dict | None,
    empty_state: dict | None,
    durability: str = "unknown",
) -> dict | None:
    """Pick the single best suggestion using deterministic scoring."""
    candidates: list[tuple[float, dict]] = []
    confidence = book_window_result.get("confidence", "low") if book_window_result else "low"
    conf_boost = CONFIDENCE_WEIGHTS.get(confidence, 1)
    val_boost = VALUE_LABEL_WEIGHTS.get(value_label_level, 1)

    # book_soon
    if (
        book_window_result
        and book_window_result.get("recommendation") == "book_now"
        and confidence in ("medium", "high")
    ):
        score = SUGGESTION_BASE["book_soon"] + conf_boost + val_boost
        detail = None
        if best_deal:
            star = best_deal.get("star_rating")
            price_dollars = best_deal["price_cents"] // 100
            star_text = f"{star:.0f}-star " if star else ""
            detail = f"Best available: {star_text}from ${price_dollars:,}."
        # Use durability to enrich the headline
        if durability == "scarce":
            headline = "If this trip fits, this is a strong price for this route."
        else:
            headline = "This price looks strong for the current market."
        candidates.append((score, {
            "type": "book_soon",
            "headline": headline,
            "detail": detail,
            "cta_href": f"/signals?expand={signal.id}",
        }))

    # nearby_airport
    if nearby_suggestion:
        savings = nearby_suggestion.get("savings_cents", 0)
        savings_boost = min(savings / 10000, 5)  # cap at 5 bonus points
        score = SUGGESTION_BASE["nearby_airport"] + savings_boost + conf_boost
        candidates.append((score, nearby_suggestion))

    # budget_unlock
    if match_count == 0 and empty_state and empty_state.get("recommended_adjustment") == "budget_flex":
        score = SUGGESTION_BASE["budget_unlock"] + conf_boost
        adj_val = empty_state.get("recommended_adjustment_value", "+$100")
        est = empty_state.get("additional_matches_estimate", 0)
        candidates.append((score, {
            "type": "budget_unlock",
            "headline": f"Increase budget {adj_val} to unlock deals",
            "detail": f"Could unlock ~{est} new deal{'s' if est != 1 else ''}." if est else None,
            "cta_href": f"/signals?expand={signal.id}",
        }))

    # date_flex
    if match_count == 0 and empty_state and empty_state.get("recommended_adjustment") == "date_flex":
        score = SUGGESTION_BASE["date_flex"] + conf_boost
        adj_val = empty_state.get("recommended_adjustment_value", "±3 days")
        est = empty_state.get("additional_matches_estimate", 0)
        candidates.append((score, {
            "type": "date_flex",
            "headline": f"Flex your dates {adj_val} to unlock deals",
            "detail": f"Could unlock ~{est} new deal{'s' if est != 1 else ''}." if est else None,
            "cta_href": f"/signals?expand={signal.id}",
        }))

    # no_rush
    if book_window_result and book_window_result.get("recommendation") == "wait":
        score = SUGGESTION_BASE["no_rush"] + conf_boost
        candidates.append((score, {
            "type": "no_rush",
            "headline": "Prices have been easing, and there's no strong pressure to book yet.",
            "detail": "We'll keep monitoring this route for you.",
            "cta_href": f"/signals?expand={signal.id}",
        }))

    if not candidates:
        return None

    # Pick highest score
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


# ──────────────────────────────────────────────────────────────────────────────
# Observation selection
# ──────────────────────────────────────────────────────────────────────────────

def _build_observations(
    intel: SignalIntelCache | None,
    book_window_result: dict | None,
    deal_score_label: str | None,
    price_delta_cents: int | None,
    price_delta_direction: str | None,
    match_count: int,
    suggestion_type: str | None,
    durability: str = "unknown",
) -> list[dict]:
    """Build max 2 observations using value-first priority.

    Slot priority:
    - Slot 1: VALUE (always first when available)
    - Slot 2: DURABILITY (when deal is good + durability is meaningful)
              or TREND (when value is typical/elevated or durability is weak)

    Decision rules:
    1. VALUE FIRST — slot 1 strongly prefers value context
    2. DURABILITY WHEN IT HELPS — for good/promising deals with meaningful signal
    3. TREND AS SECONDARY — when value is typical/elevated or durability is weak
    4. STILL LEARNING — for low-data routes, suppress overconfident advice
    """
    data_points = book_window_result.get("data_points", 0) if book_window_result else 0
    confidence = book_window_result.get("confidence", "low") if book_window_result else "low"
    is_still_learning = confidence == "low" or data_points < STILL_LEARNING_THRESHOLD

    result: list[dict] = []

    # ── Still learning: special case ──
    if is_still_learning:
        result.append({"type": "still_learning", "text": "We're still learning this route."})
        # Allow one more gentle observation for still-learning routes
        if match_count > 0 and deal_score_label == "Typical price":
            result.append({"type": "value_label", "text": "Prices are typical for this route right now."})
        elif match_count == 0:
            result.append({"type": "still_learning", "text": "Few useful price signals yet."})
        return result[:2]

    is_good_deal = deal_score_label in ("Rare value", "Great value", "Good price")

    # ── Slot 1: VALUE ──
    if deal_score_label and match_count > 0:
        if is_good_deal:
            delta_text = ""
            if price_delta_cents and price_delta_direction == "below":
                delta_text = f" — about ${price_delta_cents // 100:,} below typical prices"
            text = f"Good value{delta_text}."
            # Suppress if book_soon already covers value strongly
            if not (suggestion_type == "book_soon" and (not price_delta_cents or price_delta_cents < 5000)):
                result.append({"type": "value_label", "text": text})
        elif deal_score_label == "Typical price":
            result.append({"type": "value_label", "text": "Prices are typical for this route right now."})
        elif deal_score_label == "High for market":
            delta_text = ""
            if price_delta_cents and price_delta_direction == "above":
                delta_text = f" — about ${price_delta_cents // 100:,} above typical"
            result.append({"type": "value_label", "text": f"Prices are currently above normal deal levels{delta_text}."})

    # ── Slot 2: DURABILITY or TREND ──
    if len(result) < 2:
        slot2_filled = False

        # Prefer DURABILITY when deal is good and durability is meaningful
        if is_good_deal and durability in ("scarce", "available"):
            if durability == "scarce":
                result.append({"type": "durability", "text": "Fewer similar deals are available right now."})
                slot2_filled = True
            elif durability == "available":
                result.append({"type": "durability", "text": "Several similar options are still available."})
                slot2_filled = True

        # Fall back to TREND when durability doesn't apply
        if not slot2_filled and intel and intel.trend_direction and match_count > 0:
            trend_map = {
                "falling": "Prices have been easing recently.",
                "down": "Prices have been easing recently.",
                "declining": "Prices have been easing recently.",
                "rising": "Prices have been rising recently.",
                "up": "Prices have been rising recently.",
                "stable": "Prices have been stable recently.",
            }
            trend_text = trend_map.get(intel.trend_direction)
            if trend_text:
                # Suppress if no_rush suggestion already conveys easing
                if not (suggestion_type == "no_rush" and "easing" in trend_text.lower()):
                    result.append({"type": "trend", "text": trend_text})
                    slot2_filled = True

        # Last resort for slot 2: inventory (only if durability didn't surface it)
        if not slot2_filled and durability not in ("scarce", "available"):
            if book_window_result and match_count > 0:
                for factor in book_window_result.get("factors", []):
                    if factor.get("name") == "inventory_pressure":
                        inv_sig = factor.get("signal", "")
                        if inv_sig == "growing":
                            result.append({"type": "inventory", "text": "More options appearing for this route."})
                        elif inv_sig == "tightening":
                            result.append({"type": "inventory", "text": "Fewer options available for this route."})
                        break

    return result[:2]


# ──────────────────────────────────────────────────────────────────────────────
# Confidence copy
# ──────────────────────────────────────────────────────────────────────────────

def _build_confidence(book_window_result: dict | None) -> dict:
    """Generate confidence block.

    The confidence object remains in the API contract for technical consistency,
    but user-facing text avoids raw data-point jargon. Only surface meaningful
    supporting text or nothing at all.
    """
    if not book_window_result:
        return {
            "level": "low",
            "text": "",
            "data_points": 0,
        }

    confidence = book_window_result.get("confidence", "low")
    data_points = book_window_result.get("data_points", 0)

    if confidence == "high":
        text = ""  # High confidence needs no qualifier — let observations speak
    elif confidence == "medium":
        text = ""  # Medium confidence — observations already convey the picture
    else:
        text = ""  # Low data is handled by still_learning observation

    return {"level": confidence, "text": text, "data_points": data_points}


# ──────────────────────────────────────────────────────────────────────────────
# Nearby airport suggestion
# ──────────────────────────────────────────────────────────────────────────────

def _find_nearby_airport_suggestion(
    db: Session,
    signal: Signal,
    best_deal: Deal | None,
) -> dict | None:
    """Compare same hotel across different origins for savings.

    Only eligible when:
    - same hotel_id, different origin, origin in signal's departure_airports
    - same destination, matching trip length, matching departure date
    - alternate deal is active and bookable
    - positive, meaningful savings (>= $50)
    """
    if not best_deal or not best_deal.hotel_id or not best_deal.origin:
        return None

    allowed_airports = set(signal.departure_airports or [])
    if len(allowed_airports) < 2:
        return None

    alternate_origins = allowed_airports - {best_deal.origin}
    if not alternate_origins:
        return None

    best_nights = None
    if best_deal.depart_date and best_deal.return_date:
        best_nights = (best_deal.return_date - best_deal.depart_date).days

    # Find comparable deals from alternate origins
    query = (
        db.query(Deal)
        .filter(
            Deal.hotel_id == best_deal.hotel_id,
            Deal.origin.in_(alternate_origins),
            Deal.destination == best_deal.destination,
            Deal.is_active == True,  # noqa: E712
            Deal.price_cents.isnot(None),
            Deal.price_cents > 0,
        )
    )

    # Match departure date exactly
    if best_deal.depart_date:
        query = query.filter(Deal.depart_date == best_deal.depart_date)

    # Match trip length
    if best_nights is not None:
        query = query.filter(
            Deal.return_date.isnot(None),
            func.extract("day", Deal.return_date - Deal.depart_date) == best_nights,
        )

    alt_deals = query.order_by(Deal.price_cents.asc()).limit(5).all()

    for alt in alt_deals:
        if alt.price_cents >= best_deal.price_cents:
            continue
        savings = best_deal.price_cents - alt.price_cents
        if savings < 5000:  # minimum $50 savings to be meaningful
            continue

        alt_city = AIRPORT_CITY_MAP.get(alt.origin, alt.origin)
        return {
            "type": "nearby_airport",
            "headline": f"Same hotel, ${savings // 100:,} less from {alt_city} ({alt.origin})",
            "detail": f"${alt.price_cents // 100:,} vs ${best_deal.price_cents // 100:,} from {AIRPORT_CITY_MAP.get(best_deal.origin, best_deal.origin)}.",
            "cta_href": f"/signals?expand={signal.id}",
            "savings_cents": savings,
        }

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Page summary
# ──────────────────────────────────────────────────────────────────────────────

def _build_summary(signals_data: list[dict]) -> str:
    """Deterministic page-level summary."""
    total = len(signals_data)
    has_book_soon = any(
        s.get("suggestion", {}).get("type") == "book_soon" if s.get("suggestion") else False
        for s in signals_data
    )
    with_deals = sum(1 for s in signals_data if s.get("deal_count", 0) > 0)

    if has_book_soon:
        return "One of your routes has a strong price right now."
    if with_deals > 0:
        return f"Tracking {total} signal{'s' if total != 1 else ''} — {with_deals} with deals."
    return f"Monitoring {total} signal{'s' if total != 1 else ''}. Deals will appear after the next scan."


# ──────────────────────────────────────────────────────────────────────────────
# Card ordering
# ──────────────────────────────────────────────────────────────────────────────

def _sort_signals(signals_data: list[dict]) -> list[dict]:
    """Deterministic ordering for display."""
    def sort_key(s):
        has_suggestion = 1 if s.get("suggestion") else 0
        val_weight = VALUE_LABEL_WEIGHTS.get(s.get("value_label", {}).get("level", "quiet"), 0)
        conf_weight = CONFIDENCE_WEIGHTS.get(s.get("confidence", {}).get("level", "low"), 0)
        deal_count = s.get("deal_count", 0)
        name = s.get("signal_name", "")
        return (-has_suggestion, -val_weight, -conf_weight, -deal_count, name)
    return sorted(signals_data, key=sort_key)


# ──────────────────────────────────────────────────────────────────────────────
# Main endpoint
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/briefing")
async def briefing(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Scout V2 briefing — card-ready intelligence for the simplified Scout page."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {
            "summary": "Create a signal to get started.",
            "signals": [],
            "next_scan_at": None,
            "meta": {"version": "v2"},
        }

    signal_ids = [s.id for s in signals]

    # ── Batch queries (reuse patterns from insights.py) ──

    # Active match counts
    match_count_rows = (
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)  # noqa: E712
        .group_by(DealMatch.signal_id)
        .all()
    )
    match_counts: dict[UUID, int] = dict(match_count_rows)

    # Intel caches
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    # Best deal per signal (cheapest active deal)
    best_deal_per_signal: dict[UUID, tuple] = {}
    best_deal_rows = (
        db.query(DealMatch, Deal)
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
            Deal.price_cents.isnot(None),
        )
        .order_by(Deal.price_cents.asc())
        .all()
    )
    for dm, deal in best_deal_rows:
        if dm.signal_id not in best_deal_per_signal:
            best_deal_per_signal[dm.signal_id] = (dm, deal)

    # Price deltas for best deals
    best_deal_ids = [deal.id for _, (_, deal) in best_deal_per_signal.items()]
    delta_map: dict[UUID, tuple[int, int]] = {}
    if best_deal_ids:
        delta_rows = db.execute(
            text("""
                SELECT deal_id, price_cents, prev_price
                FROM (
                    SELECT deal_id, price_cents,
                           LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                           ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
                    FROM deal_price_history
                    WHERE deal_id = ANY(:deal_ids)
                ) sub
                WHERE rn = 1 AND prev_price IS NOT NULL
            """),
            {"deal_ids": [str(did) for did in best_deal_ids]},
        ).all()
        for row in delta_rows:
            delta_map[row[0]] = (row[2], row[1])  # (prev_price, current_price)

    # Market stats + book windows per signal
    market_stats_map = {}
    book_window_map: dict[UUID, dict] = {}
    empty_state_map: dict[UUID, dict] = {}

    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if bucket:
            stats = compute_market_stats(db, bucket)
            if stats.sample_size >= 6:
                market_stats_map[s.id] = stats

        mc = match_counts.get(s.id, 0)
        if mc > 0:
            try:
                bw = get_book_window(s.id, s.name, _build_route_label(s), db)
                if bw.result:
                    book_window_map[s.id] = bw.result.model_dump()
            except Exception:
                logger.exception("Book window failed for signal %s", s.id)
        else:
            # Empty state insights for signals with 0 matches
            if bucket:
                try:
                    esi = compute_empty_state_insights(db, s, bucket)
                    empty_state_map[s.id] = esi.to_dict()
                except Exception:
                    logger.exception("Empty state insights failed for signal %s", s.id)

    # Next scan time
    latest_run = (
        db.query(SignalRun)
        .filter(SignalRun.signal_id.in_(signal_ids))
        .order_by(SignalRun.started_at.desc())
        .first()
    )
    next_scan_at = None
    if latest_run and latest_run.completed_at:
        next_scan = latest_run.completed_at + timedelta(hours=6)
        if next_scan > datetime.now(timezone.utc):
            next_scan_at = next_scan.isoformat()

    # ── Build per-signal cards ──

    signals_data = []
    for s in signals:
        mc = match_counts.get(s.id, 0)
        intel = intel_map.get(s.id)
        bw_result = book_window_map.get(s.id)
        stats = market_stats_map.get(s.id)
        empty_state = empty_state_map.get(s.id)

        # Best deal
        best_deal_dict = None
        best_deal_obj = None
        deal_score_label = None
        price_delta_cents = None
        price_delta_direction = None

        if s.id in best_deal_per_signal:
            dm, deal = best_deal_per_signal[s.id]
            best_deal_obj = deal

            # Score the best deal
            if stats and deal.price_cents:
                score_result = score_deal(deal.price_cents, stats)
                deal_score_label = score_result.label
                price_delta_cents = score_result.price_delta_amount
                price_delta_direction = score_result.price_delta_direction

            # Price trend from delta map
            delta_info = delta_map.get(deal.id)
            if delta_info:
                prev_price, cur_price = delta_info
                if cur_price < prev_price:
                    price_trend = "down"
                    deal_delta_cents = prev_price - cur_price
                elif cur_price > prev_price:
                    price_trend = "up"
                    deal_delta_cents = cur_price - prev_price
                else:
                    price_trend = "stable"
                    deal_delta_cents = None
            else:
                price_trend = "stable"
                deal_delta_cents = None

            nights = None
            if deal.depart_date and deal.return_date:
                nights = (deal.return_date - deal.depart_date).days

            best_deal_dict = {
                "match_id": str(dm.id),
                "hotel_name": deal.hotel_name,
                "star_rating": float(deal.star_rating) if deal.star_rating else None,
                "price_cents": deal.price_cents,
                "price_trend": price_trend,
                "price_delta_cents": deal_delta_cents,
                "deal_url": deal.deeplink_url,
                "departure_date": deal.depart_date.isoformat() if deal.depart_date else None,
                "nights": nights,
                "departure_airport": deal.origin,
            }

        # Value label
        value_label = _compute_value_label(deal_score_label, intel, mc > 0)

        # Deal durability
        best_deal_price = best_deal_dict["price_cents"] if best_deal_dict else None
        durability = _compute_durability(mc, deal_score_label, bw_result, stats, best_deal_price)

        # Nearby airport suggestion
        nearby = _find_nearby_airport_suggestion(db, s, best_deal_obj)

        # Suggestion
        suggestion = _build_suggestion(
            signal=s,
            book_window_result=bw_result,
            intel=intel,
            match_count=mc,
            best_deal=best_deal_dict,
            value_label_level=value_label["level"],
            nearby_suggestion=nearby,
            empty_state=empty_state,
            durability=durability,
        )

        # Observations
        observations = _build_observations(
            intel=intel,
            book_window_result=bw_result,
            deal_score_label=deal_score_label,
            price_delta_cents=price_delta_cents,
            price_delta_direction=price_delta_direction,
            match_count=mc,
            suggestion_type=suggestion.get("type") if suggestion else None,
            durability=durability,
        )

        # Confidence
        confidence = _build_confidence(bw_result)

        signals_data.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "route_label": _build_route_label(s),
            "value_label": value_label,
            "suggestion": suggestion,
            "observations": observations,
            "confidence": confidence,
            "best_deal": best_deal_dict,
            "deal_count": mc,
        })

    # ── Sort and build summary ──

    signals_data = _sort_signals(signals_data)
    summary = _build_summary(signals_data)

    return {
        "summary": summary,
        "signals": signals_data,
        "next_scan_at": next_scan_at,
        "meta": {"version": "v2"},
    }
