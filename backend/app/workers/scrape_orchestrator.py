"""Scrape Orchestrator — runs all enabled scrapers in staggered sequence.

Replaces independent scraper scheduling. Both scrapers run within the same
time window but sequentially (SellOff first, then RedTag), ensuring:
  - No concurrent load on external sites
  - Staggered execution within a predictable daily cadence
  - Single process to monitor and manage

Usage:
  python -m app.workers.scrape_orchestrator --once     # one cycle, then exit
  python -m app.workers.scrape_orchestrator             # continuous daemon
"""
import logging
import os
import random
import signal as _signal
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("scrape_orchestrator")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

_SYSTEM_API_HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}

# Schedule: daily window in Eastern Time, varies by day of week
# Weekdays: wider 5:00–10:00 AM window  |  Weekends: 7:00 AM–1:00 PM
_ET = ZoneInfo("America/Toronto")
_WEEKDAY_WINDOW = (5, 0, 10, 0)   # Mon-Fri: 5:00 AM – 10:00 AM ET
_WEEKEND_WINDOW = (7, 0, 13, 0)   # Sat-Sun: 7:00 AM – 1:00 PM ET

# Hard timeouts (seconds) — prevents hung scrapers from blocking the entire pipeline
SELLOFF_HARD_TIMEOUT = int(os.getenv("SELLOFF_HARD_TIMEOUT", "21600"))  # 6 hours
REDTAG_HARD_TIMEOUT = int(os.getenv("REDTAG_HARD_TIMEOUT", "3600"))    # 1 hour

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown signal received, will finish after current scraper")


_signal.signal(_signal.SIGTERM, _handle_signal)
_signal.signal(_signal.SIGINT, _handle_signal)


def _window_for_date(dt: datetime) -> tuple[int, int, int, int]:
    """Return (start_h, start_m, end_h, end_m) for the given date's day of week."""
    if dt.weekday() < 5:  # Mon=0 .. Fri=4
        return _WEEKDAY_WINDOW
    return _WEEKEND_WINDOW


def _in_scrape_window() -> bool:
    now_et = datetime.now(_ET)
    sh, sm, eh, em = _window_for_date(now_et)
    ws = now_et.replace(hour=sh, minute=sm, second=0, microsecond=0)
    we = now_et.replace(hour=eh, minute=em, second=0, microsecond=0)
    return ws <= now_et < we


def _next_scrape_time() -> datetime:
    now_et = datetime.now(_ET)
    for day_offset in range(3):
        base = now_et + timedelta(days=day_offset)
        sh, sm, eh, em = _window_for_date(base)
        window_start = base.replace(hour=sh, minute=sm, second=0, microsecond=0)
        window_end = base.replace(hour=eh, minute=em, second=0, microsecond=0)
        if window_start > now_et:
            offset = random.randint(0, int((window_end - window_start).total_seconds()))
            return (window_start + timedelta(seconds=offset)).astimezone(timezone.utc)
    return datetime.now(timezone.utc) + timedelta(hours=6)


def _is_scraper_enabled(key: str) -> bool:
    """Check system_config for a scraper feature flag."""
    try:
        from sqlalchemy import text
        from app.db.session import get_db
        with next(get_db()) as db:
            row = db.execute(
                text("SELECT value FROM system_config WHERE key = :k"),
                {"k": key},
            ).scalar_one_or_none()
            if row is None:
                return True  # No flag = enabled by default
            return row.lower() == "true"
    except Exception:
        return True  # If table doesn't exist, proceed


