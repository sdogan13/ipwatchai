"""
Run the Creative Suite migration (Name Generator + Logo Studio).
Usage: python migrations/run_creative_suite_migration.py
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
    """Execute the Creative Suite migration."""

    # Read SQL file
    sql_file = Path(__file__).parent / "creative_suite.sql"

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
            print("Running Creative Suite migration...")
            cur.execute(sql)
            conn.commit()
            print("Migration completed successfully!")

            # Verify new tables exist
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name IN ('generation_logs', 'generated_images')
            """)
            tables = [row[0] for row in cur.fetchall()]
            print(f"Created tables: {', '.join(tables)}")

            # Verify organizations columns
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'organizations'
                AND column_name IN (
                    'logo_credits_monthly', 'logo_credits_purchased',
                    'name_credits_purchased', 'logo_credits_reset_at'
                )
            """)
            org_cols = [row[0] for row in cur.fetchall()]
            print(f"Organizations new columns: {', '.join(org_cols)}")

            # Verify subscription_plans columns
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'subscription_plans'
                AND column_name IN ('name_suggestions_per_session', 'logo_runs_per_month')
            """)
            plan_cols = [row[0] for row in cur.fetchall()]
            print(f"Subscription plans new columns: {', '.join(plan_cols)}")

            # Show plan limits
            cur.execute("""
                SELECT name, name_suggestions_per_session, logo_runs_per_month
                FROM subscription_plans
                ORDER BY CASE name
                    WHEN 'free' THEN 1
                    WHEN 'starter' THEN 2
                    WHEN 'professional' THEN 3
                    WHEN 'enterprise' THEN 4
                END
            """)
            print("\nPlan limits:")
            for row in cur.fetchall():
                print(f"  {row[0]:15s} | names/session: {row[1]:4d} | logos/month: {row[2]:4d}")

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
