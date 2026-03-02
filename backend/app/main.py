"""FastAPI application entry point."""
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import select, text

from app.core.logging import setup_logging
from app.db.session import get_db
from app.db.models.scrape_run import ScrapeRun
from app.db.models.notification_outbox import NotificationOutbox
from app.api.routes import health
from app.api.routes.deal_matches import router as deal_matches_router
from app.api.routes.billing import router as billing_router
from app.api.routes.admin import router as admin_router
from app.api.routes.unsubscribe import router as unsubscribe_router
from app.api.routes.users import router as users_router
from app.api.routes.clerk_webhook import router as clerk_webhook_router
from app.api.signals import router as signals_router

# Setup logging
setup_logging()

# Create FastAPI app
app = FastAPI(
    title="TripSignal API",
    description="Backend API for TripSignal",
    version="1.0.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(signals_router)
app.include_router(deal_matches_router, prefix="/api")
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(unsubscribe_router)
app.include_router(users_router)
app.include_router(clerk_webhook_router)

@app.post("/api/system/next-scan")
async def set_next_scan(payload: dict):
    """Called by scraper to register next scan time. Persists to system_config."""
    next_scan_at = payload.get("next_scan_at")
    db = next(get_db())
    try:
        db.execute(text(
            "INSERT INTO system_config (key, value, updated_at) "
            "VALUES ('next_scan_at', :val, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = :val, updated_at = now()"
        ), {"val": str(next_scan_at) if next_scan_at is not None else ""})
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/system/next-scan")
async def next_scan():
    """Return the next scheduled scrape time from system_config."""
    db = next(get_db())
    try:
        row = db.execute(text(
            "SELECT value FROM system_config WHERE key = 'next_scan_at'"
        )).first()
        if row and row[0]:
            try:
                val = float(row[0])
                return {"next_scan_at": val, "available": True}
            except (ValueError, TypeError):
                pass
        return {"next_scan_at": None, "available": False}
    finally:
        db.close()


@app.post("/api/system/scrape-started")
async def scrape_started(payload: dict):
    """Called by scraper when a cycle begins. Creates a ScrapeRun row."""
    started_at_str = payload.get("started_at")
    started_at = datetime.fromisoformat(started_at_str) if started_at_str else datetime.now(timezone.utc)
    db = next(get_db())
    try:
        run = ScrapeRun(started_at=started_at, status="running", proxy_ip=payload.get("proxy_ip"), proxy_geo=payload.get("proxy_geo"))
        db.add(run)
        db.commit()
        db.refresh(run)
        return {"ok": True, "run_id": run.id}
    finally:
        db.close()


@app.post("/api/system/collection-complete")
async def collection_complete(payload: dict):
    """Called by scraper when a cycle finishes. Updates or creates a ScrapeRun row."""
    db = next(get_db())
    try:
        run = db.execute(
            select(ScrapeRun)
            .where(ScrapeRun.status == "running")
            .order_by(ScrapeRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        completed_at_str = payload.get("completed_at")
        completed_at = datetime.fromisoformat(completed_at_str) if completed_at_str else datetime.now(timezone.utc)

        if run:
            run.completed_at = completed_at
            run.total_deals = payload.get("total_deals", 0)
            run.total_matches = payload.get("total_matches", 0)
            run.error_count = payload.get("error_count", 0)
            run.error_log = payload.get("errors", [])
            run.deals_deactivated = payload.get("deals_deactivated")
            run.status = payload.get("status", "completed")
            if payload.get("proxy_ip"):
                run.proxy_ip = payload["proxy_ip"]
            if payload.get("proxy_geo"):
                run.proxy_geo = payload["proxy_geo"]
        else:
            started_at_str = payload.get("started_at")
            started_at = datetime.fromisoformat(started_at_str) if started_at_str else completed_at
            run = ScrapeRun(
                started_at=started_at,
                completed_at=completed_at,
                total_deals=payload.get("total_deals", 0),
                total_matches=payload.get("total_matches", 0),
                error_count=payload.get("error_count", 0),
                error_log=payload.get("errors", []),
                deals_deactivated=payload.get("deals_deactivated"),
                status=payload.get("status", "completed"),
                proxy_ip=payload.get("proxy_ip"),
                proxy_geo=payload.get("proxy_geo"),
            )
            db.add(run)

        db.commit()
        return {"ok": True, "run_id": run.id}
    finally:
        db.close()


# 1x1 transparent PNG
_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@app.get("/api/notifications/{notification_id}/pixel.png")
def tracking_pixel(notification_id: str):
    """Return a 1x1 transparent PNG and track opens."""
    db = next(get_db())
    try:
        notif = db.execute(
            select(NotificationOutbox).where(NotificationOutbox.id == notification_id)
        ).scalar_one_or_none()
        if notif:
            if notif.opened_at is None:
                notif.opened_at = datetime.now(timezone.utc)
            notif.open_count = (notif.open_count or 0) + 1
            db.commit()
    except Exception:
        pass
    finally:
        db.close()
    return Response(
        content=_PIXEL_PNG,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "TripSignal API", "version": "1.0.0"}


@app.get("/debug/routes")
def debug_routes():
    return [
        {"path": r.path, "methods": sorted(list(r.methods or []))}
        for r in app.routes
    ]
