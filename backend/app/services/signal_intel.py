"""Signal intelligence cache — computes Module 1-7 values after each scrape cycle.

Modules:
  1. Price History — All-Time Low & Percentile Rank
  2. Trend Direction — Price Momentum (+ 2b Velocity, 2c Inflection)
  3. Night Length Sweet Spot — Per-Night Value
  4. Star-Price Anomaly Detection
  5. Price Floor Proximity
  7. Price-to-Quality Value Score (0-100)

Route-level intelligence (departure heatmap, destination index, booking countdown)
is computed separately via refresh_route_intel_cache().
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
from app.db.models.route_intel_cache import RouteIntelCache
from app.db.models.signal import Signal
from app.db.models.signal_intel_cache import SignalIntelCache

logger = logging.getLogger(__name__)


def refresh_intel_cache(db: Session, signal_id) -> dict | None:
    """Recompute and upsert intelligence cache for a single signal.

    Returns the computed values dict, or None on error.
    """
    try:
        values: dict = {"signal_id": signal_id, "cache_refreshed_at": datetime.now(timezone.utc)}

        # ── Total matches ──
        total = db.execute(
            select(func.count()).select_from(DealMatch).where(DealMatch.signal_id == signal_id)
        ).scalar() or 0
        values["total_matches"] = total

        if total == 0:
            # No matches yet — write empty cache row
            _upsert(db, values)
            return values

        # ── Module 1: Price History — All-Time Low & Percentile Rank ──
        min_price = db.execute(
            select(func.min(Deal.price_cents))
            .join(DealMatch, DealMatch.deal_id == Deal.id)
            .where(DealMatch.signal_id == signal_id)
        ).scalar()
        values["min_price_ever_cents"] = min_price

        current_best = db.execute(
            select(func.min(Deal.price_cents))
            .join(DealMatch, DealMatch.deal_id == Deal.id)
            .where(DealMatch.signal_id == signal_id, Deal.is_active == True)  # noqa: E712
        ).scalar()

        if current_best is not None and total > 1:
            cheaper_count = db.execute(
                select(func.count())
                .select_from(DealMatch)
                .join(Deal, Deal.id == DealMatch.deal_id)
                .where(DealMatch.signal_id == signal_id, Deal.price_cents < current_best)
            ).scalar() or 0
            values["current_deal_percentile"] = round(cheaper_count / total, 3)
        else:
            values["current_deal_percentile"] = 0.0

        # ── Module 2: Trend Direction — Price Momentum ──
        weekly_avgs = db.execute(
            sa_text("""
                SELECT DATE_TRUNC('week', dm.matched_at) AS week,
                       AVG(d.price_cents)::int AS avg_price
                FROM deal_matches dm
                JOIN deals d ON d.id = dm.deal_id
                WHERE dm.signal_id = :sid
                GROUP BY week
                ORDER BY week DESC
                LIMIT 6
            """),
            {"sid": str(signal_id)},
        ).fetchall()

        if len(weekly_avgs) >= 2:
            directions = []
            week_deltas = []  # price change per week (newer - older, negative = dropping)
            for i in range(len(weekly_avgs) - 1):
                newer_price = weekly_avgs[i][1]
                older_price = weekly_avgs[i + 1][1]
                week_deltas.append(newer_price - older_price)
                # Only count as directional if delta exceeds 2% of the older price
                threshold = older_price * 0.02 if older_price else 0
                if newer_price < older_price - threshold:
                    directions.append("down")
                elif newer_price > older_price + threshold:
                    directions.append("up")
                else:
                    directions.append("stable")

            # Require majority of directions to agree; default to stable
            down_count = sum(1 for d in directions if d == "down")
            up_count = sum(1 for d in directions if d == "up")
            total_dirs = len(directions)

            if down_count > total_dirs / 2:
                current_direction = "down"
            elif up_count > total_dirs / 2:
                current_direction = "up"
            else:
                current_direction = "stable"

            consecutive = 1
            for d in directions[1:]:
                if d == current_direction:
                    consecutive += 1
                else:
                    break

            values["trend_direction"] = current_direction
            values["trend_consecutive_weeks"] = consecutive

            # ── Module 2b: Velocity — is the change accelerating or decelerating? ──
            values["trend_last_week_delta_cents"] = week_deltas[0] if week_deltas else None
            values["trend_prev_week_delta_cents"] = week_deltas[1] if len(week_deltas) >= 2 else None

            if len(week_deltas) >= 2:
                last_delta = abs(week_deltas[0])
                prev_delta = abs(week_deltas[1])
                # Both moving in the same direction?
                same_direction = (
                    (week_deltas[0] < 0 and week_deltas[1] < 0)
                    or (week_deltas[0] > 0 and week_deltas[1] > 0)
                )
                if same_direction:
                    if last_delta > prev_delta * 1.15:  # 15% threshold
                        values["trend_velocity"] = "accelerating"
                    elif last_delta < prev_delta * 0.85:
                        values["trend_velocity"] = "decelerating"
                    else:
                        values["trend_velocity"] = "steady"
                else:
                    values["trend_velocity"] = "steady"
            else:
                values["trend_velocity"] = None

            # ── Module 2c: Inflection Detection ──
            # Load previous cache to detect direction change
            prev_cache = db.execute(
                select(SignalIntelCache.trend_direction)
                .where(SignalIntelCache.signal_id == signal_id)
            ).scalar_one_or_none()

            if (
                prev_cache is not None
                and prev_cache == "down"
                and current_direction == "up"
                and len(week_deltas) >= 1
                and weekly_avgs[1][1] > 0  # older week price > 0
            ):
                pct_change = round(week_deltas[0] / weekly_avgs[1][1] * 100, 1)
                if pct_change > 3:  # Only flag meaningful inflections (>3%)
                    values["trend_inflection"] = True
                    values["inflection_pct_change"] = pct_change
                else:
                    values["trend_inflection"] = False
                    values["inflection_pct_change"] = None
            else:
                values["trend_inflection"] = False
                values["inflection_pct_change"] = None
        else:
            values["trend_direction"] = "stable"
            values["trend_consecutive_weeks"] = 0
            values["trend_velocity"] = None
            values["trend_last_week_delta_cents"] = None
            values["trend_prev_week_delta_cents"] = None
            values["trend_inflection"] = False
            values["inflection_pct_change"] = None

        # ── Module 3: Night Length Sweet Spot — Per-Night Value ──
        duration_stats = db.execute(
            sa_text("""
                SELECT
                    (d.return_date - d.depart_date) AS nights,
                    AVG(d.price_cents / NULLIF((d.return_date - d.depart_date), 0))::int AS avg_per_night,
                    COUNT(*) AS sample_size
                FROM deal_matches dm
                JOIN deals d ON d.id = dm.deal_id
                WHERE dm.signal_id = :sid
                  AND d.return_date IS NOT NULL
                  AND d.return_date > d.depart_date
                GROUP BY nights
                HAVING COUNT(*) >= 10
                ORDER BY avg_per_night ASC
            """),
            {"sid": str(signal_id)},
        ).fetchall()

        if len(duration_stats) >= 2:
            best = duration_stats[0]
            second = duration_stats[1]
            values["best_value_nights"] = best[0]
            if second[1] and second[1] > 0:
                pct_saving = round((1 - best[1] / second[1]) * 100, 1)
                values["best_value_pct_saving"] = pct_saving
            else:
                values["best_value_pct_saving"] = None
        elif len(duration_stats) == 1:
            values["best_value_nights"] = duration_stats[0][0]
            values["best_value_pct_saving"] = None
        else:
            values["best_value_nights"] = None
            values["best_value_pct_saving"] = None

        # ── Module 4: Star-Price Anomaly Detection ──
        # Find the hero deal (cheapest active deal for this signal)
        hero = db.execute(
            sa_text("""
                SELECT d.price_cents, d.star_rating, d.origin, d.destination
                FROM deal_matches dm
                JOIN deals d ON d.id = dm.deal_id
                WHERE dm.signal_id = :sid AND d.is_active = true
                  AND d.star_rating IS NOT NULL
                ORDER BY d.price_cents ASC
                LIMIT 1
            """),
            {"sid": str(signal_id)},
        ).fetchone()

        if hero and hero[1] is not None and hero[1] > 0:
            hero_price = hero[0]
            hero_stars = hero[1]
            hero_origin = hero[2]
            hero_dest = hero[3]
            values["hero_star_rating"] = hero_stars

            # Count lower-star active deals on the same route that cost MORE
            anomaly_result = db.execute(
                sa_text("""
                    SELECT
                        COUNT(*) FILTER (WHERE d.price_cents > :hero_price) AS more_expensive,
                        COUNT(*) AS total_lower_star
                    FROM deals d
                    WHERE d.is_active = true
                      AND d.origin = :origin
                      AND d.destination = :dest
                      AND d.star_rating IS NOT NULL
                      AND d.star_rating < :hero_stars
                """),
                {
                    "hero_price": hero_price,
                    "origin": hero_origin,
                    "dest": hero_dest,
                    "hero_stars": hero_stars,
                },
            ).fetchone()

            if anomaly_result and anomaly_result[1] > 0:
                values["star_price_anomaly_pct"] = round(
                    anomaly_result[0] / anomaly_result[1], 2
                )
            else:
                values["star_price_anomaly_pct"] = None
        else:
            values["star_price_anomaly_pct"] = None
            values["hero_star_rating"] = None

        # ── Module 5: Price Floor Proximity ──
        if current_best is not None and min_price is not None and min_price > 0:
            values["floor_proximity_pct"] = round(
                (current_best - min_price) / min_price * 100, 1
            )
        else:
            values["floor_proximity_pct"] = None

        # ── Module 7: Price-to-Quality Value Score ──
        # Composite: price_cents / (star_rating * duration_days) — lower is better
        # Percentile rank among all deals for the same route
        if hero and hero[1] is not None and hero[1] > 0:
            value_score_result = db.execute(
                sa_text("""
                    WITH deal_values AS (
                        SELECT d.price_cents::float / (d.star_rating * NULLIF((d.return_date - d.depart_date), 0)) AS value_metric
                        FROM deals d
                        WHERE d.origin = :origin
                          AND d.destination = :dest
                          AND d.star_rating IS NOT NULL AND d.star_rating > 0
                          AND d.return_date IS NOT NULL AND d.return_date > d.depart_date
                    ),
                    hero_value AS (
                        SELECT :hero_price::float / (:hero_stars * NULLIF(:hero_duration, 0)) AS metric
                    )
                    SELECT
                        (SELECT COUNT(*) FROM deal_values WHERE value_metric > (SELECT metric FROM hero_value)) AS better_count,
                        (SELECT COUNT(*) FROM deal_values) AS total_count
                """),
                {
                    "origin": hero_origin,
                    "dest": hero_dest,
                    "hero_price": hero_price,
                    "hero_stars": hero_stars,
                    "hero_duration": 7,  # default duration for scoring
                },
            ).fetchone()

            if value_score_result and value_score_result[1] > 0:
                # Score = % of deals with worse value metric
                score = round(value_score_result[0] / value_score_result[1] * 100)
                values["value_score"] = min(100, max(0, score))
            else:
                values["value_score"] = None
        else:
            values["value_score"] = None

        _upsert(db, values)
        return values

    except Exception:
        logger.exception("Failed to refresh intel cache for signal %s", signal_id)
        db.rollback()
        return None


def _upsert(db: Session, values: dict) -> None:
    """Insert or update a signal_intel_cache row."""
    stmt = pg_insert(SignalIntelCache).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_id"],
        set_={k: v for k, v in values.items() if k != "signal_id"},
    )
    db.execute(stmt)
    db.commit()


def refresh_all_active_signal_caches(db: Session) -> int:
    """Refresh intel cache for all active signals. Returns count refreshed."""
    signal_ids = db.execute(
        select(Signal.id).where(Signal.status == "active")
    ).scalars().all()

    refreshed = 0
    for sid in signal_ids:
        result = refresh_intel_cache(db, sid)
        if result is not None:
            refreshed += 1

    logger.info("Refreshed intel cache for %d / %d active signals", refreshed, len(signal_ids))
    return refreshed


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE-LEVEL INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════


def refresh_route_intel_cache(db: Session) -> int:
    """Recompute route-level intelligence for all active routes.

    A route is any (origin, destination) pair with active deals.
    Computes: departure window heatmap, destination price index, booking countdown.

    Returns count of routes refreshed.
    """
    try:
        # Find all distinct active routes
        routes = db.execute(
            sa_text("""
                SELECT DISTINCT origin, destination
                FROM deals
                WHERE is_active = true
                  AND depart_date >= CURRENT_DATE
            """)
        ).fetchall()

        refreshed = 0
        now = datetime.now(timezone.utc)

        for origin, destination in routes:
            try:
                values: dict = {
                    "origin": origin,
                    "destination_region": destination,
                    "cache_refreshed_at": now,
                }

                # ── Departure Window Heatmap ──
                # Average price by departure week (future dates only)
                week_prices = db.execute(
                    sa_text("""
                        SELECT DATE_TRUNC('week', depart_date)::date AS week,
                               AVG(price_cents)::int AS avg_price,
                               COUNT(*) AS deal_count
                        FROM deals
                        WHERE origin = :origin AND destination = :dest
                          AND depart_date >= CURRENT_DATE
                        GROUP BY week
                        HAVING COUNT(*) >= 3
                        ORDER BY avg_price ASC
                    """),
                    {"origin": origin, "dest": destination},
                ).fetchall()

                if week_prices:
                    cheapest = week_prices[0]
                    priciest = week_prices[-1]
                    values["cheapest_depart_week"] = cheapest[0]
                    values["cheapest_week_avg_cents"] = cheapest[1]
                    values["priciest_depart_week"] = priciest[0]
                    values["priciest_week_avg_cents"] = priciest[1]
                    values["total_deals_analyzed"] = sum(w[2] for w in week_prices)

                # ── Destination Price Index ──
                # Current week avg vs previous week avg
                index_result = db.execute(
                    sa_text("""
                        SELECT
                            AVG(price_cents) FILTER (
                                WHERE depart_date >= DATE_TRUNC('week', CURRENT_DATE)
                            )::int AS current_week_avg,
                            AVG(price_cents) FILTER (
                                WHERE depart_date >= DATE_TRUNC('week', CURRENT_DATE) - INTERVAL '7 days'
                                  AND depart_date < DATE_TRUNC('week', CURRENT_DATE)
                            )::int AS prev_week_avg
                        FROM deals
                        WHERE origin = :origin AND destination = :dest
                          AND is_active = true
                    """),
                    {"origin": origin, "dest": destination},
                ).fetchone()

                if index_result:
                    values["current_week_avg_cents"] = index_result[0]
                    values["prev_week_avg_cents"] = index_result[1]
                    if index_result[0] and index_result[1] and index_result[1] > 0:
                        values["week_over_week_pct"] = round(
                            (index_result[0] - index_result[1]) / index_result[1] * 100, 1
                        )

                # ── Booking Countdown Pressure ──
                # Average price by days-until-departure buckets
                countdown_result = db.execute(
                    sa_text("""
                        SELECT
                            AVG(price_cents) FILTER (
                                WHERE (depart_date - found_at::date) >= 28
                            )::int AS avg_4plus_weeks,
                            AVG(price_cents) FILTER (
                                WHERE (depart_date - found_at::date) >= 14
                                  AND (depart_date - found_at::date) < 28
                            )::int AS avg_2to4_weeks,
                            AVG(price_cents) FILTER (
                                WHERE (depart_date - found_at::date) < 14
                            )::int AS avg_under_2_weeks
                        FROM deals
                        WHERE origin = :origin AND destination = :dest
                          AND found_at IS NOT NULL
                    """),
                    {"origin": origin, "dest": destination},
                ).fetchone()

                if countdown_result:
                    values["avg_price_4plus_weeks_cents"] = countdown_result[0]
                    values["avg_price_2to4_weeks_cents"] = countdown_result[1]
                    values["avg_price_under_2_weeks_cents"] = countdown_result[2]

                    if countdown_result[0] and countdown_result[2] and countdown_result[0] > 0:
                        values["late_booking_premium_pct"] = round(
                            (countdown_result[2] - countdown_result[0]) / countdown_result[0] * 100, 1
                        )

                # Upsert route intel
                stmt = pg_insert(RouteIntelCache).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["origin", "destination_region"],
                    set_={k: v for k, v in values.items() if k not in ("origin", "destination_region")},
                )
                db.execute(stmt)
                refreshed += 1

            except Exception:
                logger.exception("Failed to refresh route intel for %s → %s", origin, destination)
                continue

        db.commit()
        logger.info("Refreshed route intel cache for %d / %d routes", refreshed, len(routes))
        return refreshed

    except Exception:
        logger.exception("Failed to refresh route intel cache")
        db.rollback()
        return 0


def get_airport_arbitrage(
    db: Session,
    hotel_id: str | None,
    depart_date,
    current_origin: str,
    current_price_cents: int,
) -> dict | None:
    """Find a cheaper price for the same hotel from a different airport.

    Returns dict with arbitrage_airport, arbitrage_price_cents, arbitrage_savings_cents
    or None if no cheaper alternative exists.
    """
    if not hotel_id or not depart_date:
        return None

    # Nearby airport groups — suggest airports within driving distance
    NEARBY_AIRPORTS: dict[str, list[str]] = {
        "YYZ": ["YHM", "YKF"],
        "YHM": ["YYZ", "YKF"],
        "YKF": ["YYZ", "YHM"],
        "YVR": ["YXX", "YYJ"],
        "YXX": ["YVR"],
        "YYJ": ["YVR"],
        "YOW": ["YUL"],
        "YUL": ["YOW"],
        "YYC": ["YEG"],
        "YEG": ["YYC"],
        "YWG": [],
        "YQR": ["YXE"],
        "YXE": ["YQR"],
    }

    nearby = NEARBY_AIRPORTS.get(current_origin, [])
    if not nearby:
        return None

    result = db.execute(
        select(Deal.origin, func.min(Deal.price_cents).label("min_price"))
        .where(
            Deal.hotel_id == hotel_id,
            Deal.depart_date == depart_date,
            Deal.is_active == True,  # noqa: E712
            Deal.origin != current_origin,
            Deal.origin.in_(nearby),
        )
        .group_by(Deal.origin)
        .order_by(func.min(Deal.price_cents).asc())
        .limit(1)
    ).fetchone()

    if not result:
        return None

    alt_origin, alt_price = result[0], result[1]
    savings = current_price_cents - alt_price

    if savings < 10000:  # Only surface if savings > $100/pp
        return None

    return {
        "arbitrage_airport": alt_origin,
        "arbitrage_price_cents": alt_price,
        "arbitrage_savings_cents": savings,
    }


def get_departure_heatmap(
    db: Session,
    origin: str,
    destination: str,
    limit: int = 8,
) -> list[dict] | None:
    """Get average price by departure week for a route.

    Returns a list of dicts sorted chronologically:
    [{week: "2026-03-09", avg_cents: 118000, deal_count: 12, is_cheapest: bool, is_priciest: bool}, ...]
    or None if insufficient data.
    """
    rows = db.execute(
        sa_text("""
            SELECT DATE_TRUNC('week', depart_date)::date AS week,
                   AVG(price_cents)::int AS avg_price,
                   COUNT(*) AS deal_count
            FROM deals
            WHERE origin = :origin AND destination = :dest
              AND depart_date >= CURRENT_DATE
              AND is_active = true
            GROUP BY week
            HAVING COUNT(*) >= 3
            ORDER BY week ASC
            LIMIT :lim
        """),
        {"origin": origin, "dest": destination, "lim": limit},
    ).fetchall()

    if len(rows) < 3:
        return None

    # Find min/max for labeling
    prices = [r[1] for r in rows]
    min_price = min(prices)
    max_price = max(prices)

    return [
        {
            "week": str(r[0]),
            "avg_cents": r[1],
            "deal_count": r[2],
            "is_cheapest": r[1] == min_price,
            "is_priciest": r[1] == max_price,
        }
        for r in rows
    ]


def get_destination_index(db: Session, origin: str, limit: int = 5) -> list[dict]:
    """Get the destination price index leaderboard for an airport.

    Returns a list of dicts sorted by cheapest avg price:
    [{destination_region, current_week_avg_cents, week_over_week_pct}, ...]
    """
    rows = db.execute(
        select(
            RouteIntelCache.destination_region,
            RouteIntelCache.current_week_avg_cents,
            RouteIntelCache.week_over_week_pct,
        )
        .where(
            RouteIntelCache.origin == origin,
            RouteIntelCache.current_week_avg_cents.isnot(None),
        )
        .order_by(RouteIntelCache.current_week_avg_cents.asc())
        .limit(limit)
    ).all()

    return [
        {
            "destination_region": r[0],
            "current_week_avg_cents": r[1],
            "week_over_week_pct": r[2],
        }
        for r in rows
    ]
