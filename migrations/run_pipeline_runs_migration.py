"""
Run pipeline_runs migration.

Usage:
    python migrations/run_pipeline_runs_migration.py
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
    sql_path = Path(__file__).parent / "pipeline_runs.sql"
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
        print("Migration complete: pipeline_runs table created")
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
