"""
Run the universal_conflicts migration.
Usage: python migrations/run_universal_conflicts_migration.py
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
    """Execute the universal_conflicts migration."""

    # Read SQL file
    sql_file = Path(__file__).parent / "add_universal_conflicts.sql"

    if not sql_file.exists():
        print(f"Migration file not found: {sql_file}")
        return False

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Connect to database
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', '')
    )

    try:
        with conn.cursor() as cur:
            print("Running universal_conflicts migration...")
            cur.execute(sql)
            conn.commit()
            print("Migration completed successfully!")

            # Verify tables exist
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name IN ('universal_conflicts', 'universal_scan_queue', 'lead_access_log')
            """)
            tables = [row[0] for row in cur.fetchall()]
            print(f"Created tables: {', '.join(tables)}")

            # Verify views exist
            cur.execute("""
                SELECT table_name FROM information_schema.views
                WHERE table_schema = 'public'
                AND table_name IN ('active_leads', 'lead_statistics')
            """)
            views = [row[0] for row in cur.fetchall()]
            print(f"Created views: {', '.join(views)}")

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
