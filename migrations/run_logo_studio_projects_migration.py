"""
Migration runner: Add Logo Studio project threads and audit status columns.

Usage:
    python migrations/run_logo_studio_projects_migration.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.crud import Database


def _apply_sql() -> None:
    sql_path = Path(__file__).parent / "logo_studio_projects.sql"
    sql = sql_path.read_text(encoding="utf-8")

    with Database() as db:
        cur = db.cursor()
        cur.execute(sql)
        db.commit()


def run_migration() -> bool:
    try:
        _apply_sql()
        print("[OK] logo_studio_projects.sql applied successfully.")
        return True
    except Exception as exc:
        print(f"[ERROR] logo_studio_projects.sql failed: {exc}")
        return False


def ensure_logo_studio_projects_schema() -> bool:
    """Ensure Logo Studio project and asynchronous audit columns exist."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_name = 'logo_projects'
                ) AS has_projects
                """
            )
            row = cur.fetchone()
            has_projects = row["has_projects"] if isinstance(row, dict) else row[0]

            cur.execute(
                """
                SELECT COUNT(*) AS present
                FROM information_schema.columns
                WHERE table_name = 'generated_images'
                  AND column_name IN (
                      'project_id',
                      'parent_image_id',
                      'variant_index',
                      'generation_kind',
                      'revision_prompt',
                      'audit_status',
                      'audit_error',
                      'audited_at'
                  )
                """
            )
            row = cur.fetchone()
            present = row["present"] if isinstance(row, dict) else row[0]
            if has_projects and present == 8:
                return True

        _apply_sql()
        return True
    except Exception:
        return False


def run():
    run_migration()


if __name__ == "__main__":
    sys.exit(0 if run_migration() else 1)
