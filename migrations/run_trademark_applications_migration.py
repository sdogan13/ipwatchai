"""
Run the Trademark Applications migration.
Usage: python migrations/run_trademark_applications_migration.py
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
    """Execute the Trademark Applications migration."""

    sql_file = Path(__file__).parent / "trademark_applications.sql"

    if not sql_file.exists():
        print(f"Migration file not found: {sql_file}")
        return False

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', '')
    )

    try:
        with conn.cursor() as cur:
            print("Running Trademark Applications migration...")
            cur.execute(sql)
            conn.commit()
            print("Migration completed successfully!")

            # Verify table exists
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'trademark_applications_mt'
            """)
            tables = [row[0] for row in cur.fetchall()]
            print(f"Created tables: {', '.join(tables)}")

            # Show column count
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'trademark_applications_mt'
            """)
            col_count = cur.fetchone()[0]
            print(f"Column count: {col_count}")

        return True

    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
