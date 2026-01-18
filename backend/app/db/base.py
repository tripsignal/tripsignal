"""Database base classes and models."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass

# Import models so Alembic can autogenerate migrations
import app.db.models.signal
