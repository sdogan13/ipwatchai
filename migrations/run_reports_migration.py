"""
Run reports table migration.

Usage:
    python migrations/run_reports_migration.py
"""
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def run_migration():
    sql_path = Path(__file__).parent / "reports.sql"
    if not sql_path.exists():
        print(f"ERROR: Migration file not found: {sql_path}")
        return False

    sql = sql_path.read_text(encoding="utf-8")

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=30,
    )

    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print("Migration complete: reports table created")
        return True
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


def ensure_reports_table():
    """Check if reports table exists; create if not. Safe for startup."""
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=30,
    )
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'reports'
            )
        """)
        exists = cur.fetchone()[0]
        if exists:
            return True

        sql_path = Path(__file__).parent / "reports.sql"
        if not sql_path.exists():
            return False

        sql = sql_path.read_text(encoding="utf-8")
        cur.execute(sql)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
