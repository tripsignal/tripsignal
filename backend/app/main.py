"""FastAPI application entry point."""
import logging
import time
import traceback
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import verify_admin
from app.api.routes import health
from app.api.routes.admin import router as admin_router
from app.api.routes.billing import router as billing_router
from app.api.routes.clerk_webhook import router as clerk_webhook_router
from app.api.routes.deal_matches import router as deal_matches_router
from app.api.routes.deal_public import router as deal_public_router
from app.api.routes.stats import router as stats_router
from app.api.routes.scout import router as scout_router
from app.api.routes.market import router as market_router
from app.api.routes.resend_webhooks import router as resend_webhook_router
from app.api.routes.unsubscribe import router as unsubscribe_router
from app.api.routes.users import router as users_router
from app.api.signals import router as signals_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.db.models.notification_outbox import NotificationOutbox
from app.db.models.scrape_run import ScrapeRun
from app.db.session import get_db

# Setup logging
setup_logging()

# Create FastAPI app
app = FastAPI(
    title="TripSignal API",
    description="Backend API for TripSignal",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tripsignal.ca",
        "https://www.tripsignal.ca",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "x-timezone",
        "X-Admin-Token",
    ],
)


# Request logging middleware
_request_logger = logging.getLogger('tripsignal.access')
_SKIP_LOG_PATHS = {'/health', '/'}


@app.middleware('http')
async def log_requests(request: Request, call_next):
    if request.url.path in _SKIP_LOG_PATHS:
        return await call_next(request)

    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000

    xff = request.headers.get('x-forwarded-for', '')
    client_ip = (xff.split(",")[-1].strip() if xff
                 else (request.client.host if request.client else 'unknown'))

    _request_logger.info(
        '%s | %s | %s %s | %s | %.0fms',
        datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        client_ip,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )

    return response


# Security response headers
@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# Catch-all exception handler — log full traceback server-side, return generic error to client
_logger = logging.getLogger(__name__)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
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
app.include_router(resend_webhook_router)
app.include_router(market_router)
app.include_router(scout_router)
app.include_router(deal_public_router)
app.include_router(stats_router)

@app.post("/api/system/next-scan", dependencies=[Depends(verify_admin)])
def set_next_scan(payload: dict, db: Session = Depends(get_db)):
    """Called by scraper to register next scan time. Persists to system_config."""
    next_scan_at = payload.get("next_scan_at")
    db.execute(text(
        "INSERT INTO system_config (key, value, updated_at) "
        "VALUES ('next_scan_at', :val, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = :val, updated_at = now()"
    ), {"val": str(next_scan_at) if next_scan_at is not None else ""})
    db.commit()
    return {"ok": True}


@app.get("/api/system/next-scan")
def next_scan(db: Session = Depends(get_db)):
    """Return the next scheduled scrape time from system_config."""
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


@app.post("/api/system/scrape-started", dependencies=[Depends(verify_admin)])
def scrape_started(payload: dict, db: Session = Depends(get_db)):
    """Called by scraper when a cycle begins. Creates a ScrapeRun row."""
    started_at_str = payload.get("started_at")
    started_at = datetime.fromisoformat(started_at_str) if started_at_str else datetime.now(timezone.utc)
    run = ScrapeRun(started_at=started_at, status="running", proxy_ip=payload.get("proxy_ip"), proxy_geo=payload.get("proxy_geo"))
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"ok": True, "run_id": run.id}


@app.post("/api/system/collection-complete", dependencies=[Depends(verify_admin)])
def collection_complete(payload: dict, db: Session = Depends(get_db)):
    """Called by scraper when a cycle finishes. Updates or creates a ScrapeRun row."""
    # Prefer explicit run_id correlation; fall back to latest running
    payload_run_id = payload.get("run_id")
    if payload_run_id:
        run = db.execute(
            select(ScrapeRun).where(ScrapeRun.id == payload_run_id)
        ).scalar_one_or_none()
    else:
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
        run.deals_expired = payload.get("deals_expired")
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
            deals_expired=payload.get("deals_expired"),
            status=payload.get("status", "completed"),
            proxy_ip=payload.get("proxy_ip"),
            proxy_geo=payload.get("proxy_geo"),
        )
        db.add(run)

    db.commit()
    return {"ok": True, "run_id": run.id}


# 1x1 transparent PNG
_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@app.get("/api/notifications/{notification_id}/pixel.png")
@limiter.limit("30/minute")
def tracking_pixel(request: Request, notification_id: str, db: Session = Depends(get_db)):
    """Return a 1x1 transparent PNG and track opens."""
    try:
        import uuid as _uuid
        _uuid.UUID(notification_id)  # validate format before querying DB
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
    return Response(
        content=_PIXEL_PNG,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "TripSignal API", "version": "1.0.0"}

