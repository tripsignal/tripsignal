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

# Schedule: 3 daily windows in Eastern Time
_ET = ZoneInfo("America/Toronto")
_SCRAPE_WINDOWS = [(7, 0, 9, 0), (12, 0, 14, 0), (18, 0, 20, 0)]

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown signal received, will finish after current scraper")


_signal.signal(_signal.SIGTERM, _handle_signal)
_signal.signal(_signal.SIGINT, _handle_signal)


def _in_scrape_window() -> bool:
    now_et = datetime.now(_ET)
    for sh, sm, eh, em in _SCRAPE_WINDOWS:
        ws = now_et.replace(hour=sh, minute=sm, second=0, microsecond=0)
        we = now_et.replace(hour=eh, minute=em, second=0, microsecond=0)
        if ws <= now_et < we:
            return True
    return False


def _next_scrape_time() -> datetime:
    now_et = datetime.now(_ET)
    for day_offset in range(3):
        base = now_et + timedelta(days=day_offset)
        for sh, sm, eh, em in _SCRAPE_WINDOWS:
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


def run_orchestrated_cycle() -> dict:
    """Run all enabled scrapers in sequence. Returns combined summary."""
    cycle_start = datetime.now(timezone.utc)
    results = []

    # --- SellOff ---
    if _shutdown_requested:
        logger.info("Shutdown requested before SellOff, skipping")
    else:
        logger.info("=== Starting SellOff scraper ===")
        try:
            from app.workers.selloff_scraper import run_scraper as run_selloff
            run_selloff(once=True)
            logger.info("=== SellOff scraper complete ===")
            results.append({"provider": "selloff", "status": "completed"})
        except Exception as e:
            logger.error("SellOff scraper failed: %s\n%s", e, traceback.format_exc())
            results.append({"provider": "selloff", "status": "failed", "error": str(e)})

    # --- RedTag ---
    if _shutdown_requested:
        logger.info("Shutdown requested before RedTag, skipping")
    elif not _is_scraper_enabled("redtag_scraper_enabled"):
        logger.info("RedTag scraper disabled via system_config, skipping")
        results.append({"provider": "redtag", "status": "disabled"})
    else:
        logger.info("=== Starting RedTag scraper ===")
        try:
            from app.workers.redtag_scraper import run_once as run_redtag_once
            from collections import defaultdict

            redtag_result = run_redtag_once(dry_run=False)
            results.append({
                "provider": "redtag",
                "status": "completed",
                "total_deals": redtag_result.get("total_deals", 0),
                "total_matches": redtag_result.get("total_matches", 0),
            })
            logger.info("=== RedTag scraper complete ===")

            # Send RedTag match alerts
            if redtag_result.get("v2_signal_deals"):
                try:
                    from app.workers.selloff_scraper import _send_cycle_alerts
                    _send_cycle_alerts(redtag_result["v2_signal_deals"], defaultdict(dict))
                except Exception as e:
                    logger.error("RedTag match alert sending failed: %s", e)

        except Exception as e:
            logger.error("RedTag scraper failed: %s\n%s", e, traceback.format_exc())
            results.append({"provider": "redtag", "status": "failed", "error": str(e)})

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


def run_orchestrator(once: bool = False) -> None:
    """Main entry point — manages scheduling and runs scraper cycles."""
    logger.info("Scrape orchestrator starting (3 daily windows: ~8AM, ~1PM, ~7PM ET)")

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
