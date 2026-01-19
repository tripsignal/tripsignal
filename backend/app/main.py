"""FastAPI application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import setup_logging
from app.api.routes import health
from app.api.routes.deal_matches import router as deal_matches_router
from app.api.signals import router as signals_router

# Setup logging
setup_logging()

# Create FastAPI app
app = FastAPI(
    title="TripSignal API",
    description="Backend API for TripSignal",
    version="1.0.0",
)

# CORS middleware (configure as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(signals_router)
app.include_router(deal_matches_router)


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
