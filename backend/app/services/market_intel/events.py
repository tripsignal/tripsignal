"""Market events — today's signals and market movers."""
import threading
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.formatting import dest_label
from app.services.market_intel.types import freshness_cutoff

# In-memory cache for market events (refreshed every 10 minutes)
_events_cache: dict | None = None
_events_cache_time: datetime | None = None
_events_cache_lock = threading.Lock()
_EVENTS_CACHE_TTL = timedelta(minutes=10)


def compute_market_events(db: Session) -> dict:
    """Compute today's signals and market movers from real scrape data.

    Today's Signals: notable price drops, resort anomalies, inventory shifts.
    Market Movers: strongest destination-level price/inventory changes.

    Returns dict with 'todays_signals' and 'market_movers' lists (max 5 each).
    Empty lists when data is insufficient.

    Results are cached in-memory for 10 minutes to avoid running 5 heavy
    analytical queries on every request.
    """
    global _events_cache, _events_cache_time

    now = datetime.now(timezone.utc)
    with _events_cache_lock:
        if _events_cache is not None and _events_cache_time and (now - _events_cache_time) < _EVENTS_CACHE_TTL:
            return _events_cache

    result = _compute_market_events_uncached(db)

    with _events_cache_lock:
        _events_cache = result
        _events_cache_time = now

    return result


