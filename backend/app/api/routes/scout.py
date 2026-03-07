"""Scout page endpoints — personal travel intelligence briefing."""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.deal_price_history import DealPriceHistory
from app.db.models.route_intel_cache import RouteIntelCache
from app.db.models.signal import Signal
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.user import User
from app.db.session import get_db
from app.services.market_intel import (
    build_market_bucket_from_signal,
    compute_market_stats,
    score_deal,
    build_spectrum_data,
)

logger = logging.getLogger("scout")

router = APIRouter(prefix="/api/scout", tags=["scout"])

REGION_LABELS: dict[str, str] = {
    "mexico": "Mexico",
    "riviera_maya": "Riviera Maya",
    "cancun": "Cancún",
    "puerto_vallarta": "Puerto Vallarta",
    "los_cabos": "Los Cabos",
    "huatulco": "Huatulco",
    "puerto_escondido": "Puerto Escondido",
    "dominican_republic": "Dominican Republic",
    "punta_cana": "Punta Cana",
    "la_romana": "La Romana",
    "puerto_plata": "Puerto Plata",
    "samana": "Samaná",
    "santo_domingo": "Santo Domingo",
    "jamaica": "Jamaica",
    "montego_bay": "Montego Bay",
    "negril": "Negril",
    "ocho_rios": "Ocho Rios",
    "cuba": "Cuba",
    "varadero": "Varadero",
    "caribbean": "Caribbean",
    "costa_rica": "Costa Rica",
    "panama": "Panama",
    "barbados": "Barbados",
    "antigua": "Antigua",
    "saint_lucia": "Saint Lucia",
    "st_maarten": "St. Maarten",
    "grenada": "Grenada",
    "aruba": "Aruba",
    "curacao": "Curaçao",
    "bahamas": "Bahamas",
    "all_south": "All Destinations",
}


def _region_label(key: str) -> str:
    return REGION_LABELS.get(key, key.replace("_", " ").title())


def _get_user_and_signals(db: Session, clerk_user_id: str):
    """Shared helper: look up user + active signals."""
    user = db.query(User).filter(User.clerk_id == clerk_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    signals = (
        db.query(Signal)
        .filter(Signal.user_id == user.id, Signal.status == "active")
        .all()
    )
    return user, signals


# ── 1. Verdict ────────────────────────────────────────────────────────────────

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


# ── 2. Destinations ──────────────────────────────────────────────────────────

@router.get("/destinations")
async def destinations(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Per-destination price intelligence with sparkline data."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"destinations": []}

    signal_ids = [s.id for s in signals]

    # Collect unique (origin, destination_region) pairs from user's signals
    route_pairs: set[tuple[str, str]] = set()
    for s in signals:
        for apt in (s.departure_airports or []):
            for reg in (s.destination_regions or []):
                route_pairs.add((apt, reg))

    # Get matched deals grouped by destination
    matched_deals = (
        db.query(Deal)
        .join(DealMatch, DealMatch.deal_id == Deal.id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,
        )
        .all()
    )

    # Group deals by destination
    by_dest: dict[str, list[Deal]] = defaultdict(list)
    for d in matched_deals:
        by_dest[d.destination].append(d)

    # Get route intel for sparkline context
    route_intel_map: dict[tuple[str, str], RouteIntelCache] = {}
    if route_pairs:
        route_rows = db.query(RouteIntelCache).all()
        for r in route_rows:
            route_intel_map[(r.origin, r.destination_region)] = r

    # Get price history for sparklines — last 14 days, grouped by destination
    fourteen_days_ago = datetime.now(timezone.utc) - timedelta(days=14)
    dest_deal_ids = [d.id for deals in by_dest.values() for d in deals]

    sparkline_data: dict[str, list[dict]] = defaultdict(list)
    if dest_deal_ids:
        price_rows = (
            db.query(
                Deal.destination,
                func.date_trunc("day", DealPriceHistory.recorded_at).label("day"),
                func.min(DealPriceHistory.price_cents).label("min_price"),
            )
            .join(DealPriceHistory, DealPriceHistory.deal_id == Deal.id)
            .filter(
                Deal.id.in_(dest_deal_ids),
                DealPriceHistory.recorded_at >= fourteen_days_ago,
            )
            .group_by(Deal.destination, "day")
            .order_by("day")
            .all()
        )
        for row in price_rows:
            sparkline_data[row[0]].append({
                "date": row[1].strftime("%Y-%m-%d") if row[1] else None,
                "price_cents": row[2],
            })

    result = []
    for dest, deals in sorted(by_dest.items(), key=lambda x: len(x[1]), reverse=True):
        prices = [d.price_cents for d in deals]
        min_price = min(prices)
        median_price = sorted(prices)[len(prices) // 2]

        # Find WoW change from route intel
        wow_pct = None
        for (orig, reg), ri in route_intel_map.items():
            if reg == dest and ri.week_over_week_pct is not None:
                wow_pct = ri.week_over_week_pct
                break

        result.append({
            "destination": dest,
            "destination_label": _region_label(dest),
            "deal_count": len(deals),
            "min_price_cents": min_price,
            "median_price_cents": median_price,
            "week_over_week_pct": wow_pct,
            "sparkline": sparkline_data.get(dest, []),
        })

    return {"destinations": result}


# ── 3. Signal Health ──────────────────────────────────────────────────────────

@router.get("/signal-health")
async def signal_health(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Per-signal health overview: matches, trend, freshness."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"signals": []}

    signal_ids = [s.id for s in signals]

    # Batch match counts
    match_counts = dict(
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)
        .group_by(DealMatch.signal_id)
        .all()
    )

    # Intel caches
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    result = []
    for s in signals:
        active = match_counts.get(s.id, 0)
        intel = intel_map.get(s.id)

        # Freshness: how recently did we last check
        freshness = "stale"
        if s.last_check_at:
            hours_ago = (datetime.now(timezone.utc) - s.last_check_at).total_seconds() / 3600
            if hours_ago < 12:
                freshness = "fresh"
            elif hours_ago < 36:
                freshness = "recent"

        # Health status
        if active == 0:
            health = "no_matches"
        elif intel and intel.trend_direction == "falling":
            health = "improving"
        elif intel and intel.trend_direction == "rising":
            health = "worsening"
        else:
            health = "stable"

        result.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "departure_airports": s.departure_airports or [],
            "destination_regions": s.destination_regions or [],
            "active_matches": active,
            "health": health,
            "freshness": freshness,
            "last_check_at": s.last_check_at.isoformat() if s.last_check_at else None,
            "last_check_min_price": s.last_check_min_price,
            "all_time_low_price": s.all_time_low_price,
            "all_time_low_at": s.all_time_low_at.isoformat() if s.all_time_low_at else None,
            "trend_direction": intel.trend_direction if intel else None,
            "trend_consecutive_weeks": intel.trend_consecutive_weeks if intel else None,
            "value_score": intel.value_score if intel else None,
            "floor_proximity_pct": intel.floor_proximity_pct if intel else None,
        })

    return {"signals": result}


# ── 4. Price Baseline ─────────────────────────────────────────────────────────

@router.get("/price-baseline")
async def price_baseline(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Price distribution across user's signals — where do their deals sit?"""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"baselines": []}

    result = []
    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if not bucket:
            continue

        stats = compute_market_stats(db, bucket)
        spectrum = build_spectrum_data(stats, s.last_check_min_price)

        # Score the user's best deal if they have one
        value_label = None
        if s.last_check_min_price and stats.is_scorable():
            score_result = score_deal(s.last_check_min_price, stats)
            value_label = score_result.label

        result.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "spectrum": spectrum,
            "value_label": value_label,
            "best_price_cents": s.last_check_min_price,
            "all_time_low_cents": s.all_time_low_price,
            "sample_size": stats.sample_size,
        })

    return {"baselines": result}


