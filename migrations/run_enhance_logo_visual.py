"""
Migration runner: Add full visual feature columns to generated_images.

Usage:
    python migrations/run_enhance_logo_visual.py
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.crud import Database


def _apply_sql() -> None:
    sql_path = Path(__file__).parent / "enhance_logo_visual_features.sql"
    sql = sql_path.read_text(encoding="utf-8")

    with Database() as db:
        cur = db.cursor()
        cur.execute(sql)
        db.commit()


def run_migration() -> bool:
    try:
        _apply_sql()
        print("[OK] enhance_logo_visual_features.sql applied successfully.")
        return True
    except Exception as exc:
        print(f"[ERROR] enhance_logo_visual_features.sql failed: {exc}")
        return False


def ensure_logo_visual_columns() -> bool:
    """Ensure generated Logo Studio images can store async visual audit fields."""
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS present
                FROM information_schema.columns
                WHERE table_name = 'generated_images'
                  AND column_name IN ('dino_embedding', 'ocr_text', 'visual_breakdown')
                """
            )
            row = cur.fetchone()
            present = row["present"] if isinstance(row, dict) else row[0]
            if present == 3:
                return True

        _apply_sql()
        return True
    except Exception:
        return False


def run():
    run_migration()


if __name__ == "__main__":
    sys.exit(0 if run_migration() else 1)
