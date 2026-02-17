"""Regenerate name_tr for NULL records using FastText + NLLB."""
import os
import sys
sys.path.insert(0, '/app')
import psycopg2
from psycopg2.extras import execute_batch
import time

sys.stdout.reconfigure(encoding='utf-8')

from utils.translation import batch_translate_to_turkish, detect_language_fasttext

# Warm up models
print("Loading models...", flush=True)
detect_language_fasttext("test")
print("FastText ready.", flush=True)

conn = psycopg2.connect(
    host='postgres', port=5432, dbname='trademark_db',
    user='turk_patent', password='Dogan.1996'
)
conn.autocommit = False
cur = conn.cursor()

cur.execute("""
    SELECT COUNT(*) FROM trademarks
    WHERE (name_tr IS NULL OR detected_lang IS NULL)
    AND name IS NOT NULL AND name != ''
""")
total = cur.fetchone()[0]
print(f"Total NULL records to process: {total:,}", flush=True)

if total == 0:
    print("Nothing to process.")
    sys.exit(0)

BATCH_SIZE = 500
offset = 0
processed = 0
start = time.time()

while True:
    cur.execute("""
        SELECT id, name FROM trademarks
        WHERE (name_tr IS NULL OR detected_lang IS NULL)
        AND name IS NOT NULL AND name != ''
        ORDER BY id LIMIT %s OFFSET %s
    """, (BATCH_SIZE, offset))
    rows = cur.fetchall()
    if not rows:
        break

    ids = [r[0] for r in rows]
    names = [r[1] for r in rows]

    try:
        results = batch_translate_to_turkish(names)
    except Exception as e:
        print(f"  ERROR in batch at offset {offset}: {e}", flush=True)
        offset += BATCH_SIZE
        continue

    updates = []
    for i, (name_tr, lang) in enumerate(results):
        if name_tr:
            updates.append((name_tr, lang, ids[i]))

    if updates:
        execute_batch(cur, """
            UPDATE trademarks SET name_tr = %s, detected_lang = %s WHERE id = %s
        """, updates, page_size=200)
        conn.commit()

    processed += len(rows)
    elapsed = time.time() - start
    rate = processed / elapsed if elapsed > 0 else 0
    eta = (total - processed) / rate / 60 if rate > 0 else 0
    print(f"  {processed:,}/{total:,} ({processed*100/total:.1f}%) | {rate:.0f}/s | ETA: {eta:.1f}m", flush=True)

    offset += BATCH_SIZE

elapsed = time.time() - start
print(f"\nDone! Processed {processed:,} records in {elapsed/60:.1f} minutes", flush=True)

# Verify
cur.execute("SELECT COUNT(*) FROM trademarks WHERE name_tr IS NULL AND name IS NOT NULL AND name != ''")
remaining = cur.fetchone()[0]
print(f"Remaining NULL name_tr: {remaining:,}", flush=True)

cur.execute("SELECT detected_lang, COUNT(*) FROM trademarks GROUP BY detected_lang ORDER BY count DESC LIMIT 10")
print("\nLanguage distribution:", flush=True)
for row in cur.fetchall():
    print(f"  {str(row[0]):10s}: {row[1]:>10,}", flush=True)

conn.close()