def _compute_market_events_uncached(db: Session) -> dict:
    """Internal: runs the actual 5-query market events computation."""
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_48h = datetime.now(timezone.utc) - timedelta(hours=48)
    freshness = freshness_cutoff()
    today_date = date.today()

    todays_signals: list[dict] = []
    market_movers: list[dict] = []

    # ── 1. Price drops by destination (last 24h) ──
    drop_rows = db.execute(text("""
        SELECT
            d.destination,
            COUNT(DISTINCT sub.deal_id) AS drop_count,
            AVG(sub.drop_pct) AS avg_drop_pct
        FROM (
            SELECT
                deal_id,
                price_cents,
                LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                CASE WHEN LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) > 0
                     THEN (LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) - price_cents)::float
                          / LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) * 100
                     ELSE 0 END AS drop_pct,
                ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
            FROM deal_price_history
            WHERE recorded_at >= :cutoff_24h
        ) sub
        JOIN deals d ON d.id = sub.deal_id
        WHERE sub.rn = 1
          AND sub.prev_price IS NOT NULL
          AND sub.price_cents < sub.prev_price
          AND sub.drop_pct >= 2.0
          AND d.is_active = true
          AND d.depart_date >= :today
        GROUP BY d.destination
        HAVING COUNT(DISTINCT sub.deal_id) >= 3
        ORDER BY AVG(sub.drop_pct) DESC
        LIMIT 5
    """), {"cutoff_24h": cutoff_24h, "today": today_date}).all()

    for dest, drop_count, avg_pct in drop_rows:
        pct = round(avg_pct)
        if pct >= 3:
            todays_signals.append({
                "text": f"{dest_label(dest)} prices dropped {pct}% overnight",
                "type": "price_drop",
                "destination": dest,
                "magnitude": pct,
            })

    # ── 2. Resort anomalies (unusually cheap resorts) ──
    anomaly_rows = db.execute(text("""
        WITH hotel_stats AS (
            SELECT
                hotel_name,
                destination,
                MIN(price_cents) AS current_min,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY price_cents) AS median_price
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :freshness
              AND depart_date >= :today
              AND hotel_name IS NOT NULL
            GROUP BY hotel_name, destination
            HAVING COUNT(*) >= 5
        )
        SELECT hotel_name, destination, current_min, median_price,
               ROUND((1.0 - current_min::float / median_price) * 100) AS discount_pct
        FROM hotel_stats
        WHERE median_price > 0
          AND current_min < median_price * 0.85
        ORDER BY discount_pct DESC
        LIMIT 3
    """), {"freshness": freshness, "today": today_date}).all()

    for hotel, dest, _current, _median, discount in anomaly_rows:
        short_name = hotel if len(hotel) <= 30 else hotel[:27] + "..."
        todays_signals.append({
            "text": f"{short_name} unusually cheap this week",
            "type": "resort_anomaly",
            "destination": dest,
            "magnitude": int(discount),
        })

    # ── 3. Inventory growth by destination ──
    inventory_rows = db.execute(text("""
        WITH recent AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND found_at >= :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
        ),
        previous AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND found_at >= :cutoff_48h
              AND found_at < :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
        )
        SELECT r.destination, r.cnt AS new_count, COALESCE(p.cnt, 0) AS prev_count
        FROM recent r
        LEFT JOIN previous p ON p.destination = r.destination
        WHERE r.cnt >= 5
          AND r.cnt > COALESCE(p.cnt, 0) * 1.3
        ORDER BY r.cnt - COALESCE(p.cnt, 0) DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "cutoff_48h": cutoff_48h, "today": today_date}).all()

    for dest, new_count, prev_count in inventory_rows:
        if prev_count > 0:
            pct_increase = round((new_count - prev_count) / prev_count * 100)
            if pct_increase >= 10:
                todays_signals.append({
                    "text": f"{dest_label(dest)} deals increasing",
                    "type": "inventory_growth",
                    "destination": dest,
                    "magnitude": pct_increase,
                })

    # Cap today's signals at 5
    todays_signals = todays_signals[:5]

    # ── Market Movers: destination-level strongest shifts ──
    price_mover_rows = db.execute(text("""
        SELECT
            d.destination,
            AVG(sub.change_pct) AS avg_change_pct,
            COUNT(DISTINCT sub.deal_id) AS deal_count,
            CASE WHEN AVG(sub.change_pct) > 0 THEN 'up' ELSE 'down' END AS direction
        FROM (
            SELECT
                deal_id,
                price_cents,
                LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price,
                CASE WHEN LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) > 0
                     THEN (price_cents::float - LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at))
                          / LAG(price_cents) OVER (PARTITION BY deal_id ORDER BY recorded_at) * 100
                     ELSE 0 END AS change_pct,
                ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY recorded_at DESC) AS rn
            FROM deal_price_history
            WHERE recorded_at >= :cutoff_24h
        ) sub
        JOIN deals d ON d.id = sub.deal_id
        WHERE sub.rn = 1
          AND sub.prev_price IS NOT NULL
          AND ABS(sub.change_pct) >= 2.0
          AND d.is_active = true
          AND d.depart_date >= :today
        GROUP BY d.destination
        HAVING COUNT(DISTINCT sub.deal_id) >= 3
           AND ABS(AVG(sub.change_pct)) >= 3.0
        ORDER BY ABS(AVG(sub.change_pct)) DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "today": today_date}).all()

    for dest, avg_pct, _count, direction in price_mover_rows:
        pct = abs(round(avg_pct))
        arrow = "↓" if direction == "down" else "↑"
        market_movers.append({
            "text": f"{dest_label(dest)} prices {arrow} {pct}%",
            "type": "price",
            "destination": dest,
            "direction": direction,
            "magnitude": pct,
        })

    # Inventory movers
    inv_mover_rows = db.execute(text("""
        WITH current_inv AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
            HAVING COUNT(*) >= 10
        ),
        prev_inv AS (
            SELECT destination, COUNT(*) AS cnt
            FROM deals
            WHERE is_active = true
              AND last_seen_at >= :cutoff_48h
              AND last_seen_at < :cutoff_24h
              AND depart_date >= :today
            GROUP BY destination
            HAVING COUNT(*) >= 5
        )
        SELECT c.destination,
               c.cnt AS current_count,
               p.cnt AS prev_count,
               ROUND((c.cnt::float - p.cnt) / p.cnt * 100) AS change_pct
        FROM current_inv c
        JOIN prev_inv p ON p.destination = c.destination
        WHERE p.cnt > 0
          AND ABS(c.cnt::float - p.cnt) / p.cnt * 100 >= 10
        ORDER BY ABS(c.cnt::float - p.cnt) / p.cnt DESC
        LIMIT 3
    """), {"cutoff_24h": cutoff_24h, "cutoff_48h": cutoff_48h, "today": today_date}).all()

    for dest, current, _prev, change_pct in inv_mover_rows:
        pct = abs(int(change_pct))
        arrow = "↑" if current > _prev else "↓"
        market_movers.append({
            "text": f"{dest_label(dest)} inventory {arrow} {pct}%",
            "type": "inventory",
            "destination": dest,
            "direction": "up" if current > _prev else "down",
            "magnitude": pct,
        })

    # Sort movers by magnitude descending, cap at 5
    market_movers.sort(key=lambda x: x["magnitude"], reverse=True)
    market_movers = market_movers[:5]

    return {
        "todays_signals": todays_signals,
        "market_movers": market_movers,
    }
