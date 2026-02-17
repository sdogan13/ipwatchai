"""
Backfill translations for existing trademarks in database.
Supports: Turkish, English, Kurdish, Farsi

Usage:
    python scripts/backfill_translations.py --limit 1000 --batch-size 100
    python scripts/backfill_translations.py --all
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', '')
    )


def backfill_translations(limit=None, batch_size=100):
    """Backfill translations for trademarks missing any translation field."""

    print("=" * 60)
    print("Translation Backfill (TR, EN, KU, FA)")
    print("=" * 60)

    # Import and initialize translation module
    from utils.translation import get_translations, initialize, is_ready

    print("\nLoading TranslateGemma...")
    if not initialize():
        print("Failed to load translation model")
        return
    print("Model loaded")

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            # Count records needing translation
            cur.execute("""
                SELECT COUNT(*) FROM trademarks
                WHERE name IS NOT NULL
                  AND name != ''
                  AND name_tr IS NULL
            """)
            total_pending = cur.fetchone()[0]
            print(f"\nTrademarks needing translation: {total_pending:,}")

            if limit:
                total_to_process = min(limit, total_pending)
                print(f"Processing limit: {total_to_process:,}")
            else:
                total_to_process = total_pending

            processed = 0
            start_time = time.time()

            while processed < total_to_process:
                cur.execute("""
                    SELECT id, name FROM trademarks
                    WHERE name IS NOT NULL
                      AND name != ''
                      AND name_tr IS NULL
                    LIMIT %s
                """, (batch_size,))

                rows = cur.fetchall()
                if not rows:
                    break

                updates = []
                for row_id, name in rows:
                    translations = get_translations(name)
                    updates.append((
                        translations.get('tr'),
                        translations.get('detected_lang'),
                        row_id
                    ))

                execute_batch(cur, """
                    UPDATE trademarks
                    SET name_tr = COALESCE(%s, name_tr),
                        detected_lang = COALESCE(%s, detected_lang)
                    WHERE id = %s
                """, updates)

                conn.commit()

                processed += len(rows)
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Processed {processed:,}/{total_to_process:,} ({rate:.1f}/s)")

            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0

            print(f"\nCompleted!")
            print(f"  Processed: {processed:,} trademarks")
            print(f"  Time: {elapsed:.1f}s")
            print(f"  Rate: {rate:.1f} translations/sec")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill trademark translations")
    parser.add_argument('--limit', '-l', type=int, help="Max records to process")
    parser.add_argument('--batch-size', '-b', type=int, default=100, help="Batch size")
    parser.add_argument('--all', action='store_true', help="Process all records")

    args = parser.parse_args()

    limit = None if args.all else (args.limit or 1000)
    backfill_translations(limit=limit, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
