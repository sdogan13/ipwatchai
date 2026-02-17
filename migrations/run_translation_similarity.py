"""Run translation similarity migration."""
import os
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def run_migration():
    print("=" * 60)
    print("Translation Similarity Migration")
    print("=" * 60)

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', '')
    )

    sql_file = Path(__file__).parent / 'add_translation_similarity.sql'

    try:
        with conn.cursor() as cur:
            cur.execute(sql_file.read_text())
            conn.commit()
        print("Migration completed successfully!")
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
