"""
Run descriptor_idf_stats migration.

Usage:
    python migrations/run_descriptor_idf_stats_migration.py
"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import psycopg2
from dotenv import load_dotenv

load_dotenv()

try:
    from config.settings import settings
except Exception:
    settings = None


def run_migration():
    sql_path = Path(__file__).parent / "descriptor_idf_stats.sql"
    if not sql_path.exists():
        print(f"ERROR: Migration file not found: {sql_path}")
        return False

    sql = sql_path.read_text(encoding="utf-8")

    conn_kwargs = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "database": os.getenv("DB_NAME", "trademark_db"),
        "user": os.getenv("DB_USER", "turk_patent"),
        "password": os.getenv("DB_PASSWORD", ""),
        "connect_timeout": 30,
    }

    if settings is not None:
        conn_kwargs.update(
            {
                "host": settings.database.host,
                "port": settings.database.port,
                "database": settings.database.name,
                "user": settings.database.user,
                "password": settings.database.password,
            }
        )

    conn = psycopg2.connect(**conn_kwargs)

    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print("Migration complete: descriptor IDF columns added")
        return True
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
