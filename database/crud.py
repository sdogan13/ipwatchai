"""Database CRUD compatibility facade."""

import psycopg2
from psycopg2.extras import RealDictCursor

from config.settings import settings


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        dbname=settings.database.name,
        user=settings.database.user,
        password=settings.database.password,
        host=settings.database.host,
        port=settings.database.port,
    )


class Database:
    """Database operations class."""

    def __init__(self, conn=None):
        self.conn = conn or get_db_connection()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        self.conn.close()

    def cursor(self, **kwargs):
        kwargs.setdefault("cursor_factory", RealDictCursor)
        return self.conn.cursor(**kwargs)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


from database.repositories.application_repository import ApplicationCRUD  # noqa: F401  re-exported
from database.repositories.alert_repository import AlertCRUD  # noqa: F401  re-exported
from database.repositories.organization_repository import OrganizationCRUD  # noqa: F401  re-exported
from database.repositories.scan_log_repository import ScanLogCRUD  # noqa: F401  re-exported
from database.repositories.user_repository import UserCRUD  # noqa: F401  re-exported
from database.repositories.watchlist_repository import WatchlistCRUD  # noqa: F401  re-exported
