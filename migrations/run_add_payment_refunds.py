"""
Run payment refund columns migration.

Usage:
    python migrations/run_add_payment_refunds.py
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
    sql_path = Path(__file__).parent / "add_payment_refunds.sql"
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
        print("Migration complete: payment refund columns added")
        return True
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


def ensure_payment_refund_columns():
    """Add refund columns to payments table if missing. Safe for startup."""
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
        # Check if refund_status column already exists
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'payments' AND column_name = 'refund_status'
            )
        """)
        exists = cur.fetchone()[0]
        if exists:
            return True

        sql_path = Path(__file__).parent / "add_payment_refunds.sql"
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
