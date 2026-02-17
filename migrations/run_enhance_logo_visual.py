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


def run():
    sql_path = Path(__file__).parent / "enhance_logo_visual_features.sql"
    sql = sql_path.read_text(encoding="utf-8")

    with Database() as db:
        cur = db.cursor()
        cur.execute(sql)
        db.commit()
        print("[OK] enhance_logo_visual_features.sql applied successfully.")


if __name__ == "__main__":
    run()
