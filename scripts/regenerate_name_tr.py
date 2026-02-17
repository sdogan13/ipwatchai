"""
Regenerate name_tr and detected_lang for all trademarks using FastText LangID.

The old hardcoded detect_language() used character sets and word lists,
which misclassified many Turkish names written without diacritics as English.
FastText LangID is model-based (217 languages) and far more accurate.

Strategy:
  1. Process ALL records (not just NULLs) since the old detector was unreliable
  2. Batch FastText detection (microseconds per record)
  3. Group non-Turkish records by language, batch-translate with NLLB
  4. Update DB in batches

Usage:
  # Full regeneration (all records):
  python scripts/regenerate_name_tr.py

  # Only NULL records:
  python scripts/regenerate_name_tr.py --null-only

  # Dry run (detect only, no DB writes):
  python scripts/regenerate_name_tr.py --dry-run

  # Custom batch size:
  python scripts/regenerate_name_tr.py --batch-size 5000
"""

import os
import sys
import time
import argparse
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DB_PORT', '5433')
os.environ.setdefault('DB_PASSWORD', os.environ.get('DB_PASSWORD', ''))

import psycopg2
from psycopg2.extras import execute_batch

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', '127.0.0.1'),
        port=int(os.environ.get('DB_PORT', '5433')),
        dbname=os.environ.get('DB_NAME', 'trademark_db'),
        user=os.environ.get('DB_USER', 'turk_patent'),
        password=os.environ.get('DB_PASSWORD', ''),
    )


