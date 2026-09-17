"""SQLAlchemy/PostGIS persistence boundary. The demo repository stays independent for deterministic tests."""
from sqlalchemy import create_engine
from .config import settings


def create_database_engine():
    return create_engine(settings.database_url, pool_pre_ping=True)
