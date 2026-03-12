"""Shared utilities for TripAdvisor enrichment scripts.

Provides proxy configuration and DB engine creation used across
scrape_ta_ratings, search_ta_unmatched, and load_ta_ratings.
"""

import os


def build_proxy_config() -> dict | None:
    """Build requests-compatible proxies dict from env vars.

    Uses the same PROXY_* env vars as scrape_orchestrator / deal scrapers.
    Returns None if proxy is disabled or not configured.
    """
    if os.getenv("PROXY_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return None
    proxy_user = os.getenv("PROXY_USER", "")
    if not proxy_user:
        return None
    proxy_host = os.getenv("PROXY_HOST", "gw.dataimpulse.com")
    proxy_port = os.getenv("PROXY_PORT", "823")
    proxy_pass = os.getenv("PROXY_PASS", "")
    proxy_country = os.getenv("PROXY_COUNTRY", "cr.ca")
    proxy_url = f"http://{proxy_user}__{proxy_country}:{proxy_pass}@{proxy_host}:{proxy_port}"
    return {"http": proxy_url, "https": proxy_url}


def get_engine():
    """Create a SQLAlchemy engine from env vars (DATABASE_URL or POSTGRES_*)."""
    from sqlalchemy import create_engine
    url = os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "postgres")
        pw = os.getenv("POSTGRES_PASSWORD", "postgres")
        db = os.getenv("POSTGRES_DB", "tripsignal")
        url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"
    return create_engine(url)
