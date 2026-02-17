"""Run the add_watchlist_ocr migration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.crud import Database

def main():
    sql_path = Path(__file__).parent / "add_watchlist_ocr.sql"
    sql = sql_path.read_text(encoding="utf-8")

    with Database() as db:
        cur = db.cursor()
        cur.execute(sql)
        db.commit()
        print("Migration complete: logo_ocr_text added to watchlist_mt")

if __name__ == "__main__":
    main()
