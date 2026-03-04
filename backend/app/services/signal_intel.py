"""Signal intelligence cache — computes Module 1/2/3 values after each scrape cycle."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.deal import Deal
from app.db.models.deal_match import DealMatch
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
        # Get min price ever for this signal (joining to deals for price_cents)
        min_price = db.execute(
            select(func.min(Deal.price_cents))
            .join(DealMatch, DealMatch.deal_id == Deal.id)
            .where(DealMatch.signal_id == signal_id)
        ).scalar()
        values["min_price_ever_cents"] = min_price

        # Percentile rank of the current best deal vs all historical matches
        # "Current best" = lowest price among active deals matched to this signal
        current_best = db.execute(
            select(func.min(Deal.price_cents))
            .join(DealMatch, DealMatch.deal_id == Deal.id)
            .where(DealMatch.signal_id == signal_id, Deal.is_active == True)  # noqa: E712
        ).scalar()

        if current_best is not None and total > 1:
            # What fraction of historical matches had a lower price?
            cheaper_count = db.execute(
                select(func.count())
                .select_from(DealMatch)
                .join(Deal, Deal.id == DealMatch.deal_id)
                .where(DealMatch.signal_id == signal_id, Deal.price_cents < current_best)
            ).scalar() or 0
            # Percentile: 0.0 = cheapest ever, 1.0 = most expensive ever
            values["current_deal_percentile"] = round(cheaper_count / total, 3)
        else:
            values["current_deal_percentile"] = 0.0

        # ── Module 2: Trend Direction — Price Momentum ──
        # Average price per week for the last 6 weeks
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
            # Compare consecutive weeks (newest first)
            directions = []
            for i in range(len(weekly_avgs) - 1):
                newer_price = weekly_avgs[i][1]
                older_price = weekly_avgs[i + 1][1]
                if newer_price < older_price:
                    directions.append("down")
                elif newer_price > older_price:
                    directions.append("up")
                else:
                    directions.append("stable")

            # Trend = direction of most recent comparison
            current_direction = directions[0] if directions else "stable"

            # Count consecutive weeks in same direction
            consecutive = 1
            for d in directions[1:]:
                if d == current_direction:
                    consecutive += 1
                else:
                    break

            values["trend_direction"] = current_direction
            values["trend_consecutive_weeks"] = consecutive
        else:
            values["trend_direction"] = "stable"
            values["trend_consecutive_weeks"] = 0

        # ── Module 3: Night Length Sweet Spot — Per-Night Value ──
        # Requires at least 10 matches per duration bucket to be statistically meaningful
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
            best = duration_stats[0]   # lowest avg_per_night
            second = duration_stats[1]  # next lowest
            values["best_value_nights"] = best[0]  # nights
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