def _run_with_timeout(target, name: str, timeout_seconds: int) -> dict:
    """Run a callable in a daemon thread with a hard timeout.

    Returns {"status": "completed"} on success, {"status": "timeout"} if the
    thread didn't finish in time, or {"status": "failed", "error": str} on exception.
    """
    result = {"status": "completed", "error": None}

    def _wrapper():
        try:
            target()
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error("%s failed: %s\n%s", name, e, traceback.format_exc())

    thread = threading.Thread(target=_wrapper, name=f"scraper-{name}", daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        logger.error(
            "%s HARD TIMEOUT after %d seconds. Thread is still running but will be "
            "abandoned (daemon thread dies on process exit).",
            name, timeout_seconds,
        )
        result["status"] = "timeout"
        result["error"] = f"Hard timeout after {timeout_seconds}s"

    return result


TA_ENRICHMENT_TIMEOUT = int(os.getenv("TA_ENRICHMENT_TIMEOUT", "3600"))  # 1 hour


def _run_ta_enrichment(results: list) -> None:
    """Run TripAdvisor enrichment: scrape ratings + search unmatched hotels.

    Uses the same PROXY_* env vars as the deal scrapers. Non-fatal — failures
    are logged but don't block the rest of the cycle.

    Uses short-lived DB sessions to avoid holding connections during slow
    network operations (each hotel scrape takes 4-8s).
    """
    # 1. Scrape ratings for matched hotels missing TA data
    logger.info("=== Starting TA rating scrape ===")
    try:
        from scripts.scrape_ta_ratings import scrape_hotel_rating
        from sqlalchemy import select
        from app.db.models.hotel_link import HotelLink
        from app.db.session import get_db

        # Fetch hotel IDs + names with a short-lived session
        with next(get_db()) as db:
            hotel_rows = db.execute(
                select(HotelLink.hotel_id, HotelLink.hotel_name).where(
                    HotelLink.tripadvisor_url.isnot(None),
                    HotelLink.tripadvisor_url != "",
                    HotelLink.ta_data_fetched_at.is_(None),
                ).order_by(HotelLink.hotel_name)
            ).all()

        total = len(hotel_rows)
        if total:
            logger.info("Scraping TA ratings for %d hotels", total)
            found = 0
            batch = []  # collect (hotel_id, data) tuples

            for i, (hotel_id, hotel_name) in enumerate(hotel_rows):
                if _shutdown_requested:
                    logger.info("Shutdown requested, stopping TA scrape at %d/%d", i, total)
                    break
                try:
                    data = scrape_hotel_rating(hotel_name)
                    batch.append((hotel_id, data))
                    if data["ta_rating"] or data["ta_review_count"]:
                        found += 1
                except Exception:
                    logger.exception("Error scraping TA rating for %s", hotel_name)

                # Flush batch to DB every 25 hotels — short session per batch
                if len(batch) >= 25:
                    _flush_rating_batch(batch)
                    batch = []

                if i < total - 1:
                    time.sleep(random.uniform(4.0, 8.0))

            if batch:
                _flush_rating_batch(batch)

            logger.info("=== TA rating scrape done: %d/%d found ===", found, total)
            results.append({"provider": "ta_ratings", "status": "completed", "total": total, "found": found})
        else:
            logger.info("No hotels need TA rating scrape")
            results.append({"provider": "ta_ratings", "status": "completed", "total": 0, "found": 0})
    except Exception as e:
        logger.exception("TA rating scrape failed (non-fatal)")
        results.append({"provider": "ta_ratings", "status": "failed", "error": str(e)})

    # 2. Search for unmatched hotels' TA pages
    if _shutdown_requested:
        return

    logger.info("=== Starting TA unmatched search ===")
    try:
        from scripts.search_ta_unmatched import search_hotel
        from sqlalchemy import select, or_
        from app.db.models.hotel_link import HotelLink
        from app.db.session import get_db

        # Fetch IDs + names + destinations with a short-lived session
        with next(get_db()) as db:
            hotel_rows = db.execute(
                select(HotelLink.hotel_id, HotelLink.hotel_name, HotelLink.destination).where(
                    HotelLink.tripadvisor_url.is_(None),
                    or_(
                        HotelLink.review_status == "not_found",
                        HotelLink.review_status.is_(None),
                    ),
                ).order_by(HotelLink.hotel_name)
            ).all()

        total = len(hotel_rows)
        if total:
            logger.info("Searching TA pages for %d unmatched hotels", total)
            found = 0
            batch = []  # collect (hotel_id, candidate_or_none) tuples

            for i, (hotel_id, hotel_name, destination) in enumerate(hotel_rows):
                if _shutdown_requested:
                    logger.info("Shutdown requested, stopping TA search at %d/%d", i, total)
                    break
                try:
                    candidate = search_hotel(hotel_name, destination or "")
                    batch.append((hotel_id, candidate))
                    if candidate:
                        found += 1
                except Exception:
                    logger.exception("Error searching TA for %s", hotel_name)

                if len(batch) >= 25:
                    _flush_search_batch(batch)
                    batch = []

                if i < total - 1:
                    time.sleep(random.uniform(4.0, 8.0))

            if batch:
                _flush_search_batch(batch)

            logger.info("=== TA unmatched search done: %d/%d found ===", found, total)
            results.append({"provider": "ta_search", "status": "completed", "total": total, "found": found})
        else:
            logger.info("No unmatched hotels to search")
            results.append({"provider": "ta_search", "status": "completed", "total": 0, "found": 0})
    except Exception as e:
        logger.exception("TA unmatched search failed (non-fatal)")
        results.append({"provider": "ta_search", "status": "failed", "error": str(e)})


def _flush_rating_batch(batch: list[tuple]) -> None:
    """Write a batch of scraped ratings to DB using a short-lived session."""
    from sqlalchemy import select
    from app.db.models.hotel_link import HotelLink
    from app.db.session import get_db

    with next(get_db()) as db:
        for hotel_id, data in batch:
            hotel = db.execute(
                select(HotelLink).where(HotelLink.hotel_id == hotel_id)
            ).scalar_one_or_none()
            if not hotel:
                continue
            if data["ta_rating"]:
                hotel.ta_rating = data["ta_rating"]
            if data["ta_review_count"]:
                hotel.ta_review_count = data["ta_review_count"]
            if data["ta_ranking_text"]:
                hotel.ta_ranking_text = data["ta_ranking_text"]
            hotel.ta_data_fetched_at = datetime.now(timezone.utc)
        db.commit()


def _flush_search_batch(batch: list[tuple]) -> None:
    """Write a batch of search results to DB using a short-lived session."""
    from sqlalchemy import select
    from app.db.models.hotel_link import HotelLink
    from app.db.session import get_db

    with next(get_db()) as db:
        for hotel_id, candidate in batch:
            hotel = db.execute(
                select(HotelLink).where(HotelLink.hotel_id == hotel_id)
            ).scalar_one_or_none()
            if not hotel:
                continue
            if candidate:
                hotel.suggested_url = candidate["tripadvisor_url"]
                hotel.suggested_name = candidate["tripadvisor_name"]
                hotel.tripadvisor_id = candidate["tripadvisor_id"]
                hotel.review_status = "needs_manual_review"
                hotel.match_method = "ddg_search"
                hotel.match_notes = "Found via DuckDuckGo search"
            else:
                hotel.review_status = "not_found"
                hotel.match_notes = "No TripAdvisor page found via search"
            hotel.updated_at = datetime.now(timezone.utc)
        db.commit()


def run_orchestrated_cycle() -> dict:
    """Run all enabled scrapers in sequence. Returns combined summary."""
    cycle_start = datetime.now(timezone.utc)
    results = []

    logger.info(
        "Hard timeouts: SellOff=%ds (%dh), RedTag=%ds (%dh)",
        SELLOFF_HARD_TIMEOUT, SELLOFF_HARD_TIMEOUT // 3600,
        REDTAG_HARD_TIMEOUT, REDTAG_HARD_TIMEOUT // 3600,
    )

    # Accumulate deals from all scrapers for a single consolidated alert email
    combined_signal_deals: dict = {}

    # --- SellOff ---
    if _shutdown_requested:
        logger.info("Shutdown requested before SellOff, skipping")
    else:
        logger.info("=== Starting SellOff scraper ===")
        from app.workers.selloff_scraper import run_scraper as run_selloff

        selloff_result_holder: dict = {"v2_signal_deals": None}

        def _run_selloff():
            selloff_result_holder["v2_signal_deals"] = run_selloff(
                once=True, defer_alerts=True,
            )

        outcome = _run_with_timeout(_run_selloff, "SellOff", SELLOFF_HARD_TIMEOUT)
        if outcome["status"] == "completed":
            logger.info("=== SellOff scraper complete ===")
            results.append({"provider": "selloff", "status": "completed"})
            selloff_deals = selloff_result_holder["v2_signal_deals"] or {}
            for sig_id, deals in selloff_deals.items():
                combined_signal_deals.setdefault(sig_id, []).extend(deals)
        elif outcome["status"] == "timeout":
            logger.error("=== SellOff scraper TIMED OUT ===")
            results.append({"provider": "selloff", "status": "timeout", "error": outcome["error"]})
        else:
            logger.error("=== SellOff scraper FAILED ===")
            results.append({"provider": "selloff", "status": "failed", "error": outcome["error"]})

    # --- RedTag ---
    if _shutdown_requested:
        logger.info("Shutdown requested before RedTag, skipping")
    elif not _is_scraper_enabled("redtag_scraper_enabled"):
        logger.info("RedTag scraper disabled via system_config, skipping")
        results.append({"provider": "redtag", "status": "disabled"})
    else:
        logger.info("=== Starting RedTag scraper ===")
        from app.workers.redtag_scraper import run_once as run_redtag_once

        redtag_result_holder = {"result": None}

        def _run_redtag():
            redtag_result_holder["result"] = run_redtag_once(dry_run=False)

        outcome = _run_with_timeout(_run_redtag, "RedTag", REDTAG_HARD_TIMEOUT)

        if outcome["status"] == "completed" and redtag_result_holder["result"] is not None:
            redtag_result = redtag_result_holder["result"]
            results.append({
                "provider": "redtag",
                "status": "completed",
                "total_deals": redtag_result.get("total_deals", 0),
                "total_matches": redtag_result.get("total_matches", 0),
            })
            logger.info("=== RedTag scraper complete ===")
            redtag_deals = redtag_result.get("v2_signal_deals", {})
            for sig_id, deals in redtag_deals.items():
                combined_signal_deals.setdefault(sig_id, []).extend(deals)
        elif outcome["status"] == "timeout":
            logger.error("=== RedTag scraper TIMED OUT ===")
            results.append({"provider": "redtag", "status": "timeout", "error": outcome["error"]})
        else:
            results.append({"provider": "redtag", "status": "failed", "error": outcome.get("error", "unknown")})

    # --- Send consolidated match alerts (one email per user across all scrapers) ---
    if combined_signal_deals:
        try:
            from collections import defaultdict
            from app.workers.selloff_scraper import _send_cycle_alerts
            signal_count = len(combined_signal_deals)
            deal_count = sum(len(d) for d in combined_signal_deals.values())
            logger.info(
                "Sending consolidated alerts: %d signals, %d total deals across all scrapers",
                signal_count, deal_count,
            )
            _send_cycle_alerts(combined_signal_deals, defaultdict(dict))
            logger.info("Consolidated alerts sent successfully")
        except Exception as e:
            logger.error(
                "Consolidated match alert sending failed: %s. "
                "Affected signals: %s",
                e, list(combined_signal_deals.keys()),
            )

    # --- TripAdvisor enrichment disabled (DDG snippets unreliable) ---
    # if not _shutdown_requested:
    #     _run_ta_enrichment(results)

    # --- Refresh intelligence caches ---
    try:
        from app.services.signal_intel import refresh_all_active_signal_caches, refresh_route_intel_cache
        from app.db.session import get_db
        with next(get_db()) as db:
            refresh_all_active_signal_caches(db)
            refresh_route_intel_cache(db)
        logger.info("Intelligence caches refreshed")
    except Exception as e:
        logger.warning("Intel cache refresh failed: %s", e)

    cycle_end = datetime.now(timezone.utc)
    elapsed = (cycle_end - cycle_start).total_seconds()
    logger.info(
        "Orchestrated cycle complete. Elapsed: %.0fs (%.1fh). Scrapers: %s",
        elapsed, elapsed / 3600,
        ", ".join(f"{r['provider']}={r['status']}" for r in results),
    )

    return {
        "started_at": cycle_start,
        "completed_at": cycle_end,
        "results": results,
    }


def _cleanup_orphaned_runs() -> None:
    """Mark any 'running' scrape_runs as 'stale' on startup.

    If the orchestrator crashed or was restarted, previous runs may be stuck
    with status='running' even though nothing is processing them.
    """
    try:
        from sqlalchemy import text
        from app.db.session import get_db
        with next(get_db()) as db:
            result = db.execute(
                text("UPDATE scrape_runs SET status = 'stale', completed_at = NOW() "
                     "WHERE status = 'running'")
            )
            db.commit()
            if result.rowcount > 0:
                logger.info("Marked %d orphaned scrape_run(s) as stale", result.rowcount)
    except Exception as e:
        logger.warning("Failed to clean up orphaned runs: %s", e)


def run_orchestrator(once: bool = False) -> None:
    """Main entry point — manages scheduling and runs scraper cycles."""
    logger.info("Scrape orchestrator starting (weekdays 5-10AM ET, weekends 7AM-1PM ET)")
    _cleanup_orphaned_runs()

    if not once:
        if not _in_scrape_window():
            next_time = _next_scrape_time()
            next_et = next_time.astimezone(_ET)
            sleep_sec = max(0, (next_time - datetime.now(timezone.utc)).total_seconds())
            hours, remainder = divmod(int(sleep_sec), 3600)
            minutes = remainder // 60
            logger.info(
                "Not in a scrape window — next cycle at %s ET (%dh %dm from now)",
                next_et.strftime("%Y-%m-%d %I:%M %p"), hours, minutes,
            )
            try:
                import requests as _req
                _req.post("http://api:8000/api/system/next-scan", json={
                    "next_scan_at": next_time.timestamp(),
                    "last_scan_at": datetime.now(timezone.utc).timestamp(),
                }, headers=_SYSTEM_API_HEADERS, timeout=5)
            except Exception:
                pass
            time.sleep(sleep_sec)
        else:
            logger.info("Currently inside a scrape window — starting immediately")

    while True:
        try:
            summary = run_orchestrated_cycle()
        except Exception as e:
            logger.error("ORCHESTRATOR CYCLE CRASHED: %s\n%s", e, traceback.format_exc())

        if once or _shutdown_requested:
            if _shutdown_requested:
                logger.info("Shutting down gracefully")
            return

        # Schedule next cycle
        next_time = _next_scrape_time()
        next_et = next_time.astimezone(_ET)
        sleep_sec = max(0, (next_time - datetime.now(timezone.utc)).total_seconds())
        hours, remainder = divmod(int(sleep_sec), 3600)
        minutes = remainder // 60
        logger.info(
            "Next cycle at %s ET (%dh %dm from now)",
            next_et.strftime("%Y-%m-%d %I:%M %p"), hours, minutes,
        )
        try:
            import requests as _req
            _req.post("http://api:8000/api/system/next-scan", json={
                "next_scan_at": next_time.timestamp(),
                "last_scan_at": datetime.now(timezone.utc).timestamp(),
            }, headers=_SYSTEM_API_HEADERS, timeout=5)
        except Exception:
            pass
        time.sleep(sleep_sec)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TripSignal scrape orchestrator")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    run_orchestrator(once=args.once)
