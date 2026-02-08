"""
Runner for add_app_settings.sql migration.
Usage: python migrations/run_add_app_settings.py
"""
import os
import sys
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings


def run_migration():
    sql_path = os.path.join(os.path.dirname(__file__), "add_app_settings.sql")
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = psycopg2.connect(
        dbname=settings.database.name,
        user=settings.database.user,
        password=settings.database.password,
        host=settings.database.host,
        port=settings.database.port,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print("Migration applied: add_app_settings.sql")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        conn.close()


def ensure_app_settings_table() -> bool:
    """Check if app_settings table exists, create if not. Returns True on success."""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=settings.database.name,
            user=settings.database.user,
            password=settings.database.password,
            host=settings.database.host,
            port=settings.database.port,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'app_settings')"
        )
        exists = cur.fetchone()[0]
        if not exists:
            run_migration()
        cur.close()
        return True
    except Exception:
        return False
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run_migration()
