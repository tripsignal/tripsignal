"""Database base classes and models."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


# Import models so Alembic can autogenerate migrations
import app.db.models.signal  # noqa: F401
import app.db.models.deal  # noqa: F401
import app.db.models.deal_match  # noqa: F401
import app.db.models.signal_run  # noqa: F401
