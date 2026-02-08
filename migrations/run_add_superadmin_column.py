"""
Runner for add_superadmin_column.sql migration.
Usage: python migrations/run_add_superadmin_column.py
"""
import os
import sys
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings


def run_migration():
    sql_path = os.path.join(os.path.dirname(__file__), "add_superadmin_column.sql")
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
        print("Migration applied: add_superadmin_column.sql")
        cur.close()
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