# ── 5. Action Queue ───────────────────────────────────────────────────────────

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
            "description": f"Was ${row[4] // 100}pp, now ${row[5] // 100}pp",
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


# ── 6. Market Context ─────────────────────────────────────────────────────────

@router.get("/market-context")
async def market_context(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Platform-wide market context relevant to the user's signals."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    # Total active deals platform-wide
    total_active = db.query(func.count(Deal.id)).filter(Deal.is_active == True).scalar() or 0

    # Deals tracked today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    deals_today = (
        db.query(func.count(Deal.id))
        .filter(Deal.found_at >= today_start)
        .scalar()
    ) or 0

    # Provider breakdown
    provider_counts = dict(
        db.query(Deal.provider, func.count(Deal.id))
        .filter(Deal.is_active == True)
        .group_by(Deal.provider)
        .all()
    )

    # Top destinations by deal count
    top_dests = (
        db.query(Deal.destination, func.count(Deal.id).label("cnt"))
        .filter(Deal.is_active == True)
        .group_by(Deal.destination)
        .order_by(func.count(Deal.id).desc())
        .limit(5)
        .all()
    )

    # Route intel for user's routes — WoW trends
    route_trends = []
    if signals:
        user_routes: set[tuple[str, str]] = set()
        for s in signals:
            for apt in (s.departure_airports or []):
                for reg in (s.destination_regions or []):
                    user_routes.add((apt, reg))

        if user_routes:
            all_route_intel = db.query(RouteIntelCache).all()
            for ri in all_route_intel:
                if (ri.origin, ri.destination_region) in user_routes:
                    route_trends.append({
                        "origin": ri.origin,
                        "destination_region": ri.destination_region,
                        "destination_label": _region_label(ri.destination_region),
                        "week_over_week_pct": ri.week_over_week_pct,
                        "current_week_avg_cents": ri.current_week_avg_cents,
                        "late_booking_premium_pct": ri.late_booking_premium_pct,
                    })

    return {
        "total_active_deals": total_active,
        "deals_tracked_today": deals_today,
        "providers": provider_counts,
        "top_destinations": [
            {"destination": d[0], "label": _region_label(d[0]), "count": d[1]}
            for d in top_dests
        ],
        "route_trends": route_trends,
    }


# ── 7. What Is a Good Price ──────────────────────────────────────────────────

@router.get("/what-is-a-good-price")
async def what_is_a_good_price(
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Educational: price ranges for each of the user's signal routes."""
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {"routes": []}

    routes = []
    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if not bucket:
            continue

        stats = compute_market_stats(db, bucket)
        if stats.sample_size < 3:
            continue

        # What label would a deal at each price point get?
        labels = {}
        for label_name, price in [
            ("great", stats.p25_price),
            ("typical", stats.median_price),
            ("high", stats.p75_price),
        ]:
            if price:
                labels[label_name] = price

        routes.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "origins": s.departure_airports or [],
            "destinations": [_region_label(r) for r in (s.destination_regions or [])],
            "great_price_cents": stats.p25_price,
            "typical_price_cents": stats.median_price,
            "high_price_cents": stats.p75_price,
            "floor_price_cents": stats.min_price,
            "sample_size": stats.sample_size,
            "unique_resorts": stats.unique_resort_count,
        })

    return {"routes": routes}
