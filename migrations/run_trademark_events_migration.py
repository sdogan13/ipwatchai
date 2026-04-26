"""
Run trademark_events schema migration.

Usage:
    python migrations/run_trademark_events_migration.py
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
load_dotenv()


def _db_config():
    try:
        from config.settings import settings

        return {
            "host": settings.database.host,
            "port": settings.database.port,
            "database": settings.database.name,
            "user": settings.database.user,
            "password": settings.database.password,
            "connect_timeout": 30,
        }
    except Exception:
        return {
            "host": os.getenv("DB_HOST", "127.0.0.1"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "database": os.getenv("DB_NAME", "trademark_db"),
            "user": os.getenv("DB_USER", "turk_patent"),
            "password": os.getenv("DB_PASSWORD", ""),
            "connect_timeout": 30,
        }


def _connect():
    return psycopg2.connect(**_db_config())


def _needs_migration(cur) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'trademark_events'
        )
        """
    )
    table_exists = cur.fetchone()[0]
    if not table_exists:
        return True

    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'trademark_events'
              AND column_name = 'event_fingerprint'
        )
        """
    )
    fingerprint_exists = cur.fetchone()[0]
    if not fingerprint_exists:
        return True

    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'trademark_events'
              AND indexname = 'uq_trademark_event'
              AND indexdef ILIKE '%event_fingerprint%'
        )
        """
    )
    fingerprint_index_exists = cur.fetchone()[0]
    if not fingerprint_index_exists:
        return True

    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM trademark_events
            WHERE event_fingerprint IS NULL
            LIMIT 1
        )
        """
    )
    return cur.fetchone()[0]


def _run_sql(conn) -> bool:
    sql_path = Path(__file__).parent / "trademark_events.sql"
    if not sql_path.exists():
        print(f"ERROR: Migration file not found: {sql_path}")
        return False

    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        cur.close()


def ensure_trademark_events_schema() -> bool:
    """Ensure trademark_events has the current full-payload dedup schema."""
    conn = _connect()
    try:
        cur = conn.cursor()
        try:
            needs_migration = _needs_migration(cur)
        finally:
            cur.close()

        if not needs_migration:
            return True
        return _run_sql(conn)
    finally:
        conn.close()


def run_migration():
    conn = _connect()
    try:
        success = _run_sql(conn)
        if success:
            print("Migration complete: trademark_events schema updated")
        return success
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
