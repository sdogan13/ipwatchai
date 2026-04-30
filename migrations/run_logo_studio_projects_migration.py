"""
Migration runner: Add Logo Studio project threads and audit status columns.

Usage:
    python migrations/run_logo_studio_projects_migration.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.crud import Database


def run():
    sql_path = Path(__file__).parent / "logo_studio_projects.sql"
    sql = sql_path.read_text(encoding="utf-8")

    with Database() as db:
        cur = db.cursor()
        cur.execute(sql)
        db.commit()
        print("[OK] logo_studio_projects.sql applied successfully.")


if __name__ == "__main__":
    run()