def main():
    parser = argparse.ArgumentParser(description='Regenerate name_tr using FastText LangID')
    parser.add_argument('--null-only', action='store_true',
                        help='Only process records with NULL name_tr/detected_lang')
    parser.add_argument('--redetect-only', action='store_true',
                        help='Only re-detect language (skip translation, update detected_lang only)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Detect languages and print stats, but do not write to DB')
    parser.add_argument('--batch-size', type=int, default=2000,
                        help='Number of records per batch (default: 2000)')
    parser.add_argument('--offset', type=int, default=0,
                        help='Start from this record offset (for resuming)')
    args = parser.parse_args()

    # Import translation functions
    from utils.translation import (
        detect_language_fasttext, batch_translate_to_turkish, turkish_lower
    )

    # Warm up FastText model
    logger.info("Loading FastText LangID model...")
    iso, nllb, conf = detect_language_fasttext("test")
    logger.info(f"FastText ready (test: iso={iso}, nllb={nllb}, conf={conf:.3f})")

    conn = get_db_connection()
    conn.autocommit = False

    # Count total records to process
    with conn.cursor() as cur:
        if args.null_only:
            cur.execute("SELECT COUNT(*) FROM trademarks WHERE name_tr IS NULL OR detected_lang IS NULL")
        else:
            cur.execute("SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL AND name != ''")
        total = cur.fetchone()[0]
    logger.info(f"Total records to process: {total:,}")

    if total == 0:
        logger.info("Nothing to process.")
        return

    # Stats
    stats = {
        'processed': 0,
        'detected_tr': 0,
        'detected_en': 0,
        'detected_other': 0,
        'translated': 0,
        'translation_changed': 0,
        'lang_changed': 0,
        'skipped_empty': 0,
        'errors': 0,
    }
    lang_distribution = {}

    batch_size = args.batch_size
    offset = args.offset
    start_time = time.time()

    while offset < total + args.offset:
        # Fetch batch
        with conn.cursor() as cur:
            if args.null_only:
                cur.execute("""
                    SELECT id, name, name_tr, detected_lang
                    FROM trademarks
                    WHERE name_tr IS NULL OR detected_lang IS NULL
                    ORDER BY id
                    LIMIT %s OFFSET %s
                """, (batch_size, offset - args.offset))
            else:
                cur.execute("""
                    SELECT id, name, name_tr, detected_lang
                    FROM trademarks
                    WHERE name IS NOT NULL AND name != ''
                    ORDER BY id
                    LIMIT %s OFFSET %s
                """, (batch_size, offset))
            rows = cur.fetchall()

        if not rows:
            break

        # Detect languages with FastText
        ids = []
        names = []
        old_name_trs = []
        old_langs = []
        for row in rows:
            ids.append(row[0])
            names.append(row[1] or "")
            old_name_trs.append(row[2])
            old_langs.append(row[3])

        # Run batch translation (FastText detection + NLLB translation)
        try:
            results = batch_translate_to_turkish(names)
        except Exception as e:
            logger.error(f"Batch translation failed at offset {offset}: {e}")
            stats['errors'] += len(rows)
            offset += batch_size
            continue

        # Prepare updates
        updates = []
        for i, (new_name_tr, new_lang) in enumerate(results):
            name = names[i]
            old_tr = old_name_trs[i]
            old_lang = old_langs[i]

            if not name.strip():
                stats['skipped_empty'] += 1
                continue

            # Track stats
            lang_distribution[new_lang] = lang_distribution.get(new_lang, 0) + 1
            if new_lang == 'tr':
                stats['detected_tr'] += 1
            elif new_lang == 'en':
                stats['detected_en'] += 1
            else:
                stats['detected_other'] += 1

            if old_lang != new_lang:
                stats['lang_changed'] += 1

            if old_tr != new_name_tr and new_name_tr:
                stats['translation_changed'] += 1

            if new_lang != 'tr':
                stats['translated'] += 1

            updates.append((new_name_tr, new_lang, ids[i]))
            stats['processed'] += 1

        # Write to DB
        if updates and not args.dry_run:
            try:
                with conn.cursor() as cur:
                    execute_batch(cur, """
                        UPDATE trademarks
                        SET name_tr = %s, detected_lang = %s
                        WHERE id = %s
                    """, updates, page_size=500)
                conn.commit()
            except Exception as e:
                logger.error(f"DB update failed at offset {offset}: {e}")
                conn.rollback()
                stats['errors'] += len(updates)

        offset += batch_size
        elapsed = time.time() - start_time
        rate = stats['processed'] / elapsed if elapsed > 0 else 0
        pct = min(100, (offset / (total + args.offset)) * 100)
        eta_sec = (total - stats['processed']) / rate if rate > 0 else 0
        eta_min = eta_sec / 60

        logger.info(
            f"Progress: {stats['processed']:,}/{total:,} ({pct:.1f}%) | "
            f"Rate: {rate:.0f}/s | ETA: {eta_min:.1f}m | "
            f"TR:{stats['detected_tr']:,} EN:{stats['detected_en']:,} "
            f"Other:{stats['detected_other']:,} | "
            f"Lang changed: {stats['lang_changed']:,} | "
            f"Trans changed: {stats['translation_changed']:,}"
        )

    # Final summary
    elapsed = time.time() - start_time
    logger.info("")
    logger.info("=" * 70)
    logger.info("REGENERATION COMPLETE" + (" (DRY RUN)" if args.dry_run else ""))
    logger.info("=" * 70)
    logger.info(f"  Total processed:      {stats['processed']:,}")
    logger.info(f"  Elapsed:              {elapsed/60:.1f} minutes")
    logger.info(f"  Detected Turkish:     {stats['detected_tr']:,}")
    logger.info(f"  Detected English:     {stats['detected_en']:,}")
    logger.info(f"  Detected other:       {stats['detected_other']:,}")
    logger.info(f"  Language changed:     {stats['lang_changed']:,} (old→new mismatch)")
    logger.info(f"  Translation changed:  {stats['translation_changed']:,}")
    logger.info(f"  Skipped empty:        {stats['skipped_empty']:,}")
    logger.info(f"  Errors:               {stats['errors']:,}")
    logger.info("")
    logger.info("  Language distribution (top 20):")
    for lang, count in sorted(lang_distribution.items(), key=lambda x: -x[1])[:20]:
        logger.info(f"    {lang:10s}: {count:>10,}")

    conn.close()


if __name__ == '__main__':
    main()
