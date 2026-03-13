"""Scout insights endpoint — unified endpoint for the redesigned Scout page."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_clerk_user_id
from app.core.rate_limit import limiter
from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.signal_intel_cache import SignalIntelCache
from app.db.models.signal_run import SignalRun
from app.db.session import get_db
from app.services.book_window import get_book_window
from app.services.market_intel import (
    build_market_bucket_from_signal,
    compute_market_stats,
)

from .helpers import (
    _build_route_label,
    _get_user_and_signals,
    _region_label,
    logger,
)

router = APIRouter()


@router.get("/insights")
@limiter.limit("20/minute")
async def insights(
    request: Request,
    db: Session = Depends(get_db),
    clerk_user_id: str = Depends(get_clerk_user_id),
):
    """Unified endpoint for the redesigned Scout page.

    Returns briefing, action items, best deals, price context, book windows,
    and next scan info in a single response.
    """
    user, signals = _get_user_and_signals(db, clerk_user_id)

    if not signals:
        return {
            "briefing": {
                "total_signals": 0,
                "active_deals": 0,
                "price_drops_today": 0,
                "new_deals_today": 0,
                "headline": "Set up a signal to get started",
                "subtext": "Create your first signal and we'll start tracking deals for you.",
                "mood": "neutral",
                "book_now_nudge": None,
            },
            "action_items": [],
            "best_deals": [],
            "price_context": [],
            "book_windows": [],
            "next_scan_at": None,
        }

    signal_ids = [s.id for s in signals]
    signal_map = {s.id: s for s in signals}

    # ── Batch queries ──

    # Active match count per signal
    match_count_rows = (
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(DealMatch.signal_id.in_(signal_ids), Deal.is_active == True)  # noqa: E712
        .group_by(DealMatch.signal_id)
        .all()
    )
    match_counts: dict[UUID, int] = dict(match_count_rows)
    total_active_deals = sum(match_counts.values())

    # Intel caches
    intel_rows = (
        db.query(SignalIntelCache)
        .filter(SignalIntelCache.signal_id.in_(signal_ids))
        .all()
    )
    intel_map = {r.signal_id: r for r in intel_rows}

    # Price drops — deals where latest price < previous price
    drop_rows = db.execute(
        text("""
            SELECT dm.signal_id::text, dm.deal_id::text, dm.id::text,
                   d.destination, d.hotel_name, d.star_rating,
                   d.price_cents, d.origin, d.depart_date, d.return_date,
                   d.deeplink_url, dm.is_favourite,
                   ph.prev_price, ph.price_cents as hist_price,
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
            LIMIT 10
        """),
        {"signal_ids": [str(sid) for sid in signal_ids]},
    ).all()

    drops_count = len(drop_rows)

    # New deals today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    new_deals_today = (
        db.query(func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
            DealMatch.matched_at >= today_start,
        )
        .scalar()
    ) or 0

    # Best deals — top 12 across all signals by price
    best_deal_rows = (
        db.query(DealMatch, Deal)
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
            Deal.price_cents.isnot(None),
        )
        .order_by(Deal.price_cents.asc())
        .limit(12)
        .all()
    )

    # Build price delta map for best deals
    best_deal_ids = [d.id for _, d in best_deal_rows]
    delta_map: dict[UUID, tuple[int, int]] = {}  # deal_id -> (prev_price, current_price)
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

    # Market stats per signal for price context
    market_stats_map: dict[UUID, object] = {}
    for s in signals:
        bucket = build_market_bucket_from_signal(s)
        if bucket:
            stats = compute_market_stats(db, bucket)
            if stats.sample_size >= 10:
                market_stats_map[s.id] = stats

    # Next scan time
    latest_run = (
        db.query(SignalRun)
        .filter(SignalRun.signal_id.in_(signal_ids))
        .order_by(SignalRun.started_at.desc())
        .first()
    )
    next_scan_at = None
    if latest_run and latest_run.completed_at:
        # Scans run ~every 6 hours
        next_scan = latest_run.completed_at + timedelta(hours=6)
        if next_scan > datetime.now(timezone.utc):
            next_scan_at = next_scan.isoformat()

    # ── Build briefing ──

    falling_count = sum(1 for i in intel_rows if i.trend_direction == "falling")
    rising_count = sum(1 for i in intel_rows if i.trend_direction == "rising")

    if drops_count > 0:
        mood = "positive"
        headline = f"Prices dropping — {drops_count} deal{'s' if drops_count != 1 else ''} just got cheaper"
        subtext = "Now could be a good time to book."
    elif falling_count > rising_count:
        mood = "positive"
        headline = f"Prices trending down across {falling_count} signal{'s' if falling_count != 1 else ''}"
        subtext = "The market is moving in your favour."
    elif rising_count > falling_count:
        mood = "caution"
        headline = f"Prices are climbing on {rising_count} signal{'s' if rising_count != 1 else ''}"
        subtext = "Consider booking soon if you see a good deal."
    elif total_active_deals > 0:
        mood = "neutral"
        headline = f"Tracking {total_active_deals} active deal{'s' if total_active_deals != 1 else ''}"
        subtext = "Prices are holding steady. We'll alert you if anything changes."
    else:
        mood = "neutral"
        headline = "No matches yet — we're watching"
        subtext = "We'll notify you as soon as deals match your signals."

    # ── Build action items ──

    action_items = []

    # Price drop actions
    for row in drop_rows[:5]:
        sig_id_str = row[0]
        sig = signal_map.get(UUID(sig_id_str))
        sig_name = sig.name if sig else "Unknown"
        route_label = _build_route_label(sig) if sig else ""
        hotel = row[4] or _region_label(row[3])
        drop_cents = row[14]

        action_items.append({
            "type": "price_drop",
            "signal_id": sig_id_str,
            "signal_name": sig_name,
            "route_label": route_label,
            "headline": f"${drop_cents // 100} price drop on {hotel}",
            "detail": f"Was ${row[12] // 100:,}, now ${row[13] // 100:,}",
            "deal_id": row[1],
            "urgency": "high",
        })

    # Signals near all-time low
    for s in signals:
        intel = intel_map.get(s.id)
        if intel and intel.floor_proximity_pct is not None and intel.floor_proximity_pct <= 5:
            action_items.append({
                "type": "near_floor",
                "signal_id": str(s.id),
                "signal_name": s.name,
                "route_label": _build_route_label(s),
                "headline": f"{s.name} is near all-time low",
                "detail": f"Within {intel.floor_proximity_pct:.0f}% of the lowest price we've seen.",
                "deal_id": None,
                "urgency": "medium",
            })

    # High value signals without favourites
    fav_counts = dict(
        db.query(DealMatch.signal_id, func.count(DealMatch.id))
        .join(Deal, Deal.id == DealMatch.deal_id)
        .filter(
            DealMatch.signal_id.in_(signal_ids),
            Deal.is_active == True,  # noqa: E712
            DealMatch.is_favourite == True,  # noqa: E712
        )
        .group_by(DealMatch.signal_id)
        .all()
    )

    for s in signals:
        intel = intel_map.get(s.id)
        matches = match_counts.get(s.id, 0)
        favs = fav_counts.get(s.id, 0)
        if intel and intel.value_score and intel.value_score >= 70 and favs == 0 and matches > 0:
            action_items.append({
                "type": "review_deals",
                "signal_id": str(s.id),
                "signal_name": s.name,
                "route_label": _build_route_label(s),
                "headline": f"Strong deals on {s.name} — none favourited",
                "detail": f"Value score {intel.value_score}/100 with {matches} active deal{'s' if matches != 1 else ''}.",
                "deal_id": None,
                "urgency": "low",
            })

    # ── Build best deals list ──

    best_deals = []
    for dm, deal in best_deal_rows:
        delta_info = delta_map.get(deal.id)
        if delta_info:
            prev_price, cur_price = delta_info
            if cur_price < prev_price:
                trend = "down"
                delta_cents = prev_price - cur_price
            elif cur_price > prev_price:
                trend = "up"
                delta_cents = cur_price - prev_price
            else:
                trend = "stable"
                delta_cents = None
        else:
            trend = "stable"
            delta_cents = None

        # vs typical from market stats
        vs_typical = None
        sig = signal_map.get(dm.signal_id)
        stats = market_stats_map.get(dm.signal_id)
        if stats and stats.median_price and deal.price_cents:
            diff = stats.median_price - deal.price_cents
            if diff > 0:
                vs_typical = f"${diff // 100:,} below typical"
            elif diff < 0:
                vs_typical = f"${abs(diff) // 100:,} above typical"

        nights = None
        dep_date_str = None
        if deal.depart_date and deal.return_date:
            nights = (deal.return_date - deal.depart_date).days
        if deal.depart_date:
            dep_date_str = deal.depart_date.isoformat()

        best_deals.append({
            "signal_id": str(dm.signal_id),
            "match_id": str(dm.id),
            "destination": _region_label(deal.destination) if deal.destination else "",
            "hotel_name": deal.hotel_name,
            "star_rating": float(deal.star_rating) if deal.star_rating else None,
            "price_cents": deal.price_cents,
            "price_trend": trend,
            "price_delta_cents": delta_cents,
            "vs_typical": vs_typical,
            "nights": nights,
            "departure_date": dep_date_str,
            "departure_airport": deal.origin,
            "deal_url": deal.deeplink_url,
            "is_favourite": dm.is_favourite,
        })

    # Sort by value: deals with vs_typical "below" first, then by price
    def deal_sort_key(d):
        # Parse vs_typical to get numeric value for sorting
        vt = d.get("vs_typical") or ""
        if "below" in vt:
            try:
                return (0, -int(vt.split("$")[1].split(" ")[0].replace(",", "")))
            except (IndexError, ValueError):
                pass
        elif "above" in vt:
            try:
                return (2, int(vt.split("$")[1].split(" ")[0].replace(",", "")))
            except (IndexError, ValueError):
                pass
        return (1, d.get("price_cents", 0))

    best_deals.sort(key=deal_sort_key)

    # ── Build price context ──

    price_context = []
    for s in signals:
        stats = market_stats_map.get(s.id)
        if not stats or stats.sample_size < 10:
            continue

        # Current average from active matched deals
        prices = (
            db.query(Deal.price_cents)
            .join(DealMatch, DealMatch.deal_id == Deal.id)
            .filter(
                DealMatch.signal_id == s.id,
                Deal.is_active == True,  # noqa: E712
                Deal.price_cents.isnot(None),
                Deal.price_cents > 0,
            )
            .all()
        )
        if not prices:
            continue

        current_prices = [p[0] for p in prices]
        current_avg = sum(current_prices) // len(current_prices)

        # Percentile of current avg within market distribution
        all_prices = sorted(stats.prices) if hasattr(stats, 'prices') and stats.prices else []
        if all_prices:
            below = sum(1 for p in all_prices if p < current_avg)
            percentile = below / len(all_prices)
        else:
            percentile = 0.5

        # Market label
        if percentile <= 0.25:
            market_label = "low"
        elif percentile <= 0.5:
            market_label = "average"
        elif percentile <= 0.75:
            market_label = "high"
        else:
            market_label = "very_high"

        # Time range
        time_range_days = 7  # freshness window from market_intel

        price_context.append({
            "signal_id": str(s.id),
            "signal_name": s.name,
            "route_label": _build_route_label(s),
            "current_avg_cents": current_avg,
            "range_low_cents": stats.min_price or current_avg,
            "range_high_cents": stats.max_price or current_avg,
            "percentile": round(percentile, 2),
            "great_deal_cents": stats.p25_price,
            "typical_cents": stats.median_price,
            "pricey_cents": stats.p75_price,
            "cheapest_ever_cents": s.all_time_low_price,
            "market_label": market_label,
            "data_points": stats.sample_size,
            "time_range_days": time_range_days,
        })

    # ── Build book windows ──

    book_windows = []
    for s in signals:
        if match_counts.get(s.id, 0) == 0:
            continue
        try:
            bw = get_book_window(s.id, s.name, _build_route_label(s), db)
            book_windows.append(bw.model_dump())
        except Exception:
            logger.exception("Book window computation failed for signal %s", s.id)

    # Check if any book window recommends "book_now" for briefing nudge
    book_now_nudge = None
    for bw in book_windows:
        if bw.get("result") and bw["result"].get("recommendation") == "book_now":
            route = bw.get("route_label", "")
            book_now_nudge = f"We think it's time to book your {route.split('→')[-1].strip()} trip"
            break

    return {
        "briefing": {
            "total_signals": len(signals),
            "active_deals": total_active_deals,
            "price_drops_today": drops_count,
            "new_deals_today": new_deals_today,
            "headline": headline,
            "subtext": subtext,
            "mood": mood,
            "book_now_nudge": book_now_nudge,
        },
        "action_items": action_items,
        "best_deals": best_deals,
        "price_context": price_context,
        "book_windows": book_windows,
        "next_scan_at": next_scan_at,
    }
